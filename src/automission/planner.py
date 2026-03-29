"""Planner: auto-generate acceptance checklist from a brief goal."""

from __future__ import annotations

import logging
import re

from automission.acceptance import _to_snake_case
from automission.models import PlanCriterion, PlanDraft, PlanGroup, VerificationSurface
from automission.structured_output import StructuredOutputBackend

logger = logging.getLogger(__name__)


class PlanValidationError(Exception):
    """Raised when PlanDraft fails DAG validation."""


def validate_dag(draft: PlanDraft) -> None:
    """Validate PlanDraft DAG. Raises PlanValidationError on failure."""
    if not draft.groups:
        raise PlanValidationError("Plan must have at least one group")

    ids = [g.id for g in draft.groups]
    if len(ids) != len(set(ids)):
        dupes = {x for x in ids if ids.count(x) > 1}
        raise PlanValidationError(f"Duplicate group IDs: {dupes}")

    group_ids = set(ids)

    for g in draft.groups:
        if not re.match(r"^[a-z][a-z0-9_]*$", g.id):
            raise PlanValidationError(f"Group '{g.id}' is not valid snake_case")
        expected_id = _to_snake_case(g.name)
        if g.id != expected_id:
            raise PlanValidationError(
                f"Group id '{g.id}' does not match name '{g.name}' "
                f"(expected '{expected_id}'): id/name mismatch"
            )
        if not g.criteria:
            raise PlanValidationError(f"Group '{g.id}' has no criteria")
        if g.id in g.depends_on:
            raise PlanValidationError(f"Group '{g.id}' has self-dependency")
        for dep in g.depends_on:
            if dep not in group_ids:
                raise PlanValidationError(
                    f"Group '{g.id}' depends on '{dep}' which does not exist (missing)"
                )

    in_degree = {g.id: len(g.depends_on) for g in draft.groups}
    queue = [gid for gid, deg in in_degree.items() if deg == 0]
    visited = 0
    adj: dict[str, list[str]] = {g.id: [] for g in draft.groups}
    for g in draft.groups:
        for dep in g.depends_on:
            adj[dep].append(g.id)
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if visited != len(draft.groups):
        raise PlanValidationError("Dependency graph contains a cycle")


def render_mission_md(draft: PlanDraft) -> str:
    """Render MISSION.md from PlanDraft."""
    lines = [f"# Mission\n\n{draft.mission_summary}\n"]
    if draft.constraints:
        lines.append("\n## Constraints\n")
        for c in draft.constraints:
            lines.append(f"- {c}")
    return "\n".join(lines) + "\n"


def render_acceptance_md(draft: PlanDraft) -> str:
    """Render ACCEPTANCE.md from PlanDraft. Round-trip compatible with parse_acceptance_md()."""
    lines = ["# Acceptance Criteria"]
    for group in draft.groups:
        lines.append("")
        lines.append(f"## {group.name}")
        if group.depends_on:
            lines.append("")
            lines.append(f"Depends on: {', '.join(group.depends_on)}")
        lines.append("")
        for criterion in group.criteria:
            lines.append(f"- {criterion.text}")
    return "\n".join(lines) + "\n"


# ── Tool schema ──

_PLAN_TOOL = {
    "name": "submit_plan",
    "description": "Submit the structured mission plan.",
    "input_schema": {
        "type": "object",
        "required": [
            "mission_summary",
            "constraints",
            "groups",
            "verification_surface",
        ],
        "properties": {
            "mission_summary": {
                "type": "string",
                "description": "Expanded goal: what to build + key constraints. NO implementation details.",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Non-functional constraints (performance, security, compatibility)",
            },
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "name", "depends_on", "criteria"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "snake_case ID, must equal snake_case(name)",
                        },
                        "name": {
                            "type": "string",
                            "description": "Human-readable name (becomes ## heading in ACCEPTANCE.md)",
                        },
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "criteria": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["text", "verification_hint"],
                                "properties": {
                                    "text": {"type": "string"},
                                    "verification_hint": {
                                        "type": "string",
                                        "description": "Black-box check description",
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "verification_surface": {
                "type": "object",
                "description": "How to verify the mission. Describes the test runner and targets.",
                "required": ["runner", "targets"],
                "properties": {
                    "runner": {
                        "type": "string",
                        "description": "Test runner command (e.g., 'pytest', 'jest', 'go test', 'bash')",
                    },
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Test targets (e.g., ['tests/'], ['test/'], ['./...'])",
                    },
                    "options": {
                        "type": "string",
                        "description": "Additional runner options (e.g., '-v --tb=short'). Empty string if none.",
                    },
                },
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Assumptions made that the user should review",
            },
        },
    },
}

