"""Protocol validation utilities for section detection and phase gate checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from btwin_core.protocol_store import ProtocolSection


SECTION_HEADER_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass
class ContributionValidation:
    valid: bool
    missing: list[str]


@dataclass
class PhaseValidation:
    passed: bool
    missing: list[dict[str, object]] = field(default_factory=list)


class ProtocolValidator:
    @staticmethod
    def detect_sections(content: str) -> set[str]:
        """Extract section names from markdown ## headers."""
        return {match.group(1).strip().lower() for match in SECTION_HEADER_RE.finditer(content)}

    @staticmethod
    def validate_contribution(
        content: str, required_sections: list[str]
    ) -> ContributionValidation:
        """Check if a contribution contains all required sections."""
        found = ProtocolValidator.detect_sections(content)
        missing = [section for section in required_sections if section.lower() not in found]
        return ContributionValidation(valid=len(missing) == 0, missing=missing)

    @staticmethod
    def validate_phase(
        phase_participants: list[str],
        template_sections: list[ProtocolSection],
        contributions: list[dict],
    ) -> PhaseValidation:
        """Validate that all phase participants submitted required sections."""
        required = [section.section for section in template_sections if section.required]
        if not required:
            return PhaseValidation(passed=True)

        agent_content: dict[str, str] = {}
        for contribution in contributions:
            agent_content[contribution["agent"]] = contribution.get("_content", "")

        missing_list = []
        for agent in phase_participants:
            content = agent_content.get(agent, "")
            result = ProtocolValidator.validate_contribution(content, required)
            if not result.valid:
                missing_list.append(
                    {
                        "agent": agent,
                        "missing_sections": result.missing,
                    }
                )

        return PhaseValidation(
            passed=len(missing_list) == 0,
            missing=missing_list,
        )
