"""Tests for data models."""

from automission.models import (
    AcceptanceGroup,
    AttemptResult,
    Criterion,
    CriticResult,
    HarnessResult,
    PlanCriterion,
    PlanDraft,
    PlanGroup,
    SkillManifest,
    SkillManifestEntry,
    StableContext,
    VerificationResult,
    VerificationSurface,
)


def test_verification_result_json_roundtrip():
    original = VerificationResult(
        harness=HarnessResult(
            passed=False,
            exit_code=1,
            stdout="6/10 tests passed",
            stderr="AssertionError",
            json_output={"score": 0.6, "metrics": {"test_pass_rate": "6/10"}},
        ),
        critic=CriticResult(
            summary="Mostly working",
            root_cause="Validation missing",
            next_actions=["Add validation"],
            blockers=[],
            group_analysis={"auth": True, "api": False},
        ),
    )
    restored = VerificationResult.from_json(original.to_json())
    assert restored.gate_passed == original.gate_passed
    assert restored.mission_passed == original.mission_passed
    assert restored.harness.passed is False
    assert restored.harness.exit_code == 1
    assert restored.harness.stdout == "6/10 tests passed"
    assert restored.critic.summary == "Mostly working"
    assert restored.critic.root_cause == "Validation missing"
    assert restored.critic.next_actions == ["Add validation"]
    assert restored.group_analysis == {"auth": True, "api": False}


class TestMissionPassed:
    """mission_passed depends ONLY on harness.passed — no Critic dependency."""

    def test_harness_passes_empty_group_analysis(self):
        """Harness passes + empty group_analysis → mission_passed = True."""
        vr = VerificationResult(
            harness=HarnessResult(passed=True, exit_code=0),
            critic=CriticResult(summary="ok", group_analysis={}),
        )
        assert vr.mission_passed is True

    def test_harness_passes_groups_incomplete(self):
        """Harness passes + group_analysis says groups incomplete → mission_passed = True."""
        vr = VerificationResult(
            harness=HarnessResult(passed=True, exit_code=0),
            critic=CriticResult(
                summary="partial",
                group_analysis={"auth": True, "api": False},
            ),
        )
        assert vr.mission_passed is True

    def test_harness_fails_groups_complete(self):
        """Harness fails + group_analysis says all complete → mission_passed = False."""
        vr = VerificationResult(
            harness=HarnessResult(passed=False, exit_code=1),
            critic=CriticResult(
                summary="looks done",
                group_analysis={"auth": True, "api": True},
            ),
        )
        assert vr.mission_passed is False


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

    c = AttemptContract(focus_groups=["divide_ops"])
    assert c.focus_groups == ["divide_ops"]
    assert c.preserve_groups == []
    assert c.evidence == []
    assert c.blockers == []
    assert c.next_actions == []


def test_attempt_contract_full():
    from automission.models import AttemptContract

    c = AttemptContract(
        focus_groups=["divide_ops"],
        preserve_groups=["add_ops", "subtract_ops"],
        evidence=["AssertionError: divide(6,3) expected 2"],
        blockers=[],
        next_actions=["Implement divide function"],
    )
    assert len(c.focus_groups) == 1
    assert len(c.preserve_groups) == 2
    assert len(c.next_actions) == 1


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
            verification_surface=VerificationSurface(
                runner="pytest", targets=["tests/"], options="-v"
            ),
            assumptions=["Python"],
        )
        assert draft.verification_surface.runner == "pytest"
        assert draft.verification_surface.targets == ["tests/"]
        assert draft.verification_surface.options == "-v"
        assert draft.assumptions == ["Python"]

    def test_plan_group_default_depends_on(self):
        g = PlanGroup(id="x", name="X", criteria=[])
        assert g.depends_on == []
