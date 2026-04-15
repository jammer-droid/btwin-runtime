"""Provider abstraction layer for CLI-based agent runners."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Any as ResolvedLaunchAuth


def _extract_session_id(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    thread = payload.get("thread")
    if isinstance(thread, dict):
        thread_id = thread.get("id")
        if isinstance(thread_id, str) and thread_id.strip():
            return thread_id
    return None


def _extract_text_delta_from_message(payload: dict[str, Any]) -> str:
    message = payload.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", [])
    if not isinstance(content, list):
        return ""
    delta = ""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                delta += text
    return delta


@dataclass
class StreamEvent:
    """A single streaming event emitted by a CLI provider."""

    event_type: str
    text_delta: str = ""
    is_final: bool = False
    final_text: str = ""
    session_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRuntimeProfile:
    canonical_name: str
    aliases: tuple[str, ...] = ()
    default_transport_mode: str = "resume_invocation_transport"
    supports_live_transport: bool = False
    fallback_mode: str | None = None
    live_adapter_key: str | None = None
    default_auth_mode: str = "cli_environment"
    transport_capability: str = "resume_invocation"
    continuity_mode: str = "resume_invocation"


class CLIProvider(ABC):
    """Abstract base class for CLI-based agent providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier string."""

    @abstractmethod
    def build_command(self, session_id: str | None, bypass_permissions: bool) -> list[str]:
        """Build the subprocess command list for a given invocation."""

    @abstractmethod
    def parse_stream_line(self, line: str) -> StreamEvent | None:
        """Parse a single line of streaming output into a StreamEvent.

        Returns None if the line is not recognized or should be skipped.
        """

    @abstractmethod
    def parse_final_response(self, output: str) -> str:
        """Parse full stdout output to extract the final response text.

        Maintains backward compatibility with non-streaming (batch JSON) output.
        """

    @abstractmethod
    def parse_session_id_from_output(self, output: str) -> str | None:
        """Parse session/thread ID from full stdout output.

        Maintains backward compatibility with non-streaming (batch JSON) output.
        """

    @abstractmethod
    def env_overrides(self, launch_auth: ResolvedLaunchAuth | None = None) -> dict[str, str]:
        """Return environment variable overrides for subprocess execution."""


class ClaudeCodeProvider(CLIProvider):
    """Provider for the Claude Code CLI (`claude`)."""

    @property
    def name(self) -> str:
        return "claude-code"

    def build_command(self, session_id: str | None, bypass_permissions: bool) -> list[str]:
        cmd = [
            "claude",
            "--print",
            "-",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if session_id:
            cmd += ["--resume", session_id]
        if bypass_permissions:
            cmd.append("--dangerously-skip-permissions")
        return cmd

    def parse_stream_line(self, line: str) -> StreamEvent | None:
        """Parse a single stream-json line from Claude Code.

        Event types:
        - system/init: carries session_id
        - assistant: carries text_delta
        - result: marks final output
        """
        line = line.strip()
        if not line:
            return None
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        event_type = data.get("type", "")

        if event_type in ("system", "init"):
            sid = _extract_session_id(data, "session_id", "thread_id")
            return StreamEvent(event_type="session_started", session_id=sid, raw=data)

        if event_type == "assistant":
            delta = _extract_text_delta_from_message(data)
            return StreamEvent(event_type="text_delta", text_delta=delta, raw=data)

        if event_type == "result":
            final_text = data.get("result", "")
            if not isinstance(final_text, str):
                final_text = ""
            return StreamEvent(
                event_type="turn_complete",
                is_final=True,
                final_text=final_text,
                session_id=_extract_session_id(data, "session_id", "thread_id"),
                raw=data,
            )

        return StreamEvent(event_type=event_type, raw=data)

    def parse_final_response(self, output: str) -> str:
        """Backward-compatible parser for batch JSON output."""
        try:
            data = json.loads(output)
            return data.get("result", data.get("content", ""))
        except (json.JSONDecodeError, AttributeError):
            return output.strip()

    def parse_session_id_from_output(self, output: str) -> str | None:
        """Backward-compatible session ID parser for batch JSON output."""
        try:
            return json.loads(output).get("session_id")
        except (json.JSONDecodeError, AttributeError):
            return None

    def env_overrides(self, launch_auth: ResolvedLaunchAuth | None = None) -> dict[str, str]:
        """Strip Claude Code auto-exec vars and disable terminal colors."""
        overrides: dict[str, str] = {}
        for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION"):
            if key in os.environ:
                overrides[key] = ""
        overrides["NO_COLOR"] = "1"
        overrides["FORCE_COLOR"] = "0"
        if launch_auth is not None:
            overrides.update(launch_auth.env)
        return overrides


class CodexProvider(CLIProvider):
    """Provider for the Codex CLI (`codex`)."""

    _HOOKS_FEATURE_FLAG = ("--enable", "codex_hooks")

    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, session_id: str | None, bypass_permissions: bool) -> list[str]:
        if session_id:
            cmd = ["codex", "exec", "resume", session_id, *self._HOOKS_FEATURE_FLAG, "--json"]
        else:
            cmd = ["codex", "exec", *self._HOOKS_FEATURE_FLAG, "--json"]
        cmd.append("--skip-git-repo-check")
        if bypass_permissions:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        return cmd

    def parse_stream_line(self, line: str) -> StreamEvent | None:
        """Parse a single NDJSON line from Codex.

        Event types:
        - thread.started: carries thread_id as session_id
        - item.completed with agent_message type: text delta / final
        """
        line = line.strip()
        if not line:
            return None
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        event_type = data.get("type", "")

        if event_type == "thread.started":
            return StreamEvent(
                event_type="session_started",
                session_id=_extract_session_id(data, "thread_id", "session_id"),
                raw=data,
            )

        if event_type == "item.completed":
            item = data.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                text = item["text"]
                session_id = _extract_session_id(data, "thread_id", "session_id")
                return StreamEvent(
                    event_type="turn_complete",
                    text_delta=text,
                    is_final=True,
                    final_text=text,
                    session_id=session_id,
                    raw=data,
                )
            return StreamEvent(event_type="item.completed", raw=data)

        return StreamEvent(event_type=event_type, raw=data)

    def parse_final_response(self, output: str) -> str:
        """Backward-compatible parser: extract last agent_message from NDJSON output."""
        last_message = ""
        for line in output.strip().splitlines():
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if data.get("type") == "item.completed":
                item = data.get("item", {})
                if item.get("type") == "agent_message" and item.get("text"):
                    last_message = item["text"]
        return last_message

    def parse_session_id_from_output(self, output: str) -> str | None:
        """Backward-compatible session ID parser from NDJSON output."""
        for line in output.strip().splitlines():
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if data.get("type") == "thread.started" and data.get("thread_id"):
                return data["thread_id"]
        return None

    def env_overrides(self, launch_auth: ResolvedLaunchAuth | None = None) -> dict[str, str]:
        return dict(launch_auth.env) if launch_auth is not None else {}


_PROVIDER_REGISTRY: dict[str, CLIProvider] = {
    "claude-code": ClaudeCodeProvider(),
    "codex": CodexProvider(),
}

_PROVIDER_RUNTIME_PROFILES: dict[str, ProviderRuntimeProfile] = {
    "claude-code": ProviderRuntimeProfile(
        canonical_name="claude-code",
        aliases=("claude", "anthropic"),
        default_transport_mode="live_process_transport",
        supports_live_transport=True,
        live_adapter_key="claude",
        transport_capability="live_persistent",
        continuity_mode="same_process_stdin_stdout",
    ),
    "codex": ProviderRuntimeProfile(
        canonical_name="codex",
        aliases=("openai",),
        default_transport_mode="live_process_transport",
        supports_live_transport=True,
        fallback_mode="resume_invocation_transport",
        live_adapter_key="codex-app-server",
        transport_capability="live_persistent",
        continuity_mode="thread_rpc_session",
    ),
}
_PROVIDER_RUNTIME_ALIASES: dict[str, str] = {
    alias: profile.canonical_name
    for profile in _PROVIDER_RUNTIME_PROFILES.values()
    for alias in profile.aliases
}

_PROVIDER_STATUS_PUBLIC_IDS: dict[str, str] = {
    "claude-code": "claude",
    "codex": "codex",
}

_PROVIDER_STATUS_SUPPORTED_TRANSPORT_MODES: dict[str, tuple[str, ...]] = {
    "claude": ("live_process_transport", "resume_invocation_transport"),
    "codex": ("live_process_transport", "resume_invocation_transport"),
}


def get_provider(name: str) -> CLIProvider | None:
    """Return a provider instance by name, or None if unknown."""
    return _PROVIDER_REGISTRY.get(name)


def get_provider_runtime_profile(name: str) -> ProviderRuntimeProfile | None:
    """Return runtime transport metadata for a provider or known alias."""
    canonical_name = _PROVIDER_RUNTIME_ALIASES.get(name, name)
    profile = _PROVIDER_RUNTIME_PROFILES.get(canonical_name)
    if profile is not None:
        return profile
    provider = get_provider(name)
    if provider is None:
        return None
    return ProviderRuntimeProfile(canonical_name=provider.name)


def get_provider_status_public_id(name: str) -> str:
    """Return the dashboard-facing provider id for a provider name or alias."""
    profile = get_provider_runtime_profile(name)
    canonical_name = profile.canonical_name if profile is not None else name
    return _PROVIDER_STATUS_PUBLIC_IDS.get(canonical_name, canonical_name)


def get_provider_status_supported_transport_modes(name: str) -> tuple[str, ...]:
    """Return stable transport modes advertised to the dashboard."""
    public_id = get_provider_status_public_id(name)
    supported_modes = _PROVIDER_STATUS_SUPPORTED_TRANSPORT_MODES.get(public_id)
    if supported_modes is not None:
        return supported_modes

    profile = get_provider_runtime_profile(name)
    if profile is None:
        return ("resume_invocation_transport",)

    modes = [profile.default_transport_mode]
    if profile.fallback_mode:
        modes.append(profile.fallback_mode)
    return tuple(dict.fromkeys(modes))
