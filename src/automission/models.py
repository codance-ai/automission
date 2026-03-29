"""Shared data models for automission."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


# ── Acceptance Checklist ──


@dataclass
class Criterion:
    id: str
    group_id: str
    text: str
    required: bool = True


@dataclass
class AcceptanceGroup:
    id: str
    name: str
    depends_on: list[str] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)


# ── Agent Backend ──


@dataclass
class StableContext:
    """Written to AUTOMISSION.md once at mission creation."""

    goal: str
    skills: list[str] = field(default_factory=list)
    side_effect_policy: str = (
        "Do not execute side effects (git push, API calls) unless explicitly allowed."
    )
    rules: list[str] = field(default_factory=list)


@dataclass
class AttemptSpec:
    """Input to AgentBackend.run_attempt()."""

    attempt_id: str
    mission_id: str
    workdir: Path
    prompt: str
    timeout_s: int = 300
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AttemptResult:
    """Output from AgentBackend.run_attempt()."""

    status: Literal["completed", "failed", "timed_out", "crashed"]
    exit_code: int = 0
    transcript_path: Path | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    duration_s: float = 0.0
    changed_files: list[str] = field(default_factory=list)


@dataclass
class AttemptContract:
    """Focus for the next attempt, derived from last verification."""

    focus_groups: list[str] = field(default_factory=list)
    preserve_groups: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)


class MissionOutcome:
    """Terminal mission states with associated exit codes."""

    COMPLETED = "completed"  # exit 0
    FAILED = "failed"  # exit 1
    CANCELLED = "cancelled"  # exit 2
    RESOURCE_LIMIT = "resource_limit"  # exit 5

    EXIT_CODES = {
        "completed": 0,
        "failed": 1,
        "cancelled": 2,
        "resource_limit": 5,
    }


# ── Verification ──


@dataclass
class HarnessResult:
    """Deterministic test execution result from Harness."""

    passed: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    json_output: dict[str, Any] | None = None


@dataclass
class CriticResult:
    """LLM analysis of test results + code changes."""

    summary: str  # one-line summary for attempt history display
    root_cause: str = ""
    next_actions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    group_statuses: dict[str, bool] = field(default_factory=dict)


@dataclass
class VerificationResult:
    """Combined harness + critic result."""

    harness: HarnessResult
    critic: CriticResult

    @property
    def gate_passed(self) -> bool:
        return self.harness.passed

    @property
    def mission_passed(self) -> bool:
        """True when gate passes AND all groups are marked complete by Critic.

        Returns False when group_statuses is empty (e.g. Critic LLM failure),
        even if the gate passed. This is intentional: a transient Critic failure
        causes a retry rather than incorrectly completing the mission.
        """
        return (
            self.harness.passed
            and bool(self.critic.group_statuses)
            and all(self.critic.group_statuses.values())
        )

    @property
    def group_statuses(self) -> dict[str, bool]:
        return self.critic.group_statuses

    def to_json(self) -> str:
        """Serialize to JSON for ledger storage."""
        return json.dumps(
            {
                "harness": {
                    "passed": self.harness.passed,
                    "exit_code": self.harness.exit_code,
                    "stdout": self.harness.stdout,
                    "stderr": self.harness.stderr,
                    "json_output": self.harness.json_output,
                },
                "critic": {
                    "summary": self.critic.summary,
                    "root_cause": self.critic.root_cause,
                    "next_actions": self.critic.next_actions,
                    "blockers": self.critic.blockers,
                    "group_statuses": self.critic.group_statuses,
                },
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> VerificationResult:
        """Deserialize from JSON."""
        d = json.loads(raw)
        h = d["harness"]
        c = d["critic"]
        return cls(
            harness=HarnessResult(
                passed=h["passed"],
                exit_code=h["exit_code"],
                stdout=h.get("stdout", ""),
                stderr=h.get("stderr", ""),
                json_output=h.get("json_output"),
            ),
            critic=CriticResult(
                summary=c["summary"],
                root_cause=c.get("root_cause", ""),
                next_actions=c.get("next_actions", []),
                blockers=c.get("blockers", []),
                group_statuses=c.get("group_statuses", {}),
            ),
        )


# ── Skill Vendoring ──


@dataclass
class TaskClaim:
    """Lease-based claim on an AcceptanceGroup."""

    id: str
    mission_id: str
    agent_id: str
    group_id: str
    status: str = "active"  # active, completed, failed, expired
    claim_contract: str = ""  # JSON attempt contract
    heartbeat_at: str = ""
    expires_at: str = ""
    created_at: str = ""


@dataclass
class MergeResult:
    """Result of atomic merge attempt."""

    success: bool
    commit_hash: str = ""
    rejected_reason: str = ""


@dataclass
class SkillManifestEntry:
    name: str
    source: str
    hash: str


@dataclass
class SkillManifest:
    skills: list[SkillManifestEntry] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "skills": [
                    {"name": s.name, "source": s.source, "hash": s.hash}
                    for s in self.skills
                ]
            },
            indent=2,
        )


# ── Planner ──


@dataclass
class PlanCriterion:
    text: str
    verification_hint: str


@dataclass
class PlanGroup:
    id: str
    name: str
    depends_on: list[str] = field(default_factory=list)
    criteria: list[PlanCriterion] = field(default_factory=list)


@dataclass
class VerificationSurface:
    """Metadata describing how to verify the mission (runner, targets, options)."""

    runner: str  # e.g., "pytest", "jest", "bash"
    targets: list[str] = field(default_factory=list)  # e.g., ["tests/"]
    options: str = ""  # e.g., "-v --tb=short"


@dataclass
class PlanDraft:
    mission_summary: str
    constraints: list[str]
    groups: list[PlanGroup]
    verification_surface: VerificationSurface
    assumptions: list[str] = field(default_factory=list)
