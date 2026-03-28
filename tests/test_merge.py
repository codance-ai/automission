"""Tests for the atomic merge protocol."""

import subprocess

import pytest

from automission.db import Ledger
from automission.models import AcceptanceGroup, Criterion


@pytest.fixture
def mission_repo(tmp_path):
    """Create a git repo with README.md, verify.sh, a mission, and acceptance group."""
    repo = tmp_path / "mission"
    repo.mkdir()

    # Init git repo
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=repo, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=repo, capture_output=True
    )

    # Add README.md
    (repo / "README.md").write_text("# Test Mission\n")

    # Add verify.sh (exit 0)
    (repo / "verify.sh").write_text("#!/bin/bash\nexit 0\n")
    (repo / "verify.sh").chmod(0o755)

    # Commit
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True
    )

    # Create mission.db with a mission and acceptance group
    db_path = repo / "mission.db"
    ledger = Ledger(db_path)
    ledger.create_mission("m1", goal="Test mission")
    ledger.store_acceptance_groups(
        "m1",
        [
            AcceptanceGroup(
                id="g1",
                name="Core",
                criteria=[
                    Criterion(id="c1", group_id="g1", text="Tests pass"),
                ],
            ),
        ],
    )

    return repo, ledger


class TestAtomicMerge:
    def test_successful_merge(self, mission_repo):
        """Create worktree, add file, commit in worktree, merge -> success, file exists in mission_dir."""
        from automission.merge import atomic_merge
        from automission.worktree import create_agent_worktree

        mission_dir, ledger = mission_repo

        # Create worktree
        wt = create_agent_worktree(mission_dir, "agent-1")

        # Add a file and commit in worktree
        (wt / "new_feature.py").write_text("print('hello')\n")
        subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add new feature"],
            cwd=wt,
            capture_output=True,
            check=True,
        )

        # Merge
        result = atomic_merge(
            worktree_dir=wt,
            mission_dir=mission_dir,
            agent_id="agent-1",
            ledger=ledger,
        )

        assert result.success is True
        assert result.commit_hash != ""
        assert len(result.commit_hash) >= 7  # short hash at minimum
        # File should now exist in mission_dir
        assert (mission_dir / "new_feature.py").exists()

    def test_merge_lock_prevents_concurrent(self, mission_repo):
        """Pre-acquire lock with agent-2, then agent-1 tries to merge -> fails with 'lock' in reason."""
        from automission.merge import atomic_merge
        from automission.worktree import create_agent_worktree

        mission_dir, ledger = mission_repo

        # Create worktree for agent-1
        wt = create_agent_worktree(mission_dir, "agent-1")
        (wt / "feature.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "agent-1 work"],
            cwd=wt,
            capture_output=True,
            check=True,
        )

        # Pre-acquire lock with agent-2
        assert ledger.acquire_merge_lock("agent-2") is True

        # agent-1 tries to merge
        result = atomic_merge(
            worktree_dir=wt,
            mission_dir=mission_dir,
            agent_id="agent-1",
            ledger=ledger,
        )

        assert result.success is False
        assert "lock" in result.rejected_reason.lower()

        # Clean up: release lock so other tests aren't affected
        ledger.release_merge_lock("agent-2")

    def test_regression_failure_rejects_merge(self, mission_repo):
        """Override verify.sh in worktree to exit 1, commit, merge -> fails with 'regression' in reason."""
        from automission.merge import atomic_merge
        from automission.worktree import create_agent_worktree

        mission_dir, ledger = mission_repo

        # Create worktree
        wt = create_agent_worktree(mission_dir, "agent-1")

        # Override verify.sh to fail
        (wt / "verify.sh").write_text("#!/bin/bash\nexit 1\n")
        subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "break verify"],
            cwd=wt,
            capture_output=True,
            check=True,
        )

        # Merge should fail
        result = atomic_merge(
            worktree_dir=wt,
            mission_dir=mission_dir,
            agent_id="agent-1",
            ledger=ledger,
        )

        assert result.success is False
        assert "regression" in result.rejected_reason.lower()

        # Main repo verify.sh should be unchanged (still exit 0)
        content = (mission_dir / "verify.sh").read_text()
        assert "exit 0" in content
