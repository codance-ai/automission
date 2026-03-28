"""Atomic merge protocol: lock, rebase, regression verify, fast-forward."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from automission.db import Ledger
from automission.models import MergeResult
from automission.verifier import run_verify_sh
from automission.worktree import sync_from_main

logger = logging.getLogger(__name__)


def atomic_merge(
    worktree_dir: Path,
    mission_dir: Path,
    agent_id: str,
    ledger: Ledger,
) -> MergeResult:
    """Atomically merge an agent's worktree branch into main.

    Steps:
    1. Acquire merge lock (prevents concurrent merges)
    2. Rebase onto main (catch conflicts early)
    3. Regression verify via verify.sh (never poison main)
    4. Fast-forward main via ``git merge --ff-only {branch}`` in mission_dir
    5. Return commit hash

    The merge lock is always released in a finally block.

    The original spec suggested ``git push . {branch}:main`` from the worktree,
    but that hits ``receive.denyCurrentBranch`` because mission_dir has main
    checked out.  ``git merge --ff-only`` from mission_dir achieves the same
    atomic fast-forward and updates the working tree in one step.
    """
    branch = f"{agent_id}-work"

    # Step 1: Acquire merge lock
    if not ledger.acquire_merge_lock(agent_id):
        return MergeResult(
            success=False,
            rejected_reason="Could not acquire merge lock",
        )

    try:
        # Step 2: Rebase onto main
        if not sync_from_main(worktree_dir):
            return MergeResult(
                success=False,
                rejected_reason="Rebase conflict",
            )

        # Step 3: Regression verify
        verify_sh = worktree_dir / "verify.sh"
        if verify_sh.exists():
            gate_result = run_verify_sh(worktree_dir, verify_sh)
            if not gate_result["passed"]:
                return MergeResult(
                    success=False,
                    rejected_reason="Regression verification failed",
                )

        # Step 4: Fast-forward main
        ff_result = subprocess.run(
            ["git", "merge", "--ff-only", branch],
            cwd=mission_dir,
            capture_output=True,
        )
        if ff_result.returncode != 0:
            stderr = ff_result.stderr.decode(errors="replace")
            logger.error("Fast-forward merge failed in %s: %s", mission_dir, stderr)
            return MergeResult(
                success=False,
                rejected_reason=f"Fast-forward failed: {stderr.strip()}",
            )

        # Step 5: Get commit hash
        hash_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=mission_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        commit_hash = hash_result.stdout.strip()

        logger.info("Atomic merge succeeded for %s: %s", agent_id, commit_hash)
        return MergeResult(success=True, commit_hash=commit_hash)

    finally:
        # Always release the merge lock
        ledger.release_merge_lock(agent_id)
