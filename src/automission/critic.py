"""Critic: LLM-powered analysis of test results."""

from __future__ import annotations

import logging

from automission.models import AcceptanceGroup, CriticResult, HarnessResult
from automission.structured_output import CLIResponseError, StructuredOutputBackend

logger = logging.getLogger(__name__)

# ── JSON Schema for structured critic output ──

_CRITIC_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One-line summary of the attempt result for attempt history display.",
        },
        "root_cause": {
            "type": "string",
            "description": "Root cause analysis of test failures. Empty string if all tests pass.",
        },
        "next_actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Prioritized list of specific, actionable next steps for the agent.",
        },
        "blockers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Issues that prevent progress (spec ambiguity, missing deps, etc). Empty if none.",
        },
        "group_analysis": {
            "type": "array",
            "description": "Per acceptance group completion status. A group is complete when ALL its required criteria are satisfied.",
            "items": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string"},
                    "completed": {"type": "boolean"},
                },
                "required": ["group_id", "completed"],
            },
        },
    },
    "required": ["summary", "root_cause", "next_actions", "blockers", "group_analysis"],
}


class Critic:
    """LLM-powered analysis of test results and code changes."""

    def __init__(
        self,
        backend: StructuredOutputBackend,
        model: str = "claude-sonnet-4-6",
    ):
        self.backend = backend
        self.model = model

    def analyze(
        self,
        harness_result: HarnessResult,
        groups: list[AcceptanceGroup],
    ) -> CriticResult:
        """Analyze test results and determine group completion status."""
        criteria_text = "\n".join(
            f"- [{g.name}] {c.text}" for g in groups for c in g.criteria
        )
        group_list = ", ".join(g.id for g in groups)

        prompt = f"""You are a code verification critic. Analyze the test output and determine which acceptance groups are complete.

## Gate Result
Passed: {harness_result.passed}
Exit code: {harness_result.exit_code}

## Test Output (stdout)
{harness_result.stdout[:3000]}

## Test Errors (stderr)
{harness_result.stderr[:2000]}

## Acceptance Criteria
{criteria_text}

## Groups
{group_list}

Analyze the results:
1. For each acceptance group, determine if ALL its criteria are satisfied based on the test output.
2. Provide a one-line summary suitable for attempt history display.
3. If tests failed, identify the root cause and suggest specific next actions.
4. Report any blockers that prevent progress (e.g., ambiguous spec, missing dependencies)."""

        try:
            result = self.backend.query(
                prompt=prompt,
                model=self.model,
                json_schema=_CRITIC_JSON_SCHEMA,
            )
            # Convert array group_analysis to dict
            gs = result.get("group_analysis", [])
            if isinstance(gs, list):
                try:
                    group_analysis = {
                        item["group_id"]: item["completed"] for item in gs
                    }
                except (KeyError, TypeError) as e:
                    logger.warning("Malformed group_analysis from critic: %s", e)
                    return self._empty_result(str(e))
            else:
                group_analysis = gs if isinstance(gs, dict) else {}

            return CriticResult(
                summary=result.get("summary", ""),
                root_cause=result.get("root_cause", ""),
                next_actions=result.get("next_actions", []),
                blockers=result.get("blockers", []),
                group_analysis=group_analysis,
            )
        except CLIResponseError as e:
            logger.error("Critic CLI call failed: %s", e)
            return self._empty_result(str(e))

    @staticmethod
    def _empty_result(error: str) -> CriticResult:
        """Return safe default when critic fails. Does not fabricate group statuses."""
        return CriticResult(
            summary=f"Critic error: {error}",
            root_cause="",
            next_actions=["Retry verification."],
            blockers=[],
            group_analysis={},
        )
