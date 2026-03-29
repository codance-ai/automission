"""Agent workspace lifecycle management via local clones."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def create_agent_worktree(mission_dir: Path, agent_id: str) -> Path:
    """Create an isolated workspace for an agent via git clone --local.

    Clones ``mission_dir`` to ``mission_dir / "worktrees" / agent_id``,
    then checks out a new branch ``{agent_id}-work``.

    The clone is self-contained (real .git directory) and can be safely
    mounted into Docker containers.

    Returns the workspace path.
    """
    workspace_path = mission_dir / "worktrees" / agent_id

    # Remove stale workspace from previous run (defensive)
    if workspace_path.exists():
        shutil.rmtree(workspace_path)

    # Clone mission_dir → workspace (hardlinks objects, fast)
    subprocess.run(
        ["git", "clone", "--local", str(mission_dir), str(workspace_path)],
        capture_output=True,
        check=True,
    )

    # Create and checkout agent work branch
    subprocess.run(
        ["git", "checkout", "-b", f"{agent_id}-work"],
        cwd=workspace_path,
        capture_output=True,
        check=True,
    )

    logger.info("Created agent workspace for %s at %s", agent_id, workspace_path)
    return workspace_path


def sync_from_main(worktree_dir: Path) -> bool:
    """Fetch latest main from origin and rebase onto it.

    Returns True on success. If the rebase fails (e.g. conflicts), aborts
    and returns False.
    """
    # Fetch latest main from origin (= mission_dir)
    fetch = subprocess.run(
        ["git", "fetch", "origin", "main"],
        cwd=worktree_dir,
        capture_output=True,
    )
    if fetch.returncode != 0:
        logger.warning(
            "Fetch failed in %s: %s",
            worktree_dir,
            fetch.stderr.decode(errors="replace"),
        )
        return False

    result = subprocess.run(
        ["git", "rebase", "origin/main"],
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

    logger.info("Synced workspace %s from origin/main", worktree_dir)
    return True


def cleanup_worktree(mission_dir: Path, agent_id: str) -> None:
    """Remove an agent's workspace directory."""
    workspace_path = mission_dir / "worktrees" / agent_id
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
        logger.info("Removed agent workspace at %s", workspace_path)
