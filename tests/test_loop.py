"""Tests for single-iteration loop and full run_loop."""

import subprocess
from pathlib import Path

import pytest

from automission.backend.mock import MockBackend
from automission.db import Ledger
from automission.loop import run_single_iteration, run_loop
from automission.models import VerifierResult
from automission.verifier import Verifier
from conftest import MockCriticBackend
from automission.workspace import create_mission


@pytest.fixture
def fixture_dir():
    return Path(__file__).parent / "fixtures" / "m1-calculator"


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


@pytest.fixture
def mission_workspace(tmp_path, fixture_dir):
    """Create a fully initialized mission workspace."""
    backend = MockBackend(
        result_status="completed",
        exit_code=0,
        simulate_files={"src/calc.py": CALC_PY},
    )
    ws = create_mission(
        mission_id="test-001",
        goal="Build calculator",
        acceptance_path=fixture_dir / "ACCEPTANCE.md",
        verify_path=fixture_dir / "verify.sh",
        backend=backend,
        workspace_dir=tmp_path / "ws",
        init_files_dir=fixture_dir / "workspace",
    )
    return ws, backend


class TestRunSingleIteration:
    def test_attempt_runs_and_records(self, mission_workspace):
        ws, backend = mission_workspace
        verifier = Verifier(backend=MockCriticBackend())

        run_single_iteration(
            mission_id="test-001",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        ledger = Ledger(ws / "mission.db")
        attempts = ledger.get_attempts("test-001")
        assert len(attempts) == 1
        assert attempts[0]["agent_id"] == "agent-1"
        assert attempts[0]["attempt_number"] == 1
        ledger.close()

    def test_returns_verifier_result(self, mission_workspace):
        ws, backend = mission_workspace
        verifier = Verifier(backend=MockCriticBackend())

        result = run_single_iteration(
            mission_id="test-001",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        assert isinstance(result, VerifierResult)
        assert result.gate_source == "script"

    def test_auto_commits_changes(self, mission_workspace):
        ws, backend = mission_workspace
        verifier = Verifier(backend=MockCriticBackend())

        run_single_iteration(
            mission_id="test-001",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=ws, capture_output=True, text=True
        )
        assert (
            "attempt" in log.stdout.lower() or len(log.stdout.strip().splitlines()) >= 2
        )

    def test_prompt_contains_instructions(self, mission_workspace):
        ws, backend = mission_workspace
        verifier = Verifier(backend=MockCriticBackend())

        run_single_iteration(
            mission_id="test-001",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        assert len(backend.attempts) == 1
        prompt = backend.attempts[0].prompt
        assert "ACCEPTANCE.md" in prompt or "acceptance" in prompt.lower()
        assert "verify.sh" in prompt

    def test_passing_mission_marked_completed(self, mission_workspace):
        """When verify.sh passes and critic says all groups done, mission completed."""
        ws, backend = mission_workspace
        # MockCriticBackend marks all groups as passed when gate passes
        verifier = Verifier(backend=MockCriticBackend())

        result = run_single_iteration(
            mission_id="test-001",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        # The mock writes correct calc.py, verify.sh should pass
        if result.contract_passed:
            ledger = Ledger(ws / "mission.db")
            mission = ledger.get_mission("test-001")
            assert mission["status"] == "completed"
            ledger.close()


class TestRunLoop:
    def test_loop_terminates_on_pass(self, tmp_path, fixture_dir):
        """Loop stops after verification passes."""
        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "def add(a, b): return a + b\n"},
                {"src/calc.py": CALC_PY},
            ],
        )
        ws = create_mission(
            mission_id="loop-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id="loop-001",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=10,
            max_cost=10.0,
            timeout=3600,
        )
        assert outcome == "completed"
        ledger = Ledger(ws / "mission.db")
        assert ledger.get_mission("loop-001")["status"] == "completed"
        assert ledger.get_mission("loop-001")["total_attempts"] == 2
        ledger.close()

    def test_circuit_breaker_max_iterations(self, tmp_path, fixture_dir):
        backend = MockBackend(
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
        )
        ws = create_mission(
            mission_id="loop-iter",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id="loop-iter",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=3,
            max_cost=100.0,
            timeout=3600,
        )
        assert outcome == "resource_limit"
        ledger = Ledger(ws / "mission.db")
        assert ledger.get_mission("loop-iter")["total_attempts"] == 3
        ledger.close()

    def test_circuit_breaker_max_cost(self, tmp_path, fixture_dir):
        backend = MockBackend(
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
            cost_usd=2.0,
        )
        ws = create_mission(
            mission_id="loop-cost",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id="loop-cost",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=100,
            max_cost=3.0,
            timeout=3600,
        )
        assert outcome == "resource_limit"
        ledger = Ledger(ws / "mission.db")
        assert ledger.get_mission("loop-cost")["total_attempts"] <= 2
        ledger.close()

    def test_retry_prompt_contains_feedback(self, tmp_path, fixture_dir):
        """Second attempt prompt should contain feedback from first failure."""
        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "def add(a, b): return a + b\n"},
                {"src/calc.py": CALC_PY},
            ],
        )
        ws = create_mission(
            mission_id="loop-feedback",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        run_loop(
            mission_id="loop-feedback",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=5,
            max_cost=100.0,
            timeout=3600,
        )
        assert len(backend.attempts) == 2
        second_prompt = backend.attempts[1].prompt
        assert "FAIL" in second_prompt or "failed" in second_prompt.lower()

    def test_cancel_flag_stops_loop(self, tmp_path, fixture_dir):
        backend = MockBackend(
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
        )
        ws = create_mission(
            mission_id="loop-cancel",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        call_count = 0

        def cancel_after_one():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        outcome = run_loop(
            mission_id="loop-cancel",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=10,
            max_cost=100.0,
            timeout=3600,
            cancel_flag=cancel_after_one,
        )
        assert outcome == "cancelled"
        ledger = Ledger(ws / "mission.db")
        assert ledger.get_mission("loop-cancel")["total_attempts"] == 1
        ledger.close()

    def test_resume_continues_from_ledger(self, tmp_path, fixture_dir):
        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "def add(a, b): return a + b\n"},
                {"src/calc.py": CALC_PY},
            ],
        )
        ws = create_mission(
            mission_id="loop-resume",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        # Cancel after first attempt
        call_count = 0

        def cancel_after_one():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        outcome = run_loop(
            mission_id="loop-resume",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=10,
            max_cost=100.0,
            timeout=3600,
            cancel_flag=cancel_after_one,
        )
        assert outcome == "cancelled"
        # Resume — reset backend counter, update mission status back to running
        backend._attempt_count = 1
        ledger = Ledger(ws / "mission.db")
        ledger.update_mission_status("loop-resume", "running")
        ledger.close()
        outcome2 = run_loop(
            mission_id="loop-resume",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=10,
            max_cost=100.0,
            timeout=3600,
        )
        assert outcome2 == "completed"
        ledger = Ledger(ws / "mission.db")
        assert ledger.get_mission("loop-resume")["total_attempts"] == 2
        ledger.close()

    def test_stall_detection_triggers(self, tmp_path, fixture_dir):
        """After N identical-score attempts, stall detection should activate."""
        backend = MockBackend(
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
        )
        ws = create_mission(
            mission_id="loop-stall",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id="loop-stall",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=10,
            max_cost=100.0,
            timeout=3600,
            stall_threshold=3,
        )
        # Should stop due to stall (failed) before hitting max_iterations
        assert outcome in ("failed", "resource_limit")

    def test_loop_with_separate_mission_dir(self, tmp_path, fixture_dir):
        """run_loop works when mission_dir is explicitly provided."""
        backend = MockBackend(simulate_files={"src/calc.py": CALC_PY})
        ws = create_mission(
            mission_id="loop-sep",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id="loop-sep",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=5,
            max_cost=10.0,
            timeout=3600,
            mission_dir=ws,
        )
        assert outcome in ("completed", "failed", "resource_limit")

    def test_dirty_state_included_in_prompt(self, tmp_path, fixture_dir):
        backend = MockBackend(simulate_files={"src/calc.py": CALC_PY})
        ws = create_mission(
            mission_id="loop-dirty",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        # Create dirty state
        (ws / "src" / "partial.py").write_text("# partial work\n")
        verifier = Verifier(backend=MockCriticBackend())
        run_loop(
            mission_id="loop-dirty",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=1,
            max_cost=100.0,
            timeout=3600,
        )
        prompt = backend.attempts[0].prompt
        assert "partial.py" in prompt or "uncommitted" in prompt.lower()


class TestEventEnrichment:
    """Test that events contain enriched data for CLI display."""

    def test_verification_event_includes_criteria_and_suggestion(
        self, tmp_path, fixture_dir
    ):
        """Verification event should include passed/failed criteria and suggestion."""
        from automission.events import EventWriter, EventTailer

        backend = MockBackend(
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
        )
        ws = create_mission(
            mission_id="evt-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        with EventWriter(ws / "events.jsonl") as ew:
            run_loop(
                mission_id="evt-001",
                workdir=ws,
                backend=backend,
                verifier=verifier,
                max_iterations=1,
                max_cost=10.0,
                timeout=3600,
                event_writer=ew,
            )

        events = list(EventTailer(ws / "events.jsonl").read_existing())
        verify_events = [e for e in events if e["type"] == "verification"]
        assert len(verify_events) == 1

        ve = verify_events[0]
        # Should have structured criteria (dicts, not plain strings)
        assert "passed_criteria" in ve
        assert "failed_criteria" in ve
        assert "suggestion" in ve
        # Criteria should be dicts with criterion and group keys
        for c in ve["failed_criteria"] + ve["passed_criteria"]:
            assert "criterion" in c
            assert "group" in c

    def test_attempt_end_event_includes_changed_files(self, tmp_path, fixture_dir):
        """attempt_end event should include changed_files list."""
        from automission.events import EventWriter, EventTailer

        backend = MockBackend(
            simulate_files={"src/calc.py": CALC_PY},
        )
        ws = create_mission(
            mission_id="evt-002",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        with EventWriter(ws / "events.jsonl") as ew:
            run_loop(
                mission_id="evt-002",
                workdir=ws,
                backend=backend,
                verifier=verifier,
                max_iterations=1,
                max_cost=10.0,
                timeout=3600,
                event_writer=ew,
            )

        events = list(EventTailer(ws / "events.jsonl").read_existing())
        end_events = [e for e in events if e["type"] == "attempt_end"]
        assert len(end_events) == 1
        assert "changed_files" in end_events[0]
        assert isinstance(end_events[0]["changed_files"], list)

    def test_retry_attempt_start_includes_scope(self, tmp_path, fixture_dir):
        """attempt_start on retry should include contract scope."""
        from automission.events import EventWriter, EventTailer

        # First attempt fails (incomplete calc), second attempt should have scope
        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "def add(a, b): return a + b\n"},
                {"src/calc.py": CALC_PY},
            ],
        )
        ws = create_mission(
            mission_id="evt-003",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())
        with EventWriter(ws / "events.jsonl") as ew:
            run_loop(
                mission_id="evt-003",
                workdir=ws,
                backend=backend,
                verifier=verifier,
                max_iterations=3,
                max_cost=10.0,
                timeout=3600,
                event_writer=ew,
            )

        events = list(EventTailer(ws / "events.jsonl").read_existing())
        start_events = [e for e in events if e["type"] == "attempt_start"]
        assert len(start_events) >= 2
        # First attempt should NOT have scope
        assert "scope" not in start_events[0] or start_events[0].get("scope") is None
        # Second attempt SHOULD have scope
        assert "scope" in start_events[1]
        assert start_events[1]["scope"]  # non-empty
