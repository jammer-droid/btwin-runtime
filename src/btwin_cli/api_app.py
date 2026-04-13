"""HTTP API app factory — package canonical implementation."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from btwin_cli.api_entries import create_entries_router
from btwin_core.agent_runner import AgentRunner
from btwin_cli.api_indexer import create_indexer_router
from btwin_cli.api_orchestration import create_orchestration_router
from btwin_cli.api_providers import create_providers_router
from btwin_core.conductor import ConductorLoop
from btwin_core.runtime_adapters import OpenClawMemoryInterface, build_runtime_adapters
from btwin_core.workflow_engine import WorkflowEngine
from btwin_core.agent_registry import AgentRegistry
from btwin_core.audit import AuditLogger
from btwin_core.runtime_logging import RuntimeEventLogger
from btwin_core.runtime_ports import AuditEvent
from btwin_core.terminal_manager import TerminalManager
from btwin_cli.api_events import create_events_router
from btwin_cli.api_helpers import error_response, require_main_admin, trace_id as _helper_trace_id
from btwin_cli.api_runtime_logs import create_runtime_logs_router
from btwin_cli.api_sessions import create_sessions_router
from btwin_cli.api_settings import create_settings_router
from btwin_cli.api_sources import create_sources_router
from btwin_cli.api_terminals import create_terminal_router
from btwin_cli.api_threads import create_threads_router
from btwin_core.agent_store import AgentStore as _AgentStore
from btwin_core.btwin import BTwin
from btwin_core.config import BTwinConfig, load_config
from btwin_core.event_bus import EventBus
from btwin_core.indexer import CoreIndexer
from btwin_core.promotion_store import PromotionStore
from btwin_core.protocol_store import ProtocolStore
from btwin_core.resource_paths import resolve_bundled_providers_path, resolve_bundled_protocols_dir
from btwin_core.sources import SourceRegistry
from btwin_core.storage import Storage
from btwin_core.thread_store import ThreadStore


def create_app(
    data_dir: Path,
    *,
    config: BTwinConfig | None = None,
    runtime_mode: str = "attached",
    initial_agents: set[str] | None = None,
    extra_agents: set[str] | None = None,
    openclaw_config_path: str | None = None,
    openclaw_memory: OpenClawMemoryInterface | None = None,
    admin_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="B-TWIN Orchestration API", version="0.1")
    app_config = config if config is not None else BTwinConfig(data_dir=data_dir)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    storage = Storage(data_dir)
    source_registry = SourceRegistry(data_dir / "sources.yaml")
    promotion_store = PromotionStore(data_dir / "promotion_queue.yaml")
    audit_logger = AuditLogger(data_dir / "audit.log.jsonl")
    runtime_event_logger = RuntimeEventLogger(data_dir)

    if runtime_mode == "standalone" and initial_agents is None:
        initial_agents = {"main"}

    registry = AgentRegistry(
        config_path=Path(openclaw_config_path).expanduser() if openclaw_config_path else None,
        extra_agents=extra_agents,
        initial_agents=initial_agents,
    )
    runtime_adapters = build_runtime_adapters(
        mode=runtime_mode,
        data_dir=data_dir,
        audit_logger=audit_logger,
        openclaw_memory=openclaw_memory,
    )

    _indexer_cache: CoreIndexer | None = None

    def _indexer() -> CoreIndexer:
        nonlocal _indexer_cache
        if _indexer_cache is None:
            _indexer_cache = CoreIndexer(data_dir=data_dir)
        return _indexer_cache

    event_bus = EventBus()
    workflow_engine = WorkflowEngine(storage, event_bus=event_bus)

    _btwin_cache: BTwin | None = None

    def _btwin() -> BTwin:
        nonlocal _btwin_cache
        if _btwin_cache is None:
            _btwin_cache = BTwin(app_config, indexer=_indexer())
        return _btwin_cache

    from fastapi import APIRouter as _APIRouter

    def _foundation_router(scope: str) -> _APIRouter:
        router = _APIRouter(prefix=f"/api/{scope}", tags=[f"foundation:{scope}"])

        @router.get("/health")
        def foundation_health():
            return {"ok": True, "scope": scope, "status": "available"}

        return router

    for scope in ("entries", "sources"):
        app.include_router(_foundation_router(scope))

    def _error(status_code: int, error_code: str, message: str, details: dict[str, object] | None = None) -> JSONResponse:
        return error_response(status_code, error_code, message, details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(_request: Request, exc: RequestValidationError):
        return _error(
            422,
            "INVALID_SCHEMA",
            "request validation failed",
            {"issues": exc.errors()},
        )

    agent_store = _AgentStore(storage.data_dir)
    if agent_store.get_agent("_conductor") is None:
        agent_store.register(
            name="_conductor",
            model="claude-haiku-4-5",
            alias="Conductor",
            capabilities=["conductor"],
            bypass_permissions=True,
        )

    conductor_loop = ConductorLoop()
    terminal_manager = TerminalManager()

    _original_startup = getattr(app, "_startup_reconcile_installed", False)

    if not _original_startup:
        from contextlib import asynccontextmanager
        import json as _json
        import logging as _logging

        _original_lifespan = app.router.lifespan_context

        async def _start_conductor_terminal() -> None:
            log = _logging.getLogger(__name__)
            for session in terminal_manager.list_sessions():
                if session.agent_name == "_conductor" and session.status == "running":
                    return

            agent = _AgentStore(storage.data_dir).get_agent("_conductor")
            if agent is None:
                return

            model_id = agent.get("model", "")

            providers_path = storage.data_dir / "providers.json"
            if not providers_path.exists():
                bundled = resolve_bundled_providers_path()
                if bundled is not None:
                    providers_path = bundled

            command = None
            args: list[str] = []
            if providers_path.exists():
                config_payload = _json.loads(providers_path.read_text(encoding="utf-8"))
                for provider in config_payload.get("providers", []):
                    for model in provider.get("models", []):
                        if model["id"] == model_id:
                            command = provider["cli"]
                            args = list(provider.get("default_args", []))
                            if provider["cli"] == "claude":
                                args.append("--dangerously-skip-permissions")
                            elif provider["cli"] == "codex":
                                args.append("--full-auto")
                            break
                    if command:
                        break

            if command:
                try:
                    await terminal_manager.create_session("_conductor", command, args)
                    log.info("Conductor terminal spawned (command=%s)", command)
                except Exception as exc:
                    log.warning("Failed to spawn conductor terminal: %s", exc)

        @asynccontextmanager
        async def _lifespan_with_reconcile(app_instance):
            log = _logging.getLogger(__name__)
            try:
                result = _indexer().reconcile()
                log.info("Startup reconcile: %s", result)
            except Exception:
                log.warning("Startup reconcile failed", exc_info=True)

            _agent_runner.start()
            event_bus.set_loop(asyncio.get_running_loop())
            try:
                await _start_conductor_terminal()
                async with _original_lifespan(app_instance) as state:
                    yield state
            finally:
                _agent_runner.stop()

        app.router.lifespan_context = _lifespan_with_reconcile
        app._startup_reconcile_installed = True  # type: ignore[attr-defined]

    def _audit(event_type: str, payload: dict[str, object]) -> None:
        runtime_adapters.audit.append(
            AuditEvent(
                event_type=event_type,
                actor=str(payload.get("actorAgent") or payload.get("actor") or "system"),
                trace_id=_helper_trace_id(),
                doc_version=int(payload.get("docVersion") or 0),
                checksum=str(payload.get("checksum") or "n/a"),
                payload=payload,
            )
        )

    def _require_main_admin(actor: str, x_admin_token: str | None) -> JSONResponse | None:
        return require_main_admin(actor, x_admin_token, admin_token, registry_agents=registry.agents)

    app.include_router(create_sources_router(source_registry, event_bus=event_bus))
    app.include_router(create_providers_router(runtime_event_logger=runtime_event_logger))
    app.include_router(create_settings_router(data_dir))
    app.include_router(create_runtime_logs_router(data_dir))
    app.include_router(create_entries_router(storage, source_registry, _btwin, admin_token, data_dir))
    app.include_router(
        create_indexer_router(
            indexer_factory=_indexer,
            storage=storage,
            admin_token=admin_token,
            audit_fn=_audit,
            require_main_admin_fn=_require_main_admin,
            runtime_mode=runtime_mode,
            runtime_adapters=runtime_adapters,
            audit_logger=audit_logger,
        )
    )

    app.include_router(
        create_orchestration_router(
            storage=storage,
            registry=registry,
            promotion_store=promotion_store,
            audit_logger=audit_logger,
            indexer_factory=_indexer,
            admin_token=admin_token,
            workflow_engine=workflow_engine,
            runtime_adapters=runtime_adapters,
            event_bus=event_bus,
            conductor_loop=conductor_loop,
            terminal_manager=terminal_manager,
        )
    )
    app.include_router(create_sessions_router(_btwin, event_bus=event_bus))
    app.include_router(create_events_router(event_bus))
    app.state.event_bus = event_bus
    app.state.runtime_event_logger = runtime_event_logger

    app.state.terminal_manager = terminal_manager
    app.include_router(create_terminal_router(terminal_manager, storage=storage))

    bundled_protocols = resolve_bundled_protocols_dir()
    thread_store = ThreadStore(data_dir / "threads")
    protocol_store = ProtocolStore(
        data_dir / "protocols",
        fallback_dir=bundled_protocols,
    )

    _agent_runner = AgentRunner(
        thread_store=thread_store,
        protocol_store=protocol_store,
        agent_store=agent_store,
        event_bus=event_bus,
        providers_path=data_dir / "providers.json",
        config=app_config,
        runtime_event_logger=runtime_event_logger,
    )

    app.include_router(
        create_threads_router(
            thread_store,
            protocol_store,
            event_bus,
            btwin_factory=_btwin,
            agent_store=agent_store,
            agent_runner=_agent_runner,
        )
    )

    import atexit as _atexit

    _atexit.register(terminal_manager.cleanup)

    return app


create_orchestration_app = create_app


def _resolve_runtime_openclaw_path(config: BTwinConfig) -> str | None:
    if config.runtime.mode == "standalone":
        return None

    env_path = os.environ.get("BTWIN_OPENCLAW_CONFIG_PATH")
    if env_path:
        return env_path

    if config.runtime.openclaw_config_path:
        return str(config.runtime.openclaw_config_path)

    return None


def create_default_app() -> FastAPI:
    """Create API app from default B-TWIN/OpenClaw runtime config."""
    config_path = Path.home() / ".btwin" / "config.yaml"
    if config_path.exists():
        config = load_config(config_path)
    else:
        config = BTwinConfig()

    extra_agents_env = os.environ.get("BTWIN_EXTRA_AGENTS", "")
    extra_agents = {agent.strip() for agent in extra_agents_env.split(",") if agent.strip()}

    return create_app(
        data_dir=config.data_dir,
        config=config,
        runtime_mode=config.runtime.mode,
        extra_agents=extra_agents,
        openclaw_config_path=_resolve_runtime_openclaw_path(config),
        admin_token=os.environ.get("BTWIN_ADMIN_TOKEN"),
    )


create_default_orchestration_app = create_default_app
