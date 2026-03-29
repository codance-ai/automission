"""Tests for dependency-aware acceptance checklist (issue #4).

Covers: frontier computation with DAG dependencies, contract scoping to
target groups, and single-agent frontier loop sequencing.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from automission.acceptance import parse_acceptance_md
from automission.backend.mock import MockBackend
from automission.db import Ledger
from automission.loop import _derive_contract
from automission.models import (
    AcceptanceGroup,
    Criterion,
    CriterionResult,
    VerifierResult,
)


# ── Fixtures ──


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "mission.db")


def _make_chain_groups():
    """A → B → C linear chain."""
    return [
        AcceptanceGroup(
            id="a",
            name="group_a",
            criteria=[Criterion(id="a_c1", group_id="a", text="A criterion 1")],
        ),
        AcceptanceGroup(
            id="b",
            name="group_b",
            depends_on=["a"],
            criteria=[Criterion(id="b_c1", group_id="b", text="B criterion 1")],
        ),
        AcceptanceGroup(
            id="c",
            name="group_c",
            depends_on=["b"],
            criteria=[Criterion(id="c_c1", group_id="c", text="C criterion 1")],
        ),
    ]


def _make_parallel_groups():
    """Three independent groups with no dependencies."""
    return [
        AcceptanceGroup(
            id="x",
            name="group_x",
            criteria=[Criterion(id="x_c1", group_id="x", text="X criterion")],
        ),
        AcceptanceGroup(
            id="y",
            name="group_y",
            criteria=[Criterion(id="y_c1", group_id="y", text="Y criterion")],
        ),
        AcceptanceGroup(
            id="z",
            name="group_z",
            criteria=[Criterion(id="z_c1", group_id="z", text="Z criterion")],
        ),
    ]


def _make_mixed_dag():
    """Mixed DAG: A and B independent, C depends on both.

    A ──┐
        ├──> C
    B ──┘
    """
    return [
        AcceptanceGroup(
            id="a",
            name="group_a",
            criteria=[Criterion(id="a_c1", group_id="a", text="A criterion")],
        ),
        AcceptanceGroup(
            id="b",
            name="group_b",
            criteria=[Criterion(id="b_c1", group_id="b", text="B criterion")],
        ),
        AcceptanceGroup(
            id="c",
            name="group_c",
            depends_on=["a", "b"],
            criteria=[Criterion(id="c_c1", group_id="c", text="C criterion")],
        ),
    ]


def _setup_mission(ledger, groups, mission_id="m1"):
    ledger.create_mission(mission_id=mission_id, goal="test", backend="claude")
    ledger.store_acceptance_groups(mission_id, groups)


# ── Frontier Tests ──


class TestFrontierChainDependency:
    """A → B → C: agents work A first, B unlocks after A, C after B."""

    def test_initial_frontier_is_a_only(self, ledger):
        _setup_mission(ledger, _make_chain_groups())
        frontier = ledger.get_frontier_groups("m1")
        ids = [g["id"] for g in frontier]
        assert ids == ["a"]

    def test_completing_a_unlocks_b(self, ledger):
        _setup_mission(ledger, _make_chain_groups())
        ledger.update_group_status("a", completed=True)
        frontier = ledger.get_frontier_groups("m1")
        ids = [g["id"] for g in frontier]
        assert "b" in ids
        assert "a" not in ids
        assert "c" not in ids

    def test_completing_a_and_b_unlocks_c(self, ledger):
        _setup_mission(ledger, _make_chain_groups())
        ledger.update_group_status("a", completed=True)
        ledger.update_group_status("b", completed=True)
        frontier = ledger.get_frontier_groups("m1")
        ids = [g["id"] for g in frontier]
        assert ids == ["c"]

    def test_all_completed_frontier_empty(self, ledger):
        _setup_mission(ledger, _make_chain_groups())
        for gid in ("a", "b", "c"):
            ledger.update_group_status(gid, completed=True)
        frontier = ledger.get_frontier_groups("m1")
        assert frontier == []


class TestFrontierParallelGroups:
    """Independent groups run in parallel (all in frontier)."""

    def test_all_independent_in_frontier(self, ledger):
        _setup_mission(ledger, _make_parallel_groups())
        frontier = ledger.get_frontier_groups("m1")
        ids = {g["id"] for g in frontier}
        assert ids == {"x", "y", "z"}

    def test_completing_one_leaves_others(self, ledger):
        _setup_mission(ledger, _make_parallel_groups())
        ledger.update_group_status("x", completed=True)
        frontier = ledger.get_frontier_groups("m1")
        ids = {g["id"] for g in frontier}
        assert ids == {"y", "z"}


class TestFrontierMixedDAG:
    """A and B independent, C depends on both."""

    def test_initial_frontier(self, ledger):
        _setup_mission(ledger, _make_mixed_dag())
        frontier = ledger.get_frontier_groups("m1")
        ids = {g["id"] for g in frontier}
        assert ids == {"a", "b"}

    def test_one_dep_done_c_still_blocked(self, ledger):
        _setup_mission(ledger, _make_mixed_dag())
        ledger.update_group_status("a", completed=True)
        frontier = ledger.get_frontier_groups("m1")
        ids = {g["id"] for g in frontier}
        assert "c" not in ids
        assert "b" in ids

    def test_both_deps_done_c_unlocked(self, ledger):
        _setup_mission(ledger, _make_mixed_dag())
        ledger.update_group_status("a", completed=True)
        ledger.update_group_status("b", completed=True)
        frontier = ledger.get_frontier_groups("m1")
        ids = {g["id"] for g in frontier}
        assert ids == {"c"}


# ── Contract Scoping Tests ──


class TestDeriveContractScoping:
    """_derive_contract should scope to target_groups when provided."""

    def _make_verification(self):
        return VerifierResult(
            contract_passed=False,
            mission_passed=False,
            gate_source="script",
            passed_criteria=[
                CriterionResult(criterion="A criterion 1", passed=True, detail="ok"),
            ],
            failed_criteria=[
                CriterionResult(criterion="B criterion 1", passed=False, detail="fail"),
                CriterionResult(criterion="C criterion 1", passed=False, detail="fail"),
            ],
            group_statuses={"a": True, "b": False, "c": False},
            suggestion="Fix B and C",
        )

    def test_no_target_groups_includes_all(self):
        vr = self._make_verification()
        contract = _derive_contract(vr)
        assert "B criterion 1" in contract.done_criteria
        assert "C criterion 1" in contract.done_criteria
        assert "A criterion 1" in contract.non_goals

    def test_target_groups_scopes_contract(self):
        vr = self._make_verification()
        groups = _make_chain_groups()
        target = [g for g in groups if g.id == "b"]  # only group B
        contract = _derive_contract(vr, target_groups=target)
        assert "B criterion 1" in contract.done_criteria
        assert "C criterion 1" not in contract.done_criteria
        # A criterion is not in target, so excluded from non_goals
        assert "A criterion 1" not in contract.non_goals


# ── Acceptance Parsing with Dependencies ──


class TestParseAcceptanceDependencies:
    def test_chain_parsing(self):
        text = """# Acceptance Criteria

