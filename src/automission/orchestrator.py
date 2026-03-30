"""Multi-agent orchestrator — thread coordination with claims and merge."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from automission.backend.protocol import AgentBackend
from automission.db import Ledger
from automission.events import EventWriter
from automission.loop import run_loop
from automission.merge import atomic_merge
from automission.models import MissionOutcome
from automission.critic import Critic
from automission.harness import Harness, run_verify_sh
from automission.worktree import cleanup_worktree, create_agent_worktree, sync_from_main

logger = logging.getLogger(__name__)

# Heartbeat interval in seconds
_HEARTBEAT_INTERVAL = 30
# Claim expiry in seconds (must be > heartbeat interval)
_CLAIM_EXPIRES_S = 120


def run_multi_agent(
    mission_id: str,
    mission_dir: Path,
    n_agents: int,
    backend: AgentBackend,
    harness: Harness,
    critic: Critic,
    max_iterations: int = 20,
    max_cost: float = 10.0,
    timeout: int = 3600,
    cancel_flag: Callable[[], bool] | None = None,
    event_writer: "EventWriter | None" = None,
) -> str:
    """Orchestrate multiple agents working on a mission in parallel.

    Creates git worktrees for each agent, spawns one thread per agent,
    coordinates work through SQLite claims, and merges results into main.

    Returns a MissionOutcome string: "completed", "failed", "cancelled",
    or "resource_limit".
    """
    if cancel_flag is None:
        cancel_flag = lambda: False  # noqa: E731

    # Shared cancel event: any thread can signal all threads to stop
    cancel_event = threading.Event()

    # Wrap the user cancel flag + our event into one callable
    def _is_cancelled() -> bool:
        return cancel_event.is_set() or cancel_flag()

    # Create worktrees and spawn threads
    threads: list[threading.Thread] = []
    agent_ids: list[str] = []

    for i in range(n_agents):
        agent_id = f"agent-{i + 1}"
        agent_ids.append(agent_id)

    # Create all worktrees first
    worktree_paths: dict[str, Path] = {}
    for agent_id in agent_ids:
        try:
            wt = create_agent_worktree(mission_dir, agent_id)
            worktree_paths[agent_id] = wt
        except Exception:
            logger.error("Failed to create worktree for %s", agent_id, exc_info=True)

    # Spawn agent threads
    for agent_id in agent_ids:
        if agent_id not in worktree_paths:
            continue
        t = threading.Thread(
            target=_agent_worker,
            args=(
                mission_id,
                mission_dir,
                worktree_paths[agent_id],
                agent_id,
                backend,
                harness,
                critic,
                max_iterations,
                max_cost,
                timeout,
                _is_cancelled,
                cancel_event,
                event_writer,
            ),
            name=f"agent-worker-{agent_id}",
            daemon=True,
        )
        threads.append(t)

    # Start all threads
    for t in threads:
        t.start()

    # Wait for all threads to complete
    for t in threads:
        t.join()

    # Cleanup worktrees
    for agent_id in agent_ids:
        if agent_id in worktree_paths:
            try:
                cleanup_worktree(mission_dir, agent_id)
            except Exception:
                logger.warning(
                    "Failed to cleanup worktree for %s", agent_id, exc_info=True
                )

    # Determine final mission outcome
    ledger = Ledger(mission_dir / "mission.db")
    try:
        mission = ledger.get_mission(mission_id)
        if mission is None:
            return MissionOutcome.FAILED

        # Check if all groups are completed
        groups = ledger.get_acceptance_groups(mission_id)
        all_completed = bool(groups) and all(
            ledger.is_group_completed(g.id) for g in groups
        )

        if all_completed:
            # Final deterministic gate: verify.sh must pass before declaring success.
            # Group completion is based on critic advisory; verify.sh is ground truth.
            verify_sh = mission_dir / "verify.sh"
            if verify_sh.exists():
                gate = run_verify_sh(mission_dir, verify_sh)
                if not gate["passed"]:
                    logger.warning(
                        "All groups marked complete by advisory, "
                        "but verify.sh failed — mission failed"
                    )
                    ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                    return MissionOutcome.FAILED
            ledger.update_mission_status(mission_id, MissionOutcome.COMPLETED)
            return MissionOutcome.COMPLETED

        # Check current status if already set by a thread
        current_status = mission["status"]
        if current_status == MissionOutcome.CANCELLED:
            return MissionOutcome.CANCELLED
        if current_status == MissionOutcome.RESOURCE_LIMIT:
            return MissionOutcome.RESOURCE_LIMIT

        # If cancel flag is set
        if _is_cancelled():
            ledger.update_mission_status(mission_id, MissionOutcome.CANCELLED)
            return MissionOutcome.CANCELLED

        ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
        return MissionOutcome.FAILED
    finally:
        ledger.close()


def _agent_worker(
    mission_id: str,
    mission_dir: Path,
    worktree_dir: Path,
    agent_id: str,
    backend: AgentBackend,
    harness: Harness,
    critic: Critic,
    max_iterations: int,
    max_cost: float,
    timeout: int,
    cancel_flag: Callable[[], bool],
    cancel_event: threading.Event,
    event_writer: "EventWriter | None" = None,
) -> None:
    """Worker function for a single agent thread.

    Repeatedly: claim a frontier group -> run_loop in worktree -> merge if passed.
    Each agent opens its own Ledger connection (thread-safe).
    """
    ledger = Ledger(mission_dir / "mission.db")
    try:
        while True:
            # Check cancellation
            if cancel_flag():
                logger.info("%s: cancelled, exiting", agent_id)
                break

            # Check mission status
            mission = ledger.get_mission(mission_id)
            if mission is None or mission["status"] != "running":
                logger.info(
                    "%s: mission status is %s, exiting",
                    agent_id,
                    mission["status"] if mission else "missing",
                )
                break

            # Check resource limits
            if mission["total_attempts"] >= max_iterations:
                logger.info("%s: max iterations reached, exiting", agent_id)
                break
            if mission["total_cost"] >= max_cost:
                logger.info("%s: max cost reached, exiting", agent_id)
                break

            # Expire stale claims
            expired = ledger.expire_stale_claims(mission_id)
            if expired > 0:
                logger.info("%s: expired %d stale claims", agent_id, expired)

            # Get frontier groups
            frontier = ledger.get_frontier_groups(mission_id)
            if not frontier:
                # Distinguish: all done vs waiting for deps vs deadlock
                groups = ledger.get_acceptance_groups(mission_id)
                all_done = bool(groups) and all(
                    ledger.is_group_completed(g.id) for g in groups
                )
                if all_done:
                    logger.info("%s: all groups completed, exiting", agent_id)
                    break

                if ledger.has_active_claims(mission_id):
                    # Other agents are working; deps may unlock soon
                    age_s = ledger.get_mission_age_s(mission_id)
                    if age_s is not None and age_s >= timeout:
                        logger.info(
                            "%s: mission timeout while waiting for frontier",
                            agent_id,
                        )
                        break
                    logger.debug(
                        "%s: no frontier, waiting for active claims to finish",
                        agent_id,
                    )
                    time.sleep(2)
                    continue

                # No frontier, no active claims, incomplete groups → deadlock
                logger.warning(
                    "%s: no frontier and no active claims — possible dependency deadlock",
                    agent_id,
                )
                break

            # Try to claim a group
            claimed = False
            claim_id = ""
            group_id = ""
            for group in frontier:
                claim_id = f"claim-{agent_id}-{uuid.uuid4().hex[:8]}"
                group_id = group["id"]
                if ledger.create_claim(
                    claim_id=claim_id,
                    mission_id=mission_id,
                    agent_id=agent_id,
                    group_id=group_id,
                    expires_s=_CLAIM_EXPIRES_S,
                ):
                    claimed = True
                    logger.info(
                        "%s: claimed group %s (%s)",
                        agent_id,
                        group_id,
                        group["name"],
                    )
                    break

            if not claimed:
                # All frontier groups are claimed by others; wait briefly and retry
                logger.info("%s: could not claim any group, retrying...", agent_id)
                time.sleep(1)
                continue

            # Sync workspace from main
            if not sync_from_main(worktree_dir):
                logger.warning("%s: sync from main failed, releasing claim", agent_id)
                ledger.release_claim(claim_id, "failed")
                continue

            # Start heartbeat thread
            heartbeat_stop = threading.Event()
            heartbeat_thread = threading.Thread(
                target=_heartbeat_worker,
                args=(mission_dir, claim_id, heartbeat_stop),
                name=f"heartbeat-{agent_id}",
                daemon=True,
            )
            heartbeat_thread.start()

            try:
                # Run the agent loop in the worktree
                # Use limited iterations per group claim to avoid hogging
                per_group_iterations = max(1, max_iterations // max(len(frontier), 1))
                per_group_iterations = min(per_group_iterations, max_iterations)

                # Get full AcceptanceGroup object for the claimed group
                all_groups = ledger.get_acceptance_groups(mission_id)
                claimed_group = [g for g in all_groups if g.id == group_id]

                loop_result = run_loop(
                    mission_id=mission_id,
                    workdir=worktree_dir,
                    backend=backend,
                    harness=harness,
                    critic=critic,
                    max_iterations=per_group_iterations,
                    max_cost=max_cost,
                    timeout=timeout,
                    agent_id=agent_id,
                    cancel_flag=cancel_flag,
                    mission_dir=mission_dir,
                    target_groups=claimed_group if claimed_group else None,
                    event_writer=event_writer,
                )

                logger.info(
                    "%s: run_loop returned %s for group %s",
                    agent_id,
                    loop_result.outcome,
                    group_id,
                )

                # Check if verify.sh passes in the worktree
                verify_sh = worktree_dir / "verify.sh"
                verify_passed = False
                if verify_sh.exists():
                    gate = run_verify_sh(worktree_dir, verify_sh)
                    verify_passed = gate["passed"]

                if verify_passed:
                    # Attempt atomic merge
                    merge_ledger = Ledger(mission_dir / "mission.db")
                    try:
                        merge_result = atomic_merge(
                            worktree_dir=worktree_dir,
                            mission_dir=mission_dir,
                            agent_id=agent_id,
                            ledger=merge_ledger,
                        )
                    finally:
                        merge_ledger.close()

                    if merge_result.success:
                        ledger.release_claim(claim_id, "completed")
                        ledger.update_group_status(group_id, completed=True)
                        logger.info(
                            "%s: merged group %s (commit %s)",
                            agent_id,
                            group_id,
                            merge_result.commit_hash,
                        )

                        # Bulk-mark other groups the critic confirmed as complete.
                        # Only trusted when verify.sh (harness) also passed.
                        vr = loop_result.last_verification
                        if vr and vr.harness.passed:
                            for gid, done in vr.group_analysis.items():
                                if done and gid != group_id:
                                    if not ledger.is_group_completed(gid):
                                        ledger.update_group_status(gid, completed=True)
                                        logger.info(
                                            "%s: bulk-marked group %s as completed "
                                            "(critic confirmed, verify.sh passed)",
                                            agent_id,
                                            gid,
                                        )

                        # Check if all groups are now completed
                        groups = ledger.get_acceptance_groups(mission_id)
                        all_done = bool(groups) and all(
                            ledger.is_group_completed(g.id) for g in groups
                        )
                        if all_done:
                            ledger.update_mission_status(
                                mission_id, MissionOutcome.COMPLETED
                            )
                            cancel_event.set()
                            logger.info(
                                "%s: all groups completed, mission done!",
                                agent_id,
                            )
                            break
                    else:
                        ledger.release_claim(claim_id, "failed")
                        logger.warning(
                            "%s: merge rejected for group %s: %s",
                            agent_id,
                            group_id,
                            merge_result.rejected_reason,
                        )
                else:
                    ledger.release_claim(claim_id, "failed")
                    logger.info(
                        "%s: verification failed for group %s",
                        agent_id,
                        group_id,
                    )

                # If loop hit cancellation or resource limit, propagate
                if loop_result.outcome == MissionOutcome.CANCELLED:
                    break
                if loop_result.outcome == MissionOutcome.RESOURCE_LIMIT:
                    break

            finally:
                # Stop heartbeat
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=5)

    except Exception:
        logger.error("%s: unhandled error", agent_id, exc_info=True)
    finally:
        ledger.close()


def _heartbeat_worker(
    mission_dir: Path,
    claim_id: str,
    stop_event: threading.Event,
) -> None:
    """Daemon thread that renews the claim heartbeat every 30s.

    Opens its own Ledger connection for thread safety.
    """
    ledger = Ledger(mission_dir / "mission.db")
    try:
        while not stop_event.wait(timeout=_HEARTBEAT_INTERVAL):
            try:
                ledger.renew_heartbeat(claim_id, expires_s=_CLAIM_EXPIRES_S)
            except Exception:
                logger.warning(
                    "Heartbeat renewal failed for claim %s",
                    claim_id,
                    exc_info=True,
                )
                break
    finally:
        ledger.close()
