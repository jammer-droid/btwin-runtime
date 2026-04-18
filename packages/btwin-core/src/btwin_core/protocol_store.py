"""Protocol store — loads and manages collaboration protocol definitions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)
SUPPORTED_PROTOCOL_GUARDS = {
    "contribution_required",
    "phase_actor_eligibility",
    "direct_target_eligibility",
    "transition_precondition",
}


def _build_protocol_yaml_loader() -> type[yaml.SafeLoader]:
    class ProtocolYamlLoader(yaml.SafeLoader):
        pass

    ProtocolYamlLoader.yaml_implicit_resolvers = {
        key: list(resolvers)
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    for key in ("o", "O"):
        ProtocolYamlLoader.yaml_implicit_resolvers[key] = [
            resolver
            for resolver in ProtocolYamlLoader.yaml_implicit_resolvers.get(key, [])
            if resolver[0] != "tag:yaml.org,2002:bool"
        ]
    return ProtocolYamlLoader


ProtocolYamlLoader = _build_protocol_yaml_loader()


def load_protocol_yaml(path: Path) -> Any:
    """Load protocol YAML while preserving bare `on:` transition keys."""
    raw = path.read_text(encoding="utf-8")
    return yaml.load(raw, Loader=ProtocolYamlLoader)


class ProtocolSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str
    required: bool = False
    guidance: str = ""


class CycleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    until: Literal["decide"] = "decide"


class ProtocolProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    action: str
    guidance: str | None = None
    alias: str | None = None
    key: str | None = None

    def visual_key(self) -> str:
        return self.key or self.action

    def visual_label(self) -> str:
        return self.alias or self.action


class ProtocolInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["passive", "chat", "orchestrated_chat"] = "passive"
    allow_user_chat: bool = False
    default_actor: str | None = None


class ProtocolGuardSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    guards: list[str] = []

    @model_validator(mode="after")
    def validate_guard_vocabulary(self) -> "ProtocolGuardSet":
        for guard in self.guards:
            if guard not in SUPPORTED_PROTOCOL_GUARDS:
                raise ValueError(
                    f"Guard set '{self.name}' contains unsupported guard '{guard}'"
                )
        return self


class ProtocolAuthoringGateRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    target_phase: str
    alias: str | None = None
    key: str | None = None


class ProtocolAuthoringGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    authoring_only: Literal[True] = True
    routes: list[ProtocolAuthoringGateRoute] = []


class ProtocolOutcomePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    authoring_only: Literal[True] = True
    emitters: list[str] = []
    actions: list[str] = []
    outcomes: list[str] = []


class ProtocolPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    actions: list[Literal["contribute", "review", "discuss", "decide"]] = []
    template: list[ProtocolSection] | None = None
    procedure: list[ProtocolProcedureStep] | None = None
    guard_set: str | None = None
    gate: str | None = None
    outcome_policy: str | None = None
    mode: Literal["realtime_messages"] | None = None
    guidance: str | None = None
    decided_by: Literal["user", "consensus", "vote"] | None = None
    cycle: CycleConfig | None = None
    declared_guards: list[str] = Field(default_factory=list, exclude=True)
    outcome_emitters: list[str] = Field(default_factory=list, exclude=True)
    outcome_actions: list[str] = Field(default_factory=list, exclude=True)
    policy_outcomes: list[str] = Field(default_factory=list, exclude=True)

    @model_validator(mode="after")
    def normalize_actions(self) -> "ProtocolPhase":
        """Migrate legacy mode to actions and apply defaults."""
        if not self.actions:
            inferred = []
            if self.mode == "realtime_messages":
                inferred.append("discuss")
            if self.template:
                inferred.append("contribute")
            if self.decided_by:
                inferred.append("decide")
            if not inferred:
                inferred.append("discuss")
            self.actions = inferred
        if self.decided_by and "decide" not in self.actions:
            self.actions.append("decide")
        return self


class ProtocolTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_phase: str = Field(alias="from")
    to: str
    on: str | None = None
    alias: str | None = None
    key: str | None = None

    def visual_key(self) -> str:
        return self.key or self.on or self.to

    def visual_label(self) -> str:
        return self.alias or self.on or self.to


class Protocol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    phases: list[ProtocolPhase]
    interaction: ProtocolInteraction = Field(default_factory=ProtocolInteraction)
    roles: list[str] = []
    guard_sets: list[ProtocolGuardSet] = []
    gates: list[ProtocolAuthoringGate] = []
    outcome_policies: list[ProtocolOutcomePolicy] = []
    transitions: list[ProtocolTransition] = []
    outcomes: list[str] = []

    @model_validator(mode="after")
    def validate_authoring_references(self) -> "Protocol":
        phase_names = {phase.name for phase in self.phases}
        declared_outcomes = set(self.outcomes)
        guard_set_names = {guard_set.name for guard_set in self.guard_sets}
        if len(guard_set_names) != len(self.guard_sets):
            raise ValueError("duplicate guard_set name values are not allowed")
        gate_names = {gate.name for gate in self.gates}
        if len(gate_names) != len(self.gates):
            raise ValueError("duplicate gate name values are not allowed")
        outcome_policy_names = {
            outcome_policy.name for outcome_policy in self.outcome_policies
        }
        if len(outcome_policy_names) != len(self.outcome_policies):
            raise ValueError("duplicate outcome_policy name values are not allowed")
        canonical_transitions: dict[tuple[str, str], list[ProtocolTransition]] = {}
        for transition in self.transitions:
            if transition.on is None:
                continue
            canonical_transitions.setdefault(
                (transition.from_phase, transition.on), []
            ).append(transition)
        for gate in self.gates:
            route_outcomes: set[str] = set()
            for route in gate.routes:
                if route.outcome in route_outcomes:
                    raise ValueError(
                        f"gate '{gate.name}' defines duplicate routes for outcome '{route.outcome}'"
                    )
                route_outcomes.add(route.outcome)
                if route.target_phase not in phase_names:
                    raise ValueError(
                        f"gate '{gate.name}' references unknown target_phase '{route.target_phase}'"
                    )
                if route.outcome not in declared_outcomes:
                    raise ValueError(
                        f"gate '{gate.name}' uses undeclared outcome '{route.outcome}'"
                    )
        for outcome_policy in self.outcome_policies:
            for outcome in outcome_policy.outcomes:
                if outcome not in declared_outcomes:
                    raise ValueError(
                        f"outcome_policy '{outcome_policy.name}' uses undeclared outcome '{outcome}'"
                    )
        for phase in self.phases:
            if phase.guard_set is not None and phase.guard_set not in guard_set_names:
                raise ValueError(
                    f"Phase '{phase.name}' references unknown guard_set '{phase.guard_set}'"
                )
            if phase.gate is not None and phase.gate not in gate_names:
                raise ValueError(
                    f"Phase '{phase.name}' references unknown authoring gate '{phase.gate}'"
                )
            if (
                phase.outcome_policy is not None
                and phase.outcome_policy not in outcome_policy_names
            ):
                raise ValueError(
                    f"Phase '{phase.name}' references unknown outcome_policy '{phase.outcome_policy}'"
                )
            if phase.gate is None:
                continue
            gate = self.get_gate(phase.gate)
            if gate is None:
                continue
            for route in gate.routes:
                matching_transitions = canonical_transitions.get((phase.name, route.outcome))
                if not matching_transitions:
                    raise ValueError(
                        "gate "
                        f"'{gate.name}' route for phase '{phase.name}' and outcome "
                        f"'{route.outcome}' has no canonical transition"
                    )
                if len(matching_transitions) > 1:
                    raise ValueError(
                        "gate "
                        f"'{gate.name}' route for phase '{phase.name}' and outcome "
                        f"'{route.outcome}' has ambiguous canonical transitions"
                    )
                canonical_target = matching_transitions[0].to
                if route.target_phase != canonical_target:
                    raise ValueError(
                        "gate "
                        f"'{gate.name}' route for phase '{phase.name}' and outcome "
                        f"'{route.outcome}' contradicts canonical transition target "
                        f"'{canonical_target}'"
                    )
        return self

    def get_guard_set(self, name: str | None) -> ProtocolGuardSet | None:
        if name is None:
            return None
        for guard_set in self.guard_sets:
            if guard_set.name == name:
                return guard_set
        return None

    def get_gate(self, name: str | None) -> ProtocolAuthoringGate | None:
        if name is None:
            return None
        for gate in self.gates:
            if gate.name == name:
                return gate
        return None

    def get_outcome_policy(self, name: str | None) -> ProtocolOutcomePolicy | None:
        if name is None:
            return None
        for outcome_policy in self.outcome_policies:
            if outcome_policy.name == name:
                return outcome_policy
        return None


class ProtocolAuthoringDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    phases: list[ProtocolPhase]
    interaction: ProtocolInteraction = Field(default_factory=ProtocolInteraction)
    roles: list[str] = []
    guard_sets: list[ProtocolGuardSet] = []
    gates: list[ProtocolAuthoringGate] = []
    outcome_policies: list[ProtocolOutcomePolicy] = []
    transitions: list[ProtocolTransition] = []
    outcomes: list[str] = []


class ProtocolValidationLayerError(ValueError):
    """Human-readable protocol validation error with layer context."""

    def __init__(
        self,
        layer: Literal["schema", "semantic", "normalization"],
        detail: str,
    ) -> None:
        self.layer = layer
        self.detail = detail
        super().__init__(f"{layer} validation failed: {detail}")


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) or str(exc)


def _phase_map(phases: list[ProtocolPhase]) -> dict[str, ProtocolPhase]:
    return {phase.name: phase for phase in phases}


def _coerce_authoring_document(data: Any) -> ProtocolAuthoringDocument:
    authoring_payload = (
        data.model_dump(exclude_none=True, by_alias=True)
        if isinstance(data, (Protocol, ProtocolAuthoringDocument))
        else data
    )
    try:
        return ProtocolAuthoringDocument.model_validate(authoring_payload)
    except ValidationError as exc:
        raise ProtocolValidationLayerError("schema", _format_validation_error(exc)) from exc


def _validate_protocol_semantics(protocol: ProtocolAuthoringDocument) -> None:
    phase_names = {phase.name for phase in protocol.phases}
    declared_outcomes = set(protocol.outcomes)
    guard_set_names = {guard_set.name for guard_set in protocol.guard_sets}
    if len(guard_set_names) != len(protocol.guard_sets):
        raise ProtocolValidationLayerError(
            "semantic",
            "duplicate guard_set name values are not allowed",
        )
    gate_names = {gate.name for gate in protocol.gates}
    if len(gate_names) != len(protocol.gates):
        raise ProtocolValidationLayerError(
            "semantic",
            "duplicate gate name values are not allowed",
        )
    outcome_policy_names = {
        outcome_policy.name for outcome_policy in protocol.outcome_policies
    }
    if len(outcome_policy_names) != len(protocol.outcome_policies):
        raise ProtocolValidationLayerError(
            "semantic",
            "duplicate outcome_policy name values are not allowed",
        )

    explicit_transitions: dict[tuple[str, str], list[ProtocolTransition]] = {}
    for transition in protocol.transitions:
        if transition.on is None:
            continue
        explicit_transitions.setdefault((transition.from_phase, transition.on), []).append(
            transition
        )

    for gate in protocol.gates:
        route_outcomes: set[str] = set()
        for route in gate.routes:
            if route.outcome in route_outcomes:
                raise ProtocolValidationLayerError(
                    "semantic",
                    f"gate '{gate.name}' defines duplicate routes for outcome '{route.outcome}'",
                )
            route_outcomes.add(route.outcome)
            if route.target_phase not in phase_names:
                raise ProtocolValidationLayerError(
                    "semantic",
                    f"gate '{gate.name}' references unknown target_phase '{route.target_phase}'",
                )
            if declared_outcomes and route.outcome not in declared_outcomes:
                raise ProtocolValidationLayerError(
                    "semantic",
                    f"gate '{gate.name}' uses undeclared outcome '{route.outcome}'",
                )

    for outcome_policy in protocol.outcome_policies:
        for outcome in outcome_policy.outcomes:
            if declared_outcomes and outcome not in declared_outcomes:
                raise ProtocolValidationLayerError(
                    "semantic",
                    f"outcome_policy '{outcome_policy.name}' uses undeclared outcome '{outcome}'",
                )

    gate_map = {gate.name: gate for gate in protocol.gates}
    for phase in protocol.phases:
        if phase.guard_set is not None and phase.guard_set not in guard_set_names:
            raise ProtocolValidationLayerError(
                "semantic",
                f"Phase '{phase.name}' references unknown guard_set '{phase.guard_set}'",
            )
        if phase.gate is not None and phase.gate not in gate_names:
            raise ProtocolValidationLayerError(
                "semantic",
                f"Phase '{phase.name}' references unknown authoring gate '{phase.gate}'",
            )
        if (
            phase.outcome_policy is not None
            and phase.outcome_policy not in outcome_policy_names
        ):
            raise ProtocolValidationLayerError(
                "semantic",
                f"Phase '{phase.name}' references unknown outcome_policy '{phase.outcome_policy}'",
            )
        if phase.gate is None:
            continue
        gate = gate_map.get(phase.gate)
        if gate is None:
            continue
        for route in gate.routes:
            matching_transitions = explicit_transitions.get((phase.name, route.outcome), [])
            if len(matching_transitions) > 1:
                raise ProtocolValidationLayerError(
                    "semantic",
                    "gate "
                    f"'{gate.name}' route for phase '{phase.name}' and outcome "
                    f"'{route.outcome}' has ambiguous canonical transitions",
                )
            if not matching_transitions:
                continue
            canonical_target = matching_transitions[0].to
            if route.target_phase != canonical_target:
                raise ProtocolValidationLayerError(
                    "semantic",
                    "gate "
                    f"'{gate.name}' route for phase '{phase.name}' and outcome "
                    f"'{route.outcome}' contradicts canonical transition target "
                    f"'{canonical_target}'",
                )


def _append_unique(items: list[str], value: str | None) -> None:
    if value and value not in items:
        items.append(value)


def _compile_protocol(protocol: ProtocolAuthoringDocument) -> Protocol:
    guard_sets = {guard_set.name: guard_set for guard_set in protocol.guard_sets}
    outcome_policies = {
        outcome_policy.name: outcome_policy
        for outcome_policy in protocol.outcome_policies
    }

    compiled_phases: list[ProtocolPhase] = []
    for phase in protocol.phases:
        compiled_phase = phase.model_copy(deep=True)
        declared_guard_set = guard_sets.get(phase.guard_set or "")
        compiled_phase.declared_guards = (
            list(declared_guard_set.guards) if declared_guard_set is not None else []
        )
        declared_outcome_policy = outcome_policies.get(phase.outcome_policy or "")
        if declared_outcome_policy is None:
            compiled_phase.outcome_emitters = []
            compiled_phase.outcome_actions = []
            compiled_phase.policy_outcomes = []
        else:
            compiled_phase.outcome_emitters = list(declared_outcome_policy.emitters)
            compiled_phase.outcome_actions = list(declared_outcome_policy.actions)
            compiled_phase.policy_outcomes = list(declared_outcome_policy.outcomes)
        compiled_phases.append(compiled_phase)

    compiled_transitions = [transition.model_copy(deep=True) for transition in protocol.transitions]
    transition_indexes: dict[tuple[str, str], list[int]] = {}
    for index, transition in enumerate(compiled_transitions):
        if transition.on is None:
            continue
        transition_indexes.setdefault((transition.from_phase, transition.on), []).append(index)

    phase_lookup = _phase_map(protocol.phases)
    for phase_name, phase in phase_lookup.items():
        if phase.gate is None:
            continue
        gate = next((item for item in protocol.gates if item.name == phase.gate), None)
        if gate is None:
            continue
        for route in gate.routes:
            indexes = transition_indexes.get((phase_name, route.outcome), [])
            if indexes:
                index = indexes[0]
                transition = compiled_transitions[index]
                compiled_transitions[index] = transition.model_copy(
                    update={
                        "alias": transition.alias or route.alias,
                        "key": transition.key or route.key,
                    }
                )
                continue
            compiled_transitions.append(
                ProtocolTransition.model_validate(
                    {
                        "from": phase_name,
                        "to": route.target_phase,
                        "on": route.outcome,
                        "alias": route.alias,
                        "key": route.key,
                    }
                )
            )

    compiled_outcomes: list[str] = []
    for outcome in protocol.outcomes:
        _append_unique(compiled_outcomes, outcome)
    for transition in compiled_transitions:
        _append_unique(compiled_outcomes, transition.on)
    for outcome_policy in protocol.outcome_policies:
        for outcome in outcome_policy.outcomes:
            _append_unique(compiled_outcomes, outcome)

    compiled_payload = protocol.model_dump(exclude_none=True, by_alias=True)
    compiled_payload["phases"] = [
        phase.model_dump(exclude_none=True, by_alias=True) for phase in compiled_phases
    ]
    compiled_payload["transitions"] = [
        transition.model_dump(exclude_none=True, by_alias=True)
        for transition in compiled_transitions
    ]
    compiled_payload["outcomes"] = compiled_outcomes

    try:
        compiled_protocol = Protocol.model_validate(compiled_payload)
    except ValidationError as exc:
        raise ProtocolValidationLayerError(
            "normalization",
            _format_validation_error(exc),
        ) from exc
    compiled_phase_metadata = {
        phase.name: phase for phase in compiled_phases
    }
    for phase in compiled_protocol.phases:
        compiled_phase = compiled_phase_metadata.get(phase.name)
        if compiled_phase is None:
            continue
        phase.declared_guards = list(compiled_phase.declared_guards)
        phase.outcome_emitters = list(compiled_phase.outcome_emitters)
        phase.outcome_actions = list(compiled_phase.outcome_actions)
        phase.policy_outcomes = list(compiled_phase.policy_outcomes)
    return compiled_protocol


def compile_protocol_definition(data: Any) -> Protocol:
    authoring = _coerce_authoring_document(data)
    _validate_protocol_semantics(authoring)
    return _compile_protocol(authoring)


def ensure_protocol_compiled(protocol: Protocol) -> Protocol:
    return compile_protocol_definition(protocol)


def build_protocol_preview(
    data: Any,
    *,
    source: dict[str, object] | None = None,
) -> dict[str, object]:
    authoring = _coerce_authoring_document(data)
    _validate_protocol_semantics(authoring)
    compiled = _compile_protocol(authoring)
    payload: dict[str, object] = {
        "authoring": {
            "name": authoring.name,
            "phase_count": len(authoring.phases),
            "gate_count": len(authoring.gates),
            "outcome_policy_count": len(authoring.outcome_policies),
        },
        "compiled": compiled.model_dump(exclude_none=True, by_alias=True),
    }
    if source:
        payload["source"] = source
    return payload


class ProtocolStore:
    """Read-only store for protocol YAML definitions."""

    def __init__(self, protocols_dir: Path, fallback_dir: Path | None = None) -> None:
        self._dir = protocols_dir
        self._fallback_dir = fallback_dir

    def list_protocols(self) -> list[dict]:
        """Return summary of all valid protocols."""
        results = {}
        for base_dir in self._candidate_dirs():
            if not base_dir.exists():
                continue
            for path in sorted(base_dir.glob("*.yaml")):
                proto = self._load_file(path)
                if proto and proto.name not in results:
                    results[proto.name] = {
                        "name": proto.name,
                        "description": proto.description,
                    }
        return list(results.values())

    def get_protocol(self, name: str) -> Protocol | None:
        """Load full protocol definition by name."""
        for base_dir in self._candidate_dirs():
            path = base_dir / f"{name}.yaml"
            if path.exists():
                return self._load_file(path)
        return None

    def save_protocol(self, protocol: Protocol) -> Path:
        """Save protocol to project-local directory."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{protocol.name}.yaml"
        data = protocol.model_dump(exclude_none=True, by_alias=True)
        for key in ("gates", "outcome_policies"):
            if not data.get(key):
                data.pop(key, None)
        for phase in data.get("phases", []):
            phase.pop("mode", None)
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def delete_protocol(self, name: str) -> bool:
        """Delete project-local protocol. Returns False if not found."""
        path = self._dir / f"{name}.yaml"
        if path.exists():
            path.unlink()
            return True
        return False

    def _candidate_dirs(self) -> list[Path]:
        dirs = [self._dir]
        if self._fallback_dir is not None:
            dirs.append(self._fallback_dir)
        return dirs

    def _load_file(self, path: Path) -> Protocol | None:
        try:
            data = load_protocol_yaml(path)
            return compile_protocol_definition(data)
        except (OSError, yaml.YAMLError, ValidationError, ProtocolValidationLayerError) as exc:
            logger.warning("Failed to load protocol %s: %s", path, exc)
            return None