## auth_schema
Authentication schema is correct.

- Users table exists with email and password_hash columns

## api_layer
API endpoints work correctly.

Depends on: auth_schema

- GET /users returns user list
- POST /users creates a user

## ui_components
UI renders correctly.

Depends on: api_layer

- Login form submits to POST /auth
"""
        groups = parse_acceptance_md(text)
        assert len(groups) == 3
        assert groups[0].id == "auth_schema"
        assert groups[0].depends_on == []
        assert groups[1].id == "api_layer"
        assert groups[1].depends_on == ["auth_schema"]
        assert groups[2].id == "ui_components"
        assert groups[2].depends_on == ["api_layer"]

    def test_mixed_dag_parsing(self):
        text = """# Criteria

## db_schema
- Tables exist

## auth_schema
- Auth tables exist

## api_endpoints
Depends on: db_schema, auth_schema

- Endpoints work
"""
        groups = parse_acceptance_md(text)
        assert groups[2].depends_on == ["db_schema", "auth_schema"]


# ── Single-Agent Frontier Loop Integration ──


CHAIN_ACCEPTANCE_MD = """\
# Acceptance Criteria

## basic_operations
All 4 basic arithmetic operations return correct results.

- add(a, b) returns the sum of a and b
- subtract(a, b) returns the difference of a and b
- multiply(a, b) returns the product of a and b
- divide(a, b) returns the quotient of a divided by b

