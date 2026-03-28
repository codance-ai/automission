"""Tests for git worktree lifecycle management."""

import subprocess

import pytest


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=repo, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=repo, capture_output=True
    )
    (repo / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True
    )
    return repo


class TestCreateAgentWorktree:
    def test_creates_worktree_directory(self, git_repo):
        from automission.worktree import create_agent_worktree

        wt_path = create_agent_worktree(git_repo, "agent-1")
        assert wt_path.exists()
        assert (wt_path / "README.md").exists()

    def test_creates_unique_branch(self, git_repo):
        from automission.worktree import create_agent_worktree

        wt_path = create_agent_worktree(git_repo, "agent-1")
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "agent-1-work"

    def test_two_agents_different_worktrees(self, git_repo):
        from automission.worktree import create_agent_worktree

        wt1 = create_agent_worktree(git_repo, "agent-1")
        wt2 = create_agent_worktree(git_repo, "agent-2")
        assert wt1 != wt2
        assert wt1.exists()
        assert wt2.exists()


class TestSyncFromMain:
    def test_sync_picks_up_new_commits(self, git_repo):
        from automission.worktree import create_agent_worktree, sync_from_main

        wt_path = create_agent_worktree(git_repo, "agent-1")

        # Add a new commit on main
        (git_repo / "new_file.txt").write_text("new content\n")
        subprocess.run(
            ["git", "add", "-A"], cwd=git_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add new file"],
            cwd=git_repo,
            capture_output=True,
            check=True,
        )

        # Sync worktree
        assert sync_from_main(wt_path) is True
        assert (wt_path / "new_file.txt").exists()


class TestCleanupWorktree:
    def test_removes_worktree(self, git_repo):
        from automission.worktree import cleanup_worktree, create_agent_worktree

        wt_path = create_agent_worktree(git_repo, "agent-1")
        assert wt_path.exists()

        cleanup_worktree(git_repo, "agent-1")
        assert not wt_path.exists()
