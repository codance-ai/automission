"""Tests for the multi-agent orchestrator."""

import stat
import subprocess
import threading
from pathlib import Path

import pytest

from automission.backend.mock import MockBackend
from automission.db import Ledger
from automission.critic import Critic
from automission.harness import Harness
from automission.models import AcceptanceGroup, Criterion
from automission.orchestrator import (
    _restore_acceptance_md,
    _scope_acceptance_md,
    run_multi_agent,
)
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

# verify.sh that uses python3 (compatible with macOS where `python` may not exist)
VERIFY_SH = """\
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m pytest src/tests/test_calc.py -v --tb=short 2>&1
"""

ACCEPTANCE_MD = """\
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


@pytest.fixture
def orch_workspace(tmp_path, fixture_dir):
    """Create a mission workspace with python3-compatible verify.sh."""
    backend = MockBackend(
        result_status="completed",
        exit_code=0,
        simulate_files={"src/calc.py": CALC_PY},
    )

    # Write acceptance and verify to tmp_path so we can customize them
    acceptance_path = tmp_path / "ACCEPTANCE.md"
    acceptance_path.write_text(ACCEPTANCE_MD)

    verify_path = tmp_path / "verify.sh"
    verify_path.write_text(VERIFY_SH)
    verify_path.chmod(verify_path.stat().st_mode | stat.S_IEXEC)

    ws = create_mission(
        mission_id="orch-test",
        goal="Build calculator",
        acceptance_path=acceptance_path,
        verify_path=verify_path,
        backend=backend,
        workspace_dir=tmp_path / "ws",
        init_files_dir=fixture_dir / "workspace",
    )
    return ws, backend


class TestRunMultiAgent:
    def test_two_agents_complete_mission(self, tmp_path, fixture_dir):
        """Two agents working in parallel should complete the mission."""
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": CALC_PY},
        )

        acceptance_path = tmp_path / "ACCEPTANCE.md"
        acceptance_path.write_text(ACCEPTANCE_MD)
        verify_path = tmp_path / "verify.sh"
        verify_path.write_text(VERIFY_SH)
        verify_path.chmod(verify_path.stat().st_mode | stat.S_IEXEC)

        ws = create_mission(
            mission_id="orch-001",
            goal="Build calculator",
            acceptance_path=acceptance_path,
            verify_path=verify_path,
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
            agents=2,
        )
        harness = Harness()
        critic = Critic(backend=MockCriticBackend())

        outcome = run_multi_agent(
            mission_id="orch-001",
            mission_dir=ws,
            n_agents=2,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=20,
            max_cost=10.0,
            timeout=3600,
        )

        assert outcome == "completed"

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission("orch-001")
        assert mission["status"] == "completed"
        ledger.close()

    def test_cancel_flag_stops_all_agents(self, tmp_path, fixture_dir):
        """Cancel flag should stop all agents promptly."""
        # Backend writes bad code — verify.sh always fails
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
        )

        acceptance_path = tmp_path / "ACCEPTANCE.md"
        acceptance_path.write_text(ACCEPTANCE_MD)
        verify_path = tmp_path / "verify.sh"
        verify_path.write_text(VERIFY_SH)
        verify_path.chmod(verify_path.stat().st_mode | stat.S_IEXEC)

        ws = create_mission(
            mission_id="orch-cancel",
            goal="Build calculator",
            acceptance_path=acceptance_path,
            verify_path=verify_path,
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
            agents=2,
        )
        harness = Harness()
        critic = Critic(backend=MockCriticBackend())

        call_count = 0
        lock = threading.Lock()

        def cancel_after_a_few():
            nonlocal call_count
            with lock:
                call_count += 1
                return call_count > 4

        outcome = run_multi_agent(
            mission_id="orch-cancel",
            mission_dir=ws,
            n_agents=2,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=20,
            max_cost=10.0,
            timeout=3600,
            cancel_flag=cancel_after_a_few,
        )

        assert outcome in ("cancelled", "failed")

    def test_single_agent_completes(self, orch_workspace):
        """Single agent mode should work correctly through orchestrator."""
        ws, backend = orch_workspace
        harness = Harness()
        critic = Critic(backend=MockCriticBackend())

        outcome = run_multi_agent(
            mission_id="orch-test",
            mission_dir=ws,
            n_agents=1,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=20,
            max_cost=10.0,
            timeout=3600,
        )

        assert outcome == "completed"

    def test_worktrees_cleaned_up(self, tmp_path, fixture_dir):
        """Worktrees should be cleaned up after orchestrator finishes."""
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": CALC_PY},
        )

        acceptance_path = tmp_path / "ACCEPTANCE.md"
        acceptance_path.write_text(ACCEPTANCE_MD)
        verify_path = tmp_path / "verify.sh"
        verify_path.write_text(VERIFY_SH)
        verify_path.chmod(verify_path.stat().st_mode | stat.S_IEXEC)

        ws = create_mission(
            mission_id="orch-cleanup",
            goal="Build calculator",
            acceptance_path=acceptance_path,
            verify_path=verify_path,
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
            agents=2,
        )
        harness = Harness()
        critic = Critic(backend=MockCriticBackend())

        run_multi_agent(
            mission_id="orch-cleanup",
            mission_dir=ws,
            n_agents=2,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=20,
            max_cost=10.0,
            timeout=3600,
        )

        worktrees_dir = ws / "worktrees"
        # Worktrees directory should be empty or not exist
        if worktrees_dir.exists():
            remaining = list(worktrees_dir.iterdir())
            assert len(remaining) == 0, f"Leftover worktrees: {remaining}"

    def test_resource_limit_stops_agents(self, tmp_path, fixture_dir):
        """Max iterations should stop agents."""
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
        )

        acceptance_path = tmp_path / "ACCEPTANCE.md"
        acceptance_path.write_text(ACCEPTANCE_MD)
        verify_path = tmp_path / "verify.sh"
        verify_path.write_text(VERIFY_SH)
        verify_path.chmod(verify_path.stat().st_mode | stat.S_IEXEC)

        ws = create_mission(
            mission_id="orch-limit",
            goal="Build calculator",
            acceptance_path=acceptance_path,
            verify_path=verify_path,
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
            agents=2,
            max_iterations=3,
        )
        harness = Harness()
        critic = Critic(backend=MockCriticBackend())

        outcome = run_multi_agent(
            mission_id="orch-limit",
            mission_dir=ws,
            n_agents=2,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=3,
            max_cost=10.0,
            timeout=3600,
        )

        # Should eventually stop (not hang)
        assert outcome in ("failed", "resource_limit", "cancelled")


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with an ACCEPTANCE.md committed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    original_content = ACCEPTANCE_MD
    (repo / "ACCEPTANCE.md").write_text(original_content)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return repo, original_content


class TestScopeAcceptanceMd:
    """Tests for _scope_acceptance_md and _restore_acceptance_md helpers."""

    def test_scope_writes_only_claimed_group(self, git_repo):
        """After scoping, ACCEPTANCE.md contains only the claimed group's criteria."""
        repo, original_content = git_repo
        group = AcceptanceGroup(
            id="basic_operations",
            name="Basic Operations",
            depends_on=[],
            criteria=[
                Criterion(
                    id="c1",
                    group_id="basic_operations",
                    text="add(a, b) returns the sum of a and b",
                ),
                Criterion(
                    id="c2",
                    group_id="basic_operations",
                    text="subtract(a, b) returns the difference of a and b",
                ),
            ],
        )

        _scope_acceptance_md(repo, [group])

        scoped = (repo / "ACCEPTANCE.md").read_text()
        assert "Basic Operations" in scoped
        assert "add(a, b) returns the sum of a and b" in scoped
        assert "Edge Cases" not in scoped
        assert "edge_cases" not in scoped

    def test_restore_brings_back_original(self, git_repo):
        """After restoration, ACCEPTANCE.md matches the original committed content."""
        repo, original_content = git_repo
        group = AcceptanceGroup(
            id="edge_cases",
            name="Edge Cases",
            depends_on=["basic_operations"],
            criteria=[
                Criterion(
                    id="c3",
                    group_id="edge_cases",
                    text="divide(a, 0) raises ValueError",
                ),
            ],
        )

        _scope_acceptance_md(repo, [group])
        # Verify it was scoped
        assert "Edge Cases" in (repo / "ACCEPTANCE.md").read_text()
        assert "Basic Operations" not in (repo / "ACCEPTANCE.md").read_text()

        _restore_acceptance_md(repo)

        restored = (repo / "ACCEPTANCE.md").read_text()
        assert restored == original_content

    def test_assume_unchanged_flag_set_after_scope(self, git_repo):
        """Git assume-unchanged flag is set after scoping."""
        repo, _ = git_repo
        group = AcceptanceGroup(
            id="basic_operations",
            name="Basic Operations",
            criteria=[
                Criterion(
                    id="c1",
                    group_id="basic_operations",
                    text="add(a, b) returns the sum",
                ),
            ],
        )

        _scope_acceptance_md(repo, [group])

        result = subprocess.run(
            ["git", "ls-files", "-v", "ACCEPTANCE.md"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        # 'h' prefix means assume-unchanged; 'H' means tracked normally
        assert result.stdout.startswith("h "), (
            f"Expected assume-unchanged flag 'h', got: {result.stdout!r}"
        )

    def test_assume_unchanged_flag_cleared_after_restore(self, git_repo):
        """Git assume-unchanged flag is cleared after restoration."""
        repo, _ = git_repo
        group = AcceptanceGroup(
            id="basic_operations",
            name="Basic Operations",
            criteria=[
                Criterion(
                    id="c1",
                    group_id="basic_operations",
                    text="add(a, b) returns the sum",
                ),
            ],
        )

        _scope_acceptance_md(repo, [group])
        _restore_acceptance_md(repo)

        result = subprocess.run(
            ["git", "ls-files", "-v", "ACCEPTANCE.md"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        # 'H' prefix means tracked normally (not assume-unchanged)
        assert result.stdout.startswith("H "), (
            f"Expected normal tracked flag 'H', got: {result.stdout!r}"
        )

    def test_scope_no_op_when_file_missing(self, tmp_path):
        """Scoping is a no-op when ACCEPTANCE.md does not exist."""
        group = AcceptanceGroup(
            id="basic_operations",
            name="Basic Operations",
            criteria=[],
        )
        # Should not raise
        _scope_acceptance_md(tmp_path, [group])
        assert not (tmp_path / "ACCEPTANCE.md").exists()
