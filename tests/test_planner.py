"""Tests for Planner — DAG validation and rendering."""

from __future__ import annotations

import json

import pytest
from unittest.mock import Mock

from automission.models import PlanCriterion, PlanDraft, PlanGroup, VerificationSurface
from automission.planner import validate_dag, PlanValidationError
from automission.planner import render_mission_md, render_acceptance_md
from automission.planner import Planner
from automission.acceptance import parse_acceptance_md


def _make_draft(groups: list[PlanGroup], **kwargs) -> PlanDraft:
    defaults = dict(
        mission_summary="test",
        constraints=[],
        verification_surface=VerificationSurface(runner="pytest", targets=["tests/"]),
        assumptions=[],
    )
    defaults.update(kwargs)
    return PlanDraft(groups=groups, **defaults)


def _make_group(
    id: str, name: str, depends_on: list[str] | None = None, n_criteria: int = 1
) -> PlanGroup:
    return PlanGroup(
        id=id,
        name=name,
        depends_on=depends_on or [],
        criteria=[
            PlanCriterion(text=f"criterion {i}", verification_hint=f"check {i}")
            for i in range(1, n_criteria + 1)
        ],
    )


class TestValidateDag:
    def test_valid_linear_chain(self):
        groups = [
            _make_group("auth", "auth"),
            _make_group("api", "api", depends_on=["auth"]),
        ]
        validate_dag(_make_draft(groups))

    def test_valid_no_deps(self):
        groups = [_make_group("a", "a"), _make_group("b", "b")]
        validate_dag(_make_draft(groups))

    def test_cycle_two_nodes(self):
        groups = [
            _make_group("a", "a", depends_on=["b"]),
            _make_group("b", "b", depends_on=["a"]),
        ]
        with pytest.raises(PlanValidationError, match="cycle"):
            validate_dag(_make_draft(groups))

    def test_self_dependency(self):
        groups = [_make_group("a", "a", depends_on=["a"])]
        with pytest.raises(PlanValidationError, match="self"):
            validate_dag(_make_draft(groups))

    def test_dangling_dep(self):
        groups = [_make_group("a", "a", depends_on=["missing"])]
        with pytest.raises(PlanValidationError, match="missing"):
            validate_dag(_make_draft(groups))

    def test_empty_criteria(self):
        groups = [PlanGroup(id="a", name="a", criteria=[])]
        with pytest.raises(PlanValidationError, match="no criteria"):
            validate_dag(_make_draft(groups))

    def test_invalid_id_format(self):
        groups = [_make_group("Invalid-ID", "Invalid-ID")]
        with pytest.raises(PlanValidationError, match="snake_case"):
            validate_dag(_make_draft(groups))

    def test_id_name_mismatch(self):
        groups = [_make_group("wrong_id", "Auth Schema")]
        with pytest.raises(PlanValidationError, match="mismatch"):
            validate_dag(_make_draft(groups))

    def test_id_name_consistent(self):
        groups = [_make_group("auth_schema", "Auth Schema")]
        validate_dag(_make_draft(groups))

    def test_empty_groups(self):
        with pytest.raises(PlanValidationError, match="at least one group"):
            validate_dag(_make_draft([]))

    def test_duplicate_group_ids(self):
        groups = [_make_group("a", "a"), _make_group("a", "a")]
        with pytest.raises(PlanValidationError, match="Duplicate"):
            validate_dag(_make_draft(groups))


