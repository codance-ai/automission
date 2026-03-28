"""Git worktree lifecycle management for multi-agent isolation."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def create_agent_worktree(mission_dir: Path, agent_id: str) -> Path:
    """Create a git worktree for an agent.

    Creates branch ``{agent_id}-work`` from HEAD (ignores error if it already
    exists) and adds a worktree at ``mission_dir / "worktrees" / agent_id``
    checked out on that branch.

    Returns the worktree path.
    """
    branch = f"{agent_id}-work"
    worktree_path = mission_dir / "worktrees" / agent_id

    # Create branch from HEAD — ignore "already exists" errors
    subprocess.run(
        ["git", "branch", branch],
        cwd=mission_dir,
        capture_output=True,
    )

    # Add worktree
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), branch],
        cwd=mission_dir,
        capture_output=True,
        check=True,
    )

    logger.info("Created worktree for %s at %s", agent_id, worktree_path)
    return worktree_path


def sync_from_main(worktree_dir: Path) -> bool:
    """Rebase the worktree branch onto main.

    Returns True on success.  If the rebase fails (e.g. conflicts), aborts it
    and returns False.
    """
    result = subprocess.run(
        ["git", "rebase", "main"],
        cwd=worktree_dir,
        capture_output=True,
    )

    if result.returncode != 0:
        logger.warning(
            "Rebase failed in %s, aborting: %s",
            worktree_dir,
            result.stderr.decode(errors="replace"),
        )
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=worktree_dir,
            capture_output=True,
        )
        return False

    logger.info("Synced worktree %s from main", worktree_dir)
    return True


def cleanup_worktree(mission_dir: Path, agent_id: str) -> None:
    """Remove an agent's worktree and delete its branch."""
    worktree_path = mission_dir / "worktrees" / agent_id
    branch = f"{agent_id}-work"

    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=mission_dir,
        capture_output=True,
        check=True,
    )
    logger.info("Removed worktree at %s", worktree_path)

    subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=mission_dir,
        capture_output=True,
        check=True,
    )
    logger.info("Deleted branch %s", branch)
