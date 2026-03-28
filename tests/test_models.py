"""Tests for data models."""

from automission.models import (
    AcceptanceGroup,
    AttemptResult,
    Criterion,
    CriterionResult,
    PlanCriterion,
    PlanDraft,
    PlanGroup,
    SkillManifest,
    SkillManifestEntry,
    StableContext,
    VerifierResult,
)


def test_verifier_result_json_roundtrip():
    original = VerifierResult(
        contract_passed=False,
        mission_passed=False,
        gate_source="script",
        score=0.6,
        scores={"completeness": 0.8},
        metrics={"test_pass_rate": "6/10"},
        passed_criteria=[
            CriterionResult(criterion="API exists", passed=True, detail="ok")
        ],
        failed_criteria=[
            CriterionResult(criterion="validation", passed=False, detail="missing")
        ],
        group_statuses={"auth": True, "api": False},
        suggestion="Add validation",
        reason="Mostly working",
    )
    restored = VerifierResult.from_json(original.to_json())
    assert restored.contract_passed == original.contract_passed
    assert restored.mission_passed == original.mission_passed
    assert restored.gate_source == original.gate_source
    assert restored.score == original.score
    assert restored.scores == original.scores
    assert len(restored.passed_criteria) == 1
    assert restored.passed_criteria[0].criterion == "API exists"
    assert len(restored.failed_criteria) == 1
    assert restored.suggestion == "Add validation"
    assert restored.group_statuses == {"auth": True, "api": False}


def test_acceptance_group_defaults():
    group = AcceptanceGroup(id="g1", name="test")
    assert group.depends_on == []
    assert group.criteria == []


def test_criterion_defaults():
    c = Criterion(id="c1", group_id="g1", text="must work")
    assert c.required is True


def test_attempt_result_defaults():
    r = AttemptResult(status="completed")
    assert r.exit_code == 0
    assert r.cost_usd == 0.0
    assert r.changed_files == []


def test_stable_context_defaults():
    ctx = StableContext(goal="Build something")
    assert "side effect" in ctx.side_effect_policy.lower()
    assert ctx.rules == []
    assert ctx.skills == []


def test_skill_manifest_json():
    m = SkillManifest(
        skills=[
            SkillManifestEntry(name="test", source="local:test.md", hash="sha256:abc")
        ]
    )
    raw = m.to_json()
    assert "test" in raw
    assert "sha256:abc" in raw


def test_attempt_contract_defaults():
    from automission.models import AttemptContract

    c = AttemptContract(scope="Fix divide function", done_criteria=["divide works"])
    assert c.scope == "Fix divide function"
    assert c.done_criteria == ["divide works"]
    assert c.non_goals == []


def test_attempt_contract_full():
    from automission.models import AttemptContract

    c = AttemptContract(
        scope="Fix divide function",
        done_criteria=["divide(6,3) returns 2", "divide(0,5) returns 0"],
        non_goals=["Do not modify add, subtract, multiply"],
    )
    assert len(c.done_criteria) == 2
    assert len(c.non_goals) == 1


def test_mission_outcome_values():
    from automission.models import MissionOutcome

    assert MissionOutcome.COMPLETED == "completed"
    assert MissionOutcome.FAILED == "failed"
    assert MissionOutcome.CANCELLED == "cancelled"
    assert MissionOutcome.RESOURCE_LIMIT == "resource_limit"


def test_mission_outcome_exit_codes():
    from automission.models import MissionOutcome

    assert MissionOutcome.EXIT_CODES["completed"] == 0
    assert MissionOutcome.EXIT_CODES["failed"] == 1
    assert MissionOutcome.EXIT_CODES["cancelled"] == 2
    assert MissionOutcome.EXIT_CODES["resource_limit"] == 5


def test_task_claim_defaults():
    from automission.models import TaskClaim

    claim = TaskClaim(
        id="c1",
        mission_id="m1",
        agent_id="agent-1",
        group_id="g1",
    )
    assert claim.status == "active"
    assert claim.claim_contract == ""
    assert claim.heartbeat_at == ""
    assert claim.expires_at == ""


def test_merge_result_defaults():
    from automission.models import MergeResult

    r = MergeResult(success=True, commit_hash="abc123")
    assert r.success is True
    assert r.rejected_reason == ""


def test_merge_result_rejected():
    from automission.models import MergeResult

    r = MergeResult(success=False, rejected_reason="regression failed")
    assert r.success is False
    assert r.rejected_reason == "regression failed"


class TestPlanDraft:
    def test_plan_criterion_fields(self):
        c = PlanCriterion(text="API returns 200", verification_hint="curl /api")
        assert c.text == "API returns 200"
        assert c.verification_hint == "curl /api"

    def test_plan_group_fields(self):
        g = PlanGroup(
            id="auth_schema",
            name="Auth Schema",
            depends_on=[],
            criteria=[
                PlanCriterion(text="Users table exists", verification_hint="check DB")
            ],
        )
        assert g.id == "auth_schema"
        assert g.name == "Auth Schema"
        assert len(g.criteria) == 1

    def test_plan_draft_fields(self):
        draft = PlanDraft(
            mission_summary="Build a TODO API",
            constraints=["JSON responses"],
            groups=[],
            verify_command="pytest tests/ -v",
            assumptions=["Python"],
        )
        assert draft.verify_command == "pytest tests/ -v"
        assert draft.assumptions == ["Python"]

    def test_plan_group_default_depends_on(self):
        g = PlanGroup(id="x", name="X", criteria=[])
        assert g.depends_on == []