class TestRenderers:
    @pytest.fixture
    def sample_draft(self):
        return _make_draft(
            groups=[
                PlanGroup(
                    id="auth_schema",
                    name="Auth Schema",
                    depends_on=[],
                    criteria=[
                        PlanCriterion(
                            text="Users table exists", verification_hint="check DB"
                        ),
                        PlanCriterion(
                            text="Password hashing works",
                            verification_hint="bcrypt check",
                        ),
                    ],
                ),
                PlanGroup(
                    id="api_endpoints",
                    name="API Endpoints",
                    depends_on=["auth_schema"],
                    criteria=[
                        PlanCriterion(
                            text="POST /todos creates todo",
                            verification_hint="curl POST",
                        )
                    ],
                ),
            ],
            mission_summary="Build a TODO API with authentication",
            constraints=["All responses in JSON", "JWT tokens for auth"],
            verification_surface=VerificationSurface(
                runner="pytest", targets=["tests/"], options="-v"
            ),
            assumptions=["Python with Flask"],
        )

    def test_render_mission_md(self, sample_draft):
        result = render_mission_md(sample_draft)
        assert "# Mission" in result
        assert "Build a TODO API with authentication" in result
        assert "All responses in JSON" in result
        assert "JWT tokens for auth" in result

    def test_render_acceptance_md_round_trip(self, sample_draft):
        rendered = render_acceptance_md(sample_draft)
        parsed = parse_acceptance_md(rendered)
        assert len(parsed) == 2
        assert parsed[0].id == "auth_schema"
        assert parsed[0].name == "Auth Schema"
        assert parsed[0].depends_on == []
        assert len(parsed[0].criteria) == 2
        assert parsed[0].criteria[0].text == "Users table exists"
        assert parsed[1].id == "api_endpoints"
        assert parsed[1].depends_on == ["auth_schema"]
        assert len(parsed[1].criteria) == 1

    def test_render_acceptance_md_no_deps_line_for_root(self, sample_draft):
        rendered = render_acceptance_md(sample_draft)
        lines = rendered.splitlines()
        auth_idx = next(i for i, line in enumerate(lines) if "Auth Schema" in line)
        next_lines = [line for line in lines[auth_idx + 1 :] if line.strip()]
        assert not next_lines[0].startswith("Depends on:")

    def test_render_acceptance_md_single_group_no_deps(self):
        draft = _make_draft(groups=[_make_group("setup", "setup")])
        rendered = render_acceptance_md(draft)
        parsed = parse_acceptance_md(rendered)
        assert len(parsed) == 1
        assert parsed[0].id == "setup"
        assert parsed[0].depends_on == []


def _mock_cli_output(plan_dict: dict) -> str:
    """Create mock CLI JSON output wrapping a plan dict."""
    return json.dumps({"type": "result", "result": "", "structured_output": plan_dict})


VALID_PLAN_INPUT = {
    "mission_summary": "Build a TODO API with authentication",
    "constraints": ["JSON responses"],
    "groups": [
        {
            "id": "auth_schema",
            "name": "Auth Schema",
            "depends_on": [],
            "criteria": [
                {"text": "Users table exists", "verification_hint": "check DB"},
            ],
        },
        {
            "id": "api_endpoints",
            "name": "API Endpoints",
            "depends_on": ["auth_schema"],
            "criteria": [
                {"text": "POST /todos works", "verification_hint": "curl test"},
            ],
        },
    ],
    "assumptions": ["Python"],
}


def _mock_backend(responses):
    """Create a mock StructuredOutputBackend returning given responses in order."""
    backend = Mock()
    if isinstance(responses, list):
        backend.query = Mock(side_effect=responses)
    else:
        backend.query = Mock(return_value=responses)
    return backend