## edge_cases
Edge cases are handled correctly.

Depends on: basic_operations

- divide(a, 0) raises ValueError
- all operations handle negative numbers correctly
"""

CALC_PY = """\
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
"""

VERIFY_SH = """\
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pytest src/tests/test_calc.py -v --tb=short 2>&1
"""


class TestSingleAgentFrontierLoop:
    """Integration test: single-agent frontier loop sequences groups correctly."""

    @pytest.fixture
    def fixture_dir(self):
        return Path(__file__).parent / "fixtures" / "m1-calculator"

    def test_frontier_loop_sequences_groups(self, tmp_path, fixture_dir):
        """Single agent works basic_operations first, then edge_cases."""
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": CALC_PY},
        )

        acceptance_path = tmp_path / "ACCEPTANCE.md"
        acceptance_path.write_text(CHAIN_ACCEPTANCE_MD)
        verify_path = tmp_path / "verify.sh"
        verify_path.write_text(VERIFY_SH)
        verify_path.chmod(verify_path.stat().st_mode | stat.S_IEXEC)

        from automission.workspace import create_mission

        ws = create_mission(
            mission_id="dag-001",
            goal="Build calculator",
            acceptance_path=acceptance_path,
            verify_path=verify_path,
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )

        from automission.executor import _run_single_agent_frontier
        from automission.events import EventWriter
        from automission.verifier import Verifier

        verifier = Verifier()
        with EventWriter(ws / "events.jsonl") as ew:
            outcome = _run_single_agent_frontier(
                mission_id="dag-001",
                ws=ws,
                backend=backend,
                verifier=verifier,
                max_iterations=20,
                max_cost=10.0,
                timeout=3600,
                cancel_flag=lambda: False,
                event_writer=ew,
            )

        assert outcome == "completed"

        # Verify both groups are completed
        ledger = Ledger(ws / "mission.db")
        assert ledger.is_group_completed("basic_operations")
        assert ledger.is_group_completed("edge_cases")
        ledger.close()

    def test_frontier_loop_prompt_scoped(self, tmp_path, fixture_dir):
        """First attempt prompt should mention target group, not all groups."""
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": CALC_PY},
        )

        acceptance_path = tmp_path / "ACCEPTANCE.md"
        acceptance_path.write_text(CHAIN_ACCEPTANCE_MD)
        verify_path = tmp_path / "verify.sh"
        verify_path.write_text(VERIFY_SH)
        verify_path.chmod(verify_path.stat().st_mode | stat.S_IEXEC)

        from automission.workspace import create_mission

        ws = create_mission(
            mission_id="dag-002",
            goal="Build calculator",
            acceptance_path=acceptance_path,
            verify_path=verify_path,
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )

        from automission.executor import _run_single_agent_frontier
        from automission.events import EventWriter
        from automission.verifier import Verifier

        verifier = Verifier()
        with EventWriter(ws / "events.jsonl") as ew:
            _run_single_agent_frontier(
                mission_id="dag-002",
                ws=ws,
                backend=backend,
                verifier=verifier,
                max_iterations=20,
                max_cost=10.0,
                timeout=3600,
                cancel_flag=lambda: False,
                event_writer=ew,
            )

        # The first attempt's prompt should mention "Current Focus"
        # with basic_operations (the first frontier group)
        assert len(backend.attempts) >= 1
        first_prompt = backend.attempts[0].prompt
        assert "Current Focus" in first_prompt
        assert "basic_operations" in first_prompt