_SYSTEM_PROMPT = """\
You are a mission planner for an autonomous coding agent system. Given a brief goal, \
you expand it into a structured acceptance checklist with dependency-aware groups.

Rules:
- Specify observable outcomes and hard constraints — NOT implementation details
- Do NOT invent framework choices, file names, class names, or architecture decisions \
unless the user explicitly stated them in the goal
- Criteria describe WHAT the system does, not HOW it's built
- verification_hint should describe black-box checks (HTTP responses, CLI output, file existence)
- 3-7 groups is typical; more than 10 is over-specification
- Each group should have 2-5 criteria
- group.id must be valid snake_case and must equal the snake_case form of group.name
- Dependencies form a DAG (no cycles). Groups with no dependencies start immediately.
- verification_surface describes how to run tests: runner (e.g., "pytest"), targets (e.g., ["tests/"]), and options (e.g., "-v --tb=short")

Call the submit_plan tool with your structured plan."""

_PLAN_JSON_SCHEMA = _PLAN_TOOL["input_schema"]


class Planner:
    """Generate structured mission plans from brief goals via structured output backend."""

    def __init__(
        self, backend: StructuredOutputBackend, model: str = "claude-sonnet-4-6"
    ):
        self.backend = backend
        self.model = model

    def plan(self, goal: str) -> PlanDraft:
        """Generate and validate a PlanDraft from a goal string."""
        raw = self._call_llm(goal)
        draft = self._parse_response(raw)
        try:
            validate_dag(draft)
        except PlanValidationError as e:
            logger.warning("Plan validation failed: %s — attempting repair", e)
            draft = self._repair(goal, str(e))
        return draft

    def _call_llm(self, goal: str) -> dict:
        """Single backend call with json-schema constraint."""
        prompt = f"{_SYSTEM_PROMPT}\n\nGoal: {goal}"
        return self.backend.query(
            prompt=prompt,
            model=self.model,
            json_schema=_PLAN_JSON_SCHEMA,
        )

    def _parse_response(self, raw: dict) -> PlanDraft:
        """Convert JSON dict to PlanDraft dataclass."""
        groups = []
        for g in raw.get("groups", []):
            criteria = [
                PlanCriterion(text=c["text"], verification_hint=c["verification_hint"])
                for c in g.get("criteria", [])
            ]
            groups.append(
                PlanGroup(
                    id=g["id"],
                    name=g["name"],
                    depends_on=g.get("depends_on", []),
                    criteria=criteria,
                )
            )
        vs_raw = raw.get("verification_surface", {})
        verification_surface = VerificationSurface(
            runner=vs_raw.get("runner", "echo"),
            targets=vs_raw.get("targets", ["'no tests configured'"]),
            options=vs_raw.get("options", ""),
        )
        return PlanDraft(
            mission_summary=raw.get("mission_summary", ""),
            constraints=raw.get("constraints", []),
            groups=groups,
            verification_surface=verification_surface,
            assumptions=raw.get("assumptions", []),
        )

    def _repair(self, goal: str, error: str) -> PlanDraft:
        """One retry with validation error feedback embedded in prompt."""
        prompt = (
            f"{_SYSTEM_PROMPT}\n\n"
            f"Goal: {goal}\n\n"
            f"Your previous plan had a validation error: {error}\n"
            "Please fix and resubmit."
        )
        raw = self.backend.query(
            prompt=prompt,
            model=self.model,
            json_schema=_PLAN_JSON_SCHEMA,
        )
        draft = self._parse_response(raw)
        validate_dag(draft)  # raises if still invalid
        return draft
