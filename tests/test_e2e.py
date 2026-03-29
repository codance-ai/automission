"""End-to-end integration test with MockBackend."""

import subprocess
from pathlib import Path

import pytest

from automission.backend.mock import MockBackend
from automission.db import Ledger
from automission.loop import run_loop, run_single_iteration
from automission.orchestrator import run_multi_agent
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


class TestE2EPassingMission:
    """Full flow where the agent produces correct code on first attempt."""

    def test_full_flow(self, tmp_path, fixture_dir):
        # 1. Setup
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            cost_usd=0.30,
            simulate_files={"src/calc.py": CALC_PY},
        )

        # 2. Create mission workspace
        ws = create_mission(
            mission_id="e2e-001",
            goal="Write calculator functions",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )

        # 3. Verify workspace
        assert (ws / "MISSION.md").exists()
        assert (ws / "ACCEPTANCE.md").exists()
        assert (ws / "verify.sh").exists()
        assert (ws / "AUTOMISSION.md").exists()
        assert (ws / "CLAUDE.md").exists()
        assert (ws / "mission.db").exists()
        assert (ws / "src" / "tests" / "test_calc.py").exists()

        # 4. Run single iteration
        verifier = Verifier(backend=MockCriticBackend())
        result = run_single_iteration(
            mission_id="e2e-001",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        # 5. Assertions
        assert result.gate_source == "script"
        assert result.contract_passed is True

        # 6. Ledger state
        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission("e2e-001")
        assert mission["status"] == "completed"
        assert mission["total_cost"] == 0.30
        assert mission["total_attempts"] == 1

        attempts = ledger.get_attempts("e2e-001")
        assert len(attempts) == 1
        assert attempts[0]["status"] == "completed"
        assert bool(attempts[0]["verification_passed"]) is True

        groups = ledger.get_acceptance_groups("e2e-001")
        assert len(groups) == 2
        ledger.close()

    def test_prompt_sent_to_backend(self, tmp_path, fixture_dir):
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": CALC_PY},
        )
        ws = create_mission(
            mission_id="e2e-002",
            goal="Write calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )

        verifier = Verifier(backend=MockCriticBackend())
        run_single_iteration(
            mission_id="e2e-002",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        assert len(backend.attempts) == 1
        spec = backend.attempts[0]
        assert "verify.sh" in spec.prompt
        assert spec.workdir == ws


class TestE2EFailingMission:
    def test_failing_attempt(self, tmp_path, fixture_dir):
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            cost_usd=0.20,
            simulate_files={
                "src/calc.py": "def add(a, b): return a + b\n",
            },
        )

        ws = create_mission(
            mission_id="e2e-fail",
            goal="Write calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )

        verifier = Verifier(backend=MockCriticBackend())
        result = run_single_iteration(
            mission_id="e2e-fail",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        assert result.contract_passed is False
        assert result.gate_source == "script"

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission("e2e-fail")
        assert mission["status"] == "running"
        assert mission["total_attempts"] == 1
        ledger.close()


class TestE2EWithMockCritic:
    def test_critic_produces_structured_result(self, tmp_path, fixture_dir):
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            cost_usd=0.25,
            simulate_files={"src/calc.py": CALC_PY},
        )

        ws = create_mission(
            mission_id="e2e-critic",
            goal="Write calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )

        critic_output = {
            "passed_criteria": [
                {"criterion": "add works", "passed": True, "detail": "ok"},
                {"criterion": "subtract works", "passed": True, "detail": "ok"},
                {"criterion": "multiply works", "passed": True, "detail": "ok"},
                {"criterion": "divide works", "passed": True, "detail": "ok"},
                {"criterion": "edge cases", "passed": True, "detail": "ok"},
            ],
            "failed_criteria": [],
            "group_statuses": {"basic_operations": True, "edge_cases": True},
            "suggestion": "",
            "reason": "All 6 tests pass. Calculator implementation is complete.",
            "score": 1.0,
        }

        from unittest.mock import Mock

        critic_backend = Mock()
        critic_backend.query = Mock(return_value=critic_output)
        verifier = Verifier(backend=critic_backend, verifier_model="claude-sonnet-4-6")
        result = run_single_iteration(
            mission_id="e2e-critic",
            workdir=ws,
            backend=backend,
            verifier=verifier,
        )

        assert result.contract_passed is True
        assert result.mission_passed is True
        assert result.score == 1.0
        assert len(result.passed_criteria) == 5
        assert (
            result.reason == "All 6 tests pass. Calculator implementation is complete."
        )
        assert result.group_statuses == {"basic_operations": True, "edge_cases": True}


class TestE2EMultiIteration:
    def test_loop_iterates_and_completes(self, tmp_path, fixture_dir):
        """Agent fails first, succeeds on second attempt."""
        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "def add(a, b): return a + b\n"},  # fail
                {"src/calc.py": CALC_PY},  # pass
            ],
        )
        ws = create_mission(
            mission_id="e2e-loop",
            goal="Write calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())

        outcome = run_loop(
            mission_id="e2e-loop",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=5,
            max_cost=10.0,
            timeout=3600,
        )

        assert outcome == "completed"

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission("e2e-loop")
        assert mission["status"] == "completed"
        assert mission["total_attempts"] == 2

        # Verify second attempt got feedback from first
        assert len(backend.attempts) == 2
        second_prompt = backend.attempts[1].prompt
        assert "FAIL" in second_prompt or "failed" in second_prompt.lower()
        ledger.close()

    def test_loop_feedback_contains_failed_criteria(self, tmp_path, fixture_dir):
        """Retry prompt should include specific failed criteria from last attempt."""
        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "def add(a, b): return a + b\n"},
                {"src/calc.py": CALC_PY},
            ],
        )
        ws = create_mission(
            mission_id="e2e-feedback",
            goal="Write calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())

        run_loop(
            mission_id="e2e-feedback",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=5,
            max_cost=10.0,
            timeout=3600,
        )

        if len(backend.attempts) >= 2:
            prompt = backend.attempts[1].prompt
            # Should contain retry-specific content
            assert "Retry" in prompt or "retry" in prompt or "Must Fix" in prompt

    def test_circuit_breaker_exits_with_resource_limit(self, tmp_path, fixture_dir):
        backend = MockBackend(
            simulate_files={"src/calc.py": "# nothing\n"},
        )
        ws = create_mission(
            mission_id="e2e-breaker",
            goal="Write calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
        )
        verifier = Verifier(backend=MockCriticBackend())

        outcome = run_loop(
            mission_id="e2e-breaker",
            workdir=ws,
            backend=backend,
            verifier=verifier,
            max_iterations=2,
            max_cost=100.0,
            timeout=3600,
        )

        assert outcome == "resource_limit"
        ledger = Ledger(ws / "mission.db")
        assert ledger.get_mission("e2e-breaker")["total_attempts"] == 2
        ledger.close()


class TestE2EMultiAgent:
    def test_two_agents_independent_groups(self, tmp_path, fixture_dir):
        """Two agents work on independent groups, mission completes."""
        backend = MockBackend(simulate_files={"src/calc.py": CALC_PY})
        ws = create_mission(
            mission_id="e2e-multi",
            goal="Write calculator functions",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
            agents=2,
        )

        # Fix verify.sh for macOS (python3 not python)
        verify_sh = ws / "verify.sh"
        content = verify_sh.read_text().replace("python -m", "python3 -m")
        verify_sh.write_text(content)
        # Re-commit the fix
        subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "fix verify.sh"], cwd=ws, capture_output=True
        )

        verifier = Verifier(backend=MockCriticBackend())

        outcome = run_multi_agent(
            mission_id="e2e-multi",
            mission_dir=ws,
            n_agents=2,
            backend=backend,
            verifier=verifier,
            max_iterations=10,
            max_cost=50.0,
            timeout=3600,
        )

        assert outcome == "completed"
        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission("e2e-multi")
        assert mission["status"] == "completed"
        assert mission["total_attempts"] >= 1
        ledger.close()
