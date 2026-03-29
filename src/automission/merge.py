"""Atomic merge protocol: lock, rebase, regression verify, fast-forward."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from automission.db import Ledger
from automission.models import MergeResult
from automission.harness import run_verify_sh
from automission.worktree import sync_from_main

logger = logging.getLogger(__name__)


def atomic_merge(
    worktree_dir: Path,
    mission_dir: Path,
    agent_id: str,
    ledger: Ledger,
) -> MergeResult:
    """Atomically merge an agent's clone workspace into main.

    Steps:
    1. Acquire merge lock (prevents concurrent merges)
    2. Rebase onto main (catch conflicts early)
    3. Regression verify via verify.sh (never poison main)
    4. Fetch agent's HEAD from clone, fast-forward main in mission_dir
    5. Return commit hash

    The merge lock is always released in a finally block.
    """

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

        # Step 4: Fetch agent's HEAD into mission_dir and fast-forward.
        # Read the fetched SHA explicitly to avoid FETCH_HEAD races.
        fetch_result = subprocess.run(
            ["git", "fetch", str(worktree_dir.resolve()), "HEAD"],
            cwd=mission_dir,
            capture_output=True,
        )
        if fetch_result.returncode != 0:
            stderr = fetch_result.stderr.decode(errors="replace")
            logger.error("Fetch from clone failed: %s", stderr)
            return MergeResult(
                success=False,
                rejected_reason=f"Fetch failed: {stderr.strip()}",
            )

        # Read SHA from FETCH_HEAD immediately — resilient to later git state changes
        fetch_head_file = mission_dir / ".git" / "FETCH_HEAD"
        fetch_sha = fetch_head_file.read_text().split()[0]

        ff_result = subprocess.run(
            ["git", "merge", "--ff-only", fetch_sha],
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