class TestPlanner:
    def test_plan_returns_valid_draft(self):
        backend = _mock_backend(VALID_PLAN_INPUT)
        planner = Planner(backend=backend, model="claude-sonnet-4-6")
        draft = planner.plan("Build a TODO API with auth")
        assert draft.mission_summary == "Build a TODO API with authentication"
        assert len(draft.groups) == 2
        assert draft.groups[0].id == "auth_schema"
        assert draft.groups[1].depends_on == ["auth_schema"]
        # Now uses default Option B runner
        assert draft.verification_surface.runner == "pytest"

    def test_plan_passes_model_to_backend(self):
        backend = _mock_backend(VALID_PLAN_INPUT)
        planner = Planner(backend=backend, model="claude-sonnet-4-6")
        planner.plan("Build something")
        call_kwargs = backend.query.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-6"

    def test_plan_validation_failure_triggers_repair(self):
        bad_input = {
            **VALID_PLAN_INPUT,
            "groups": [
                {
                    "id": "a",
                    "name": "a",
                    "depends_on": ["b"],
                    "criteria": [{"text": "x", "verification_hint": "y"}],
                },
                {
                    "id": "b",
                    "name": "b",
                    "depends_on": ["a"],
                    "criteria": [{"text": "x", "verification_hint": "y"}],
                },
            ],
        }
        backend = _mock_backend([bad_input, VALID_PLAN_INPUT])
        planner = Planner(backend=backend)
        draft = planner.plan("Build something")
        assert len(draft.groups) == 2
        assert backend.query.call_count == 2

    def test_plan_double_failure_raises(self):
        bad_input = {
            **VALID_PLAN_INPUT,
            "groups": [
                {
                    "id": "a",
                    "name": "a",
                    "depends_on": ["b"],
                    "criteria": [{"text": "x", "verification_hint": "y"}],
                },
                {
                    "id": "b",
                    "name": "b",
                    "depends_on": ["a"],
                    "criteria": [{"text": "x", "verification_hint": "y"}],
                },
            ],
        }
        backend = _mock_backend([bad_input, bad_input])
        planner = Planner(backend=backend)
        with pytest.raises(PlanValidationError):
            planner.plan("Build something")

    def test_plan_backend_failure_raises(self):
        from automission.structured_output import CLIResponseError

        backend = Mock()
        backend.query = Mock(side_effect=CLIResponseError("connection failed"))
        planner = Planner(backend=backend)
        with pytest.raises(Exception):
            planner.plan("Build something")


class TestRoundTrip:
    """End-to-end: PlanDraft -> render -> parse -> verify equivalence."""

    def test_full_round_trip(self):
        draft = PlanDraft(
            mission_summary="Build a REST API for task management",
            constraints=["JSON only", "Auth required"],
            groups=[
                PlanGroup(
                    id="database_schema",
                    name="Database Schema",
                    depends_on=[],
                    criteria=[
                        PlanCriterion(
                            text="Tasks table with id, title, done columns",
                            verification_hint="check schema",
                        ),
                        PlanCriterion(
                            text="Users table with id, email, password_hash",
                            verification_hint="check schema",
                        ),
                    ],
                ),
                PlanGroup(
                    id="auth_system",
                    name="Auth System",
                    depends_on=["database_schema"],
                    criteria=[
                        PlanCriterion(
                            text="Login endpoint returns JWT",
                            verification_hint="POST /login",
                        ),
                        PlanCriterion(
                            text="Protected endpoints reject unauthenticated requests",
                            verification_hint="GET /tasks without token",
                        ),
                    ],
                ),
                PlanGroup(
                    id="crud_endpoints",
                    name="CRUD Endpoints",
                    depends_on=["database_schema", "auth_system"],
                    criteria=[
                        PlanCriterion(
                            text="POST /tasks creates a task",
                            verification_hint="curl POST",
                        ),
                        PlanCriterion(
                            text="GET /tasks returns user's tasks",
                            verification_hint="curl GET",
                        ),
                        PlanCriterion(
                            text="DELETE /tasks/:id removes a task",
                            verification_hint="curl DELETE",
                        ),
                    ],
                ),
            ],
            verification_surface=VerificationSurface(
                runner="pytest", targets=["tests/"], options="-v --tb=short"
            ),
            assumptions=["Python", "SQLite"],
        )

        # Validate
        validate_dag(draft)

        # Render
        acceptance_md = render_acceptance_md(draft)
        mission_md = render_mission_md(draft)

        # Parse back
        parsed_groups = parse_acceptance_md(acceptance_md)
        assert len(parsed_groups) == 3

        # Verify equivalence
        for orig, parsed in zip(draft.groups, parsed_groups):
            assert parsed.id == orig.id
            assert parsed.name == orig.name
            assert parsed.depends_on == orig.depends_on
            assert len(parsed.criteria) == len(orig.criteria)
            for oc, pc in zip(orig.criteria, parsed.criteria):
                assert pc.text == oc.text

        # Verify file contents
        assert "Build a REST API" in mission_md
        assert "JSON only" in mission_md
