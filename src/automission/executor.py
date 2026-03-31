"""Standalone mission executor process.

Runs as a separate OS process, manages its own PID file, registers in the DB,
runs a heartbeat thread, and emits events to the workspace events.jsonl file.

Usage:
    python -m automission.executor <workspace_dir> <mission_id>
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys
import threading
import uuid
from pathlib import Path
from typing import Callable

from automission import DEFAULT_DOCKER_IMAGE
from automission.db import Ledger
from automission.events import EventWriter
from automission.mission_log import MissionLogger
from automission.models import MissionOutcome

logger = logging.getLogger(__name__)

EXECUTOR_HEARTBEAT_INTERVAL = 30  # seconds


def reconcile_stale_state(workspace_dir: Path, mission_id: str) -> None:
    """Clean up from a crashed executor.

    Performs the following recovery actions:
    - Force-release stuck merge lock
    - Expire all active claims
    - Clear previous executor runtime entry
    - Ensure mission status is "running"
    - Clean up stale worktrees (any dirs in workspace_dir/worktrees/)
    """
    logger.info(
        "Reconciling stale state for mission %s in %s", mission_id, workspace_dir
    )

    with Ledger(workspace_dir / "mission.db") as ledger:
        # 1. Force-release merge lock
        ledger.force_release_merge_lock()
        logger.info("Force-released merge lock")

        # 2. Expire all active claims
        expired = ledger.expire_all_active_claims(mission_id)
        logger.info("Expired %d stale claims", expired)

        # 3. Clear previous executor runtime entry
        ledger.clear_executor_runtime(mission_id)
        logger.info("Cleared executor runtime entry")

        # 4. Ensure mission status is "running"
        ledger.update_mission_status(mission_id, "running")
        logger.info("Reset mission status to 'running'")

    # 5. Clean up stale agent workspaces
    worktrees_dir = workspace_dir / "worktrees"
    if worktrees_dir.exists():
        for wt_path in worktrees_dir.iterdir():
            if wt_path.is_dir():
                try:
                    shutil.rmtree(wt_path)
                    logger.info("Removed stale agent workspace: %s", wt_path)
                except Exception as exc:
                    logger.warning(
                        "Failed to remove agent workspace %s: %s", wt_path, exc
                    )


def _heartbeat_loop(
    workspace_dir: Path,
    mission_id: str,
    executor_id: str,
    stop_event: threading.Event,
) -> None:
    """Background thread: update executor heartbeat every EXECUTOR_HEARTBEAT_INTERVAL seconds."""
    while not stop_event.wait(EXECUTOR_HEARTBEAT_INTERVAL):
        try:
            with Ledger(workspace_dir / "mission.db") as ledger:
                ledger.update_executor_heartbeat(mission_id, executor_id)
            logger.debug("Heartbeat updated for executor %s", executor_id)
        except Exception as exc:
            logger.warning("Heartbeat update failed: %s", exc)


def _execute_mission(
    workspace_dir: Path,
    mission_id: str,
    event_writer: EventWriter,
    cancel_flag: Callable[[], bool],
    mission_logger: "MissionLogger | None" = None,
) -> str:
    """Run the actual mission work (single-agent or multi-agent).

    Returns a MissionOutcome string.
    """
    from automission.backend.claude import ClaudeCodeBackend
    from automission.critic import Critic
    from automission.harness import Harness
    from automission.models import MissionOutcome
    from automission.orchestrator import run_multi_agent
    from automission.structured_output.factory import create_structured_backend

    # Read mission config from DB
    with Ledger(workspace_dir / "mission.db") as ledger:
        mission = ledger.get_mission(mission_id)

    if mission is None:
        logger.error("Mission %s not found in DB", mission_id)
        return MissionOutcome.FAILED

    backend_name = mission.get("backend", "claude")
    model = mission.get("model", "claude-sonnet-4-6")
    backend_auth = mission.get("backend_auth", "api_key")
    verifier_backend_name = mission.get("verifier_backend", "claude")
    verifier_model = mission.get("verifier_model", "claude-sonnet-4-6")
    verifier_auth = mission.get("verifier_auth", "api_key")
    docker_image = mission.get("docker_image", DEFAULT_DOCKER_IMAGE)
    agents = mission.get("agents", 1)
    max_iterations = mission.get("max_iterations", 20)
    max_cost = mission.get("max_cost", 10.0)
    timeout = mission.get("timeout", 3600)

    # Create backend
    if backend_name == "claude":
        agent_backend = ClaudeCodeBackend(
            docker_image=docker_image, model=model, auth_method=backend_auth
        )
    elif backend_name == "codex":
        from automission.backend.codex import CodexBackend

        agent_backend = CodexBackend(
            docker_image=docker_image, model=model, auth_method=backend_auth
        )
    elif backend_name == "gemini":
        from automission.backend.gemini import GeminiBackend

        agent_backend = GeminiBackend(
            docker_image=docker_image, model=model, auth_method=backend_auth
        )
    else:
        logger.error("Unsupported backend: %s", backend_name)
        return MissionOutcome.FAILED

    # Create harness + critic
    harness = Harness(docker_image=docker_image)
    so_backend = create_structured_backend(
        verifier_backend_name, docker_image=docker_image, auth_method=verifier_auth
    )
    critic = Critic(backend=so_backend, model=verifier_model)

    # Build combined cancel flag: check cancel_event AND desired_state=="stopping" in DB
    def _combined_cancel() -> bool:
        if cancel_flag():
            return True
        try:
            with Ledger(workspace_dir / "mission.db") as ledger:
                rt = ledger.get_executor_runtime(mission_id)
                if rt and rt.get("desired_state") == "stopping":
                    return True
        except Exception:
            pass
        return False

    # Log acceptance plan to mission log
    if mission_logger:
        with Ledger(workspace_dir / "mission.db") as ledger:
            acceptance_groups = ledger.get_acceptance_groups(mission_id)
        plan_groups = []
        for g in acceptance_groups:
            plan_groups.append(
                {
                    "name": g.id,
                    "title": g.name,
                    "depends": g.depends_on if g.depends_on else None,
                    "criteria": [c.text for c in g.criteria],
                }
            )
        mission_logger.plan(groups=plan_groups, duration_s=0.0)

    if agents > 1:
        outcome = run_multi_agent(
            mission_id=mission_id,
            mission_dir=workspace_dir,
            n_agents=agents,
            backend=agent_backend,
            harness=harness,
            critic=critic,
            max_iterations=max_iterations,
            max_cost=max_cost,
            timeout=timeout,
            cancel_flag=_combined_cancel,
            event_writer=event_writer,
            mission_logger=mission_logger,
        )
    else:
        outcome = _run_single_agent_frontier(
            mission_id=mission_id,
            ws=workspace_dir,
            backend=agent_backend,
            harness=harness,
            critic=critic,
            max_iterations=max_iterations,
            max_cost=max_cost,
            timeout=timeout,
            cancel_flag=_combined_cancel,
            event_writer=event_writer,
            mission_logger=mission_logger,
        )

    return outcome


def _run_single_agent_frontier(
    mission_id: str,
    ws: Path,
    backend,
    harness,
    critic,
    max_iterations: int,
    max_cost: float,
    timeout: int,
    cancel_flag: Callable[[], bool],
    event_writer: EventWriter,
    mission_logger: "MissionLogger | None" = None,
) -> str:
    """Single-agent frontier loop: work on frontier groups one at a time.

    Emits group_start and group_completed events for each group.
    """
    from automission.loop import run_loop

    failed_groups: set[str] = set()

    with Ledger(ws / "mission.db") as ledger:
        while True:
            if cancel_flag():
                ledger.update_mission_status(mission_id, MissionOutcome.CANCELLED)
                return MissionOutcome.CANCELLED

            # Check resource limits
            mission = ledger.get_mission(mission_id)
            if mission is None:
                return MissionOutcome.FAILED
            if mission["total_attempts"] >= max_iterations:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                return MissionOutcome.RESOURCE_LIMIT
            if mission["total_cost"] >= max_cost:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                return MissionOutcome.RESOURCE_LIMIT

            # Compute frontier (excludes completed and claimed)
            frontier_dicts = ledger.get_frontier_groups(mission_id)
            if not frontier_dicts:
                # No frontier = all groups done or blocked
                groups = ledger.get_acceptance_groups(mission_id)
                all_done = bool(groups) and all(
                    ledger.is_group_completed(g.id) for g in groups
                )
                if all_done:
                    # Final deterministic gate: run full verification before completing
                    verify_sh = ws / "verify.sh"
                    harness_result = harness.run(
                        ws, verify_sh if verify_sh.exists() else None
                    )
                    if harness_result.passed:
                        ledger.update_mission_status(
                            mission_id, MissionOutcome.COMPLETED
                        )
                        return MissionOutcome.COMPLETED
                    # Advisory was wrong — not actually complete
                    logger.warning(
                        "All groups marked complete by advisory, "
                        "but verify.sh failed — mission failed"
                    )
                    ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                    return MissionOutcome.FAILED
                # Blocked (shouldn't happen in valid DAG)
                ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                return MissionOutcome.FAILED

            # Get full AcceptanceGroup objects for the frontier, skip failed
            all_groups = ledger.get_acceptance_groups(mission_id)
            frontier_ids = {g["id"] for g in frontier_dicts}
            target_groups = [
                g
                for g in all_groups
                if g.id in frontier_ids and g.id not in failed_groups
            ]

            if not target_groups:
                # All frontier groups have failed — mission fails
                ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                return MissionOutcome.FAILED

            # Budget remaining iterations across frontier groups
            remaining_iters = max_iterations - mission["total_attempts"]
            per_group_iters = max(1, remaining_iters // len(target_groups))

            logger.info(
                "Frontier: %s (budget %d iters each)",
                [g.id for g in target_groups],
                per_group_iters,
            )

            # Work on the first available frontier group
            current_group = target_groups[0]

            event_writer.emit(
                "group_start",
                {"group_id": current_group.id, "group_name": current_group.name},
            )

            loop_result = run_loop(
                mission_id=mission_id,
                workdir=ws,
                backend=backend,
                harness=harness,
                critic=critic,
                max_iterations=mission["total_attempts"] + per_group_iters,
                max_cost=max_cost,
                timeout=timeout,
                cancel_flag=cancel_flag,
                target_groups=[current_group],
                event_writer=event_writer,
                mission_logger=mission_logger,
            )

            if loop_result.outcome == MissionOutcome.COMPLETED:
                # Target group done — AUTHORITATIVE write to DB
                ledger.update_group_status(current_group.id, completed=True)

                # Bulk-mark other groups the critic confirmed as complete.
                # Only trusted when verify.sh (harness) also passed.
                vr = loop_result.last_verification
                if vr and vr.harness.passed:
                    known_ids = {g.id for g in all_groups}
                    for gid, done in vr.group_analysis.items():
                        if done and gid != current_group.id and gid in known_ids:
                            if not ledger.is_group_completed(gid):
                                ledger.update_group_status(gid, completed=True)
                                logger.info(
                                    "Bulk-marked group %s as completed "
                                    "(critic confirmed, verify.sh passed)",
                                    gid,
                                )

                event_writer.emit(
                    "group_completed",
                    {"group_id": current_group.id, "group_name": current_group.name},
                )
                # Check if ALL groups done (target group passed, but others may remain)
                groups = ledger.get_acceptance_groups(mission_id)
                all_done = bool(groups) and all(
                    ledger.is_group_completed(g.id) for g in groups
                )
                if all_done:
                    # Final deterministic gate: run full verification before completing
                    verify_sh = ws / "verify.sh"
                    harness_result = harness.run(
                        ws, verify_sh if verify_sh.exists() else None
                    )
                    if harness_result.passed:
                        ledger.update_mission_status(
                            mission_id, MissionOutcome.COMPLETED
                        )
                        return MissionOutcome.COMPLETED
                    # Advisory was wrong — not actually complete
                    logger.warning(
                        "All groups marked complete by advisory, "
                        "but verify.sh failed — mission failed"
                    )
                    ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                    return MissionOutcome.FAILED
                # Target group done, new frontier may have opened — loop
                continue

            if loop_result.outcome == MissionOutcome.CANCELLED:
                return MissionOutcome.CANCELLED

            if loop_result.outcome == MissionOutcome.RESOURCE_LIMIT:
                return MissionOutcome.RESOURCE_LIMIT

            # Stall/failure on this group — skip it and try next
            failed_groups.add(current_group.id)
            logger.info("Group %s failed, skipping", current_group.id)


def run_executor(workspace_dir: Path, mission_id: str) -> None:
    """Main entry point for the executor process.

    - Writes PID to workspace_dir/mission.pid
    - Registers in executor_runtime table
    - Starts heartbeat thread (every EXECUTOR_HEARTBEAT_INTERVAL seconds)
    - Emits "mission_started" event
    - Calls _execute_mission() for actual work
    - Emits terminal event (mission_completed/mission_failed/executor_shutdown)
    - Cleans up: stop heartbeat, remove PID file, clear runtime entry
    """
    workspace_dir = Path(workspace_dir)
    pid_file = workspace_dir / "mission.pid"
    events_file = workspace_dir / "events.jsonl"
    executor_id = uuid.uuid4().hex[:12]

    # Set up signal handling
    cancel_event = threading.Event()

    def _signal_handler(signum, frame):
        logger.info("Signal %d received — setting cancel flag", signum)
        cancel_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Write PID file
    pid_file.write_text(str(os.getpid()))

    # Heartbeat stop event
    heartbeat_stop = threading.Event()
    heartbeat_thread = None

    mission_log_path = workspace_dir / "mission.log"
    with (
        EventWriter(events_file) as event_writer,
        MissionLogger(mission_log_path) as mission_logger,
    ):
        try:
            # Register in DB
            with Ledger(workspace_dir / "mission.db") as ledger:
                ledger.register_executor(mission_id, executor_id, os.getpid())
                mission = ledger.get_mission(mission_id)

            # Write mission log header
            if mission:
                mission_logger.header(
                    mission_id=mission_id,
                    backend=mission.get("backend", "claude"),
                    model=mission.get("model", "claude-sonnet-4-6"),
                    docker_image=mission.get("docker_image", DEFAULT_DOCKER_IMAGE),
                    agents=mission.get("agents", 1),
                    max_attempts=mission.get("max_iterations", 20),
                    max_cost=mission.get("max_cost", 10.0),
                    timeout=mission.get("timeout", 3600),
                )

            # Start heartbeat thread
            heartbeat_thread = threading.Thread(
                target=_heartbeat_loop,
                args=(workspace_dir, mission_id, executor_id, heartbeat_stop),
                daemon=True,
                name=f"executor-heartbeat-{executor_id}",
            )
            heartbeat_thread.start()

            # Emit mission_started
            agents = mission.get("agents", 1) if mission else 1
            event_writer.emit(
                "mission_started",
                {
                    "mission_id": mission_id,
                    "executor_id": executor_id,
                    "agents": agents,
                },
            )

            # Run the mission
            outcome = _execute_mission(
                workspace_dir,
                mission_id,
                event_writer,
                cancel_event.is_set,
                mission_logger=mission_logger,
            )

            # Write mission log footer
            with Ledger(workspace_dir / "mission.db") as ledger:
                final_mission = ledger.get_mission(mission_id)
                groups = ledger.get_acceptance_groups(mission_id)
                group_statuses = {
                    g.name: ledger.is_group_completed(g.id) for g in groups
                }
                age_s = ledger.get_mission_age_s(mission_id) or 0.0
            if final_mission:
                mission_logger.footer(
                    outcome=outcome,
                    total_attempts=final_mission["total_attempts"],
                    total_cost=final_mission["total_cost"],
                    total_duration_s=age_s,
                    group_statuses=group_statuses,
                )

            # Emit terminal event based on outcome
            if outcome == MissionOutcome.COMPLETED:
                with Ledger(workspace_dir / "mission.db") as ledger:
                    ledger.update_mission_status(mission_id, MissionOutcome.COMPLETED)
                    mission = ledger.get_mission(mission_id)
                event_writer.emit(
                    "mission_completed",
                    {
                        "mission_id": mission_id,
                        "outcome": outcome,
                        "total_attempts": mission["total_attempts"] if mission else 0,
                    },
                )
            elif outcome == MissionOutcome.FAILED:
                with Ledger(workspace_dir / "mission.db") as ledger:
                    ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                    mission = ledger.get_mission(mission_id)
                event_writer.emit(
                    "mission_failed",
                    {
                        "mission_id": mission_id,
                        "outcome": outcome,
                        "total_attempts": mission["total_attempts"] if mission else 0,
                    },
                )
            elif outcome == MissionOutcome.CANCELLED:
                with Ledger(workspace_dir / "mission.db") as ledger:
                    ledger.update_mission_status(mission_id, MissionOutcome.CANCELLED)
                    mission = ledger.get_mission(mission_id)
                event_writer.emit(
                    "mission_failed",
                    {
                        "mission_id": mission_id,
                        "outcome": outcome,
                        "total_attempts": mission["total_attempts"] if mission else 0,
                    },
                )
            elif outcome == MissionOutcome.RESOURCE_LIMIT:
                with Ledger(workspace_dir / "mission.db") as ledger:
                    ledger.update_mission_status(
                        mission_id, MissionOutcome.RESOURCE_LIMIT
                    )
                    mission = ledger.get_mission(mission_id)
                event_writer.emit(
                    "mission_failed",
                    {
                        "mission_id": mission_id,
                        "outcome": outcome,
                        "total_attempts": mission["total_attempts"] if mission else 0,
                    },
                )
            else:
                # Unknown outcome — treat as failure
                with Ledger(workspace_dir / "mission.db") as ledger:
                    ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                    mission = ledger.get_mission(mission_id)
                event_writer.emit(
                    "mission_failed",
                    {
                        "mission_id": mission_id,
                        "outcome": str(outcome),
                        "total_attempts": mission["total_attempts"] if mission else 0,
                    },
                )

        except Exception as exc:
            logger.exception("Executor crashed: %s", exc)
            try:
                event_writer.emit(
                    "executor_shutdown", {"mission_id": mission_id, "error": str(exc)}
                )
            except Exception:
                logger.exception("Failed to emit executor_shutdown event")

        finally:
            # Stop heartbeat thread
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                try:
                    heartbeat_thread.join(timeout=5)
                except Exception:
                    logger.warning("Failed to join heartbeat thread")

            # Remove PID file
            try:
                pid_file.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Failed to remove PID file: %s", exc)

            # Clear runtime entry
            try:
                with Ledger(workspace_dir / "mission.db") as ledger:
                    ledger.clear_executor_runtime(mission_id)
            except Exception as exc:
                logger.warning("Failed to clear executor runtime: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) != 3:
        print(
            "Usage: python -m automission.executor <workspace_dir> <mission_id>",
            file=sys.stderr,
        )
        sys.exit(1)

    workspace_dir = Path(sys.argv[1])
    mission_id = sys.argv[2]

    if not workspace_dir.exists():
        print(
            f"Error: workspace directory does not exist: {workspace_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    db_path = workspace_dir / "mission.db"
    if not db_path.exists():
        print(
            f"Error: mission.db not found in workspace: {workspace_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    run_executor(workspace_dir, mission_id)
