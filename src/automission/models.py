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
    """Auto-derived focus for an attempt from last VerifierResult."""

    scope: str
    done_criteria: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)


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


# ── Verifier ──


@dataclass
class CriterionResult:
    criterion: str
    passed: bool
    detail: str = ""


@dataclass
class VerifierResult:
    contract_passed: bool
    mission_passed: bool
    gate_source: Literal["script", "llm"]
    score: float | None = None
    scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    passed_criteria: list[CriterionResult] = field(default_factory=list)
    failed_criteria: list[CriterionResult] = field(default_factory=list)
    group_statuses: dict[str, bool] = field(default_factory=dict)
    suggestion: str = ""
    reason: str = ""

    def to_json(self) -> str:
        """Serialize to JSON for ledger storage."""
        return json.dumps(
            {
                "contract_passed": self.contract_passed,
                "mission_passed": self.mission_passed,
                "gate_source": self.gate_source,
                "score": self.score,
                "scores": self.scores,
                "metrics": self.metrics,
                "passed_criteria": [
                    {"criterion": c.criterion, "passed": c.passed, "detail": c.detail}
                    for c in self.passed_criteria
                ],
                "failed_criteria": [
                    {"criterion": c.criterion, "passed": c.passed, "detail": c.detail}
                    for c in self.failed_criteria
                ],
                "group_statuses": self.group_statuses,
                "suggestion": self.suggestion,
                "reason": self.reason,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> VerifierResult:
        """Deserialize from JSON."""
        d = json.loads(raw)
        return cls(
            contract_passed=d["contract_passed"],
            mission_passed=d["mission_passed"],
            gate_source=d["gate_source"],
            score=d.get("score"),
            scores=d.get("scores", {}),
            metrics=d.get("metrics", {}),
            passed_criteria=[
                CriterionResult(**c) for c in d.get("passed_criteria", [])
            ],
            failed_criteria=[
                CriterionResult(**c) for c in d.get("failed_criteria", [])
            ],
            group_statuses=d.get("group_statuses", {}),
            suggestion=d.get("suggestion", ""),
            reason=d.get("reason", ""),
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
class PlanDraft:
    mission_summary: str
    constraints: list[str]
    groups: list[PlanGroup]
    verify_command: str
    assumptions: list[str] = field(default_factory=list)
