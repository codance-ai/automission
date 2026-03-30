"""Agent loop — single iteration (M1) and full loop with circuit breakers."""

from __future__ import annotations

import json
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from automission.backend.protocol import AgentBackend
from automission.db import Ledger
from automission.events import EventWriter
from automission.models import (
    AcceptanceGroup,
    AttemptContract,
    AttemptSpec,
    LoopResult,
    MissionOutcome,
    VerificationResult,
)
from automission.critic import Critic
from automission.harness import Harness

if TYPE_CHECKING:
    from automission.mission_log import MissionLogger

logger = logging.getLogger(__name__)


# ── Public API ──


def run_single_iteration(
    mission_id: str,
    workdir: Path,
    backend: AgentBackend,
    harness: Harness,
    critic: Critic,
    agent_id: str = "agent-1",
    timeout_s: int = 300,
    mission_dir: Path | None = None,
    target_groups: list[AcceptanceGroup] | None = None,
    mission_logger: "MissionLogger | None" = None,
) -> VerificationResult:
    """Run one attempt: prompt -> execute -> commit -> verify -> record.

    M1 compatibility wrapper — delegates to _run_one_iteration().
    """
    mission_dir = mission_dir or workdir
    ledger = Ledger(mission_dir / "mission.db")
    try:
        verification = _run_one_iteration(
            mission_id=mission_id,
            workdir=workdir,
            backend=backend,
            harness=harness,
            critic=critic,
            ledger=ledger,
            agent_id=agent_id,
            timeout_s=timeout_s,
            last_verification=None,
            stall_hint=False,
            mission_dir=mission_dir,
            target_groups=target_groups,
            mission_logger=mission_logger,
        )

        # Update mission status (matches original behavior)
        if verification.mission_passed:
            ledger.update_mission_status(mission_id, "completed")
            logger.info("Mission %s completed!", mission_id)

        return verification
    finally:
        ledger.close()


def run_loop(
    mission_id: str,
    workdir: Path,
    backend: AgentBackend,
    harness: Harness,
    critic: Critic,
    max_iterations: int = 20,
    max_cost: float = 10.0,
    timeout: int = 3600,
    agent_id: str = "agent-1",
    timeout_per_attempt: int = 300,
    stall_threshold: int = 3,
    cancel_flag: Callable[[], bool] | None = None,
    mission_dir: Path | None = None,
    target_groups: list[AcceptanceGroup] | None = None,
    event_writer: "EventWriter | None" = None,
    mission_logger: "MissionLogger | None" = None,
) -> LoopResult:
    """Main agent loop with circuit breakers, stall detection, and resume.

    If target_groups is provided, the agent focuses on those groups only
    (used by orchestrator for claimed groups). The critic still evaluates
    all groups to compute group_analysis.

    Returns a LoopResult containing the MissionOutcome string and
    the last VerificationResult (for bulk group-completion marking).
    """
    mission_dir = mission_dir or workdir

    if cancel_flag is None:
        cancel_flag = lambda: False  # noqa: E731

    ledger = Ledger(mission_dir / "mission.db")
    try:
        # Resume: load last verification from ledger if exists
        last_verification = _load_last_verification(ledger, mission_id)

        while True:
            mission = ledger.get_mission(mission_id)
            if mission is None:
                logger.error("Mission %s not found", mission_id)
                return LoopResult(MissionOutcome.FAILED)

            # ── Circuit breakers ──

            # Cancel flag
            if cancel_flag():
                ledger.update_mission_status(mission_id, MissionOutcome.CANCELLED)
                logger.info("Mission %s cancelled by flag", mission_id)
                return LoopResult(MissionOutcome.CANCELLED, last_verification)

            # Max iterations
            if mission["total_attempts"] >= max_iterations:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                logger.info(
                    "Mission %s hit max iterations (%d)",
                    mission_id,
                    max_iterations,
                )
                return LoopResult(MissionOutcome.RESOURCE_LIMIT, last_verification)

            # Max cost
            if mission["total_cost"] >= max_cost:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                logger.info(
                    "Mission %s hit max cost ($%.2f)",
                    mission_id,
                    max_cost,
                )
                return LoopResult(MissionOutcome.RESOURCE_LIMIT, last_verification)

            # Timeout
            age_s = ledger.get_mission_age_s(mission_id)
            if age_s is not None and age_s >= timeout:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                logger.info("Mission %s timed out", mission_id)
                return LoopResult(MissionOutcome.RESOURCE_LIMIT, last_verification)

            # ── Stall detection (pre-iteration) ──
            stall_hint = False
            stall_count = _count_stall(ledger, mission_id)

            if stall_count >= stall_threshold * 2:
                # Too many stalls — give up
                ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                logger.info(
                    "Mission %s failed: stall count %d >= %d",
                    mission_id,
                    stall_count,
                    stall_threshold * 2,
                )
                return LoopResult(MissionOutcome.FAILED, last_verification)

            if stall_count >= stall_threshold + 1:
                # Rollback to best commit
                best = ledger.get_best_attempt(mission_id)
                if best and best.get("commit_hash"):
                    _rollback_to_best(workdir, best["commit_hash"])
                    logger.info(
                        "Mission %s: rolled back to best commit %s",
                        mission_id,
                        best["commit_hash"],
                    )

            if stall_count >= stall_threshold:
                stall_hint = True
                logger.info(
                    "Mission %s: stall detected (%d), adding strategy hint",
                    mission_id,
                    stall_count,
                )

            # ── Run one iteration ──
            verification = _run_one_iteration(
                mission_id=mission_id,
                workdir=workdir,
                backend=backend,
                harness=harness,
                critic=critic,
                ledger=ledger,
                agent_id=agent_id,
                timeout_s=timeout_per_attempt,
                last_verification=last_verification,
                stall_hint=stall_hint,
                mission_dir=mission_dir,
                target_groups=target_groups,
                event_writer=event_writer,
                mission_logger=mission_logger,
            )

            # ── Check pass ──
            if target_groups is not None:
                # Scoped mode: only check if target groups passed.
                # Don't set mission status — caller decides mission completion.
                target_ids = {g.id for g in target_groups}
                all_targets_done = all(
                    verification.group_analysis.get(gid, False) for gid in target_ids
                )
                if all_targets_done:
                    logger.info(
                        "Target groups %s all completed",
                        target_ids,
                    )
                    return LoopResult(MissionOutcome.COMPLETED, verification)
            elif verification.mission_passed:
                ledger.update_mission_status(mission_id, MissionOutcome.COMPLETED)
                logger.info("Mission %s completed!", mission_id)
                return LoopResult(MissionOutcome.COMPLETED, verification)

            # Carry verification forward for next iteration
            last_verification = verification

    finally:
        ledger.close()


# ── Internal helpers ──


def _run_one_iteration(
    mission_id: str,
    workdir: Path,
    backend: AgentBackend,
    harness: Harness,
    critic: Critic,
    ledger: Ledger,
    agent_id: str = "agent-1",
    timeout_s: int = 300,
    last_verification: VerificationResult | None = None,
    stall_hint: bool = False,
    mission_dir: Path | None = None,
    target_groups: list[AcceptanceGroup] | None = None,
    event_writer: "EventWriter | None" = None,
    mission_logger: "MissionLogger | None" = None,
) -> VerificationResult:
    """Run one attempt: prompt -> execute -> commit -> verify -> record.

    Extracted core used by both run_single_iteration() and run_loop().

    If target_groups is set, the agent prompt focuses on those groups' criteria.
    The critic still evaluates all groups for group_analysis computation.
    """
    # Clean stale lock file
    lock_file = workdir / ".git" / "index.lock"
    if lock_file.exists():
        lock_file.unlink()
        logger.warning("Removed stale .git/index.lock")

    # Determine attempt number
    last = ledger.get_last_attempt(mission_id)
    attempt_number = (last["attempt_number"] + 1) if last else 1
    attempt_id = f"{mission_id}-{attempt_number}-{uuid.uuid4().hex[:6]}"

    # Get ALL acceptance groups for critic (needs full picture)
    groups = ledger.get_acceptance_groups(mission_id)

    # Check for dirty state
    dirty_state = _get_dirty_state(workdir)

    # ── Build prompt ──
    t_prompt_start = time.monotonic()

    contract = None
    if last_verification is None:
        # First attempt
        prompt = _build_first_attempt_prompt(
            dirty_state=dirty_state,
            target_groups=target_groups,
        )
    else:
        # Retry: derive contract scoped to target groups
        contract = _derive_contract(
            last_verification, all_groups=groups, target_groups=target_groups
        )
        prompt = _build_retry_prompt(
            last_verification=last_verification,
            contract=contract,
            attempt_number=attempt_number,
            all_groups=groups,
            stall_hint=stall_hint,
            dirty_state=dirty_state,
        )

    t_prompt_end = time.monotonic()

    # ── Run attempt ──
    spec = AttemptSpec(
        attempt_id=attempt_id,
        mission_id=mission_id,
        workdir=workdir,
        prompt=prompt,
        timeout_s=timeout_s,
        output_dir=(mission_dir / "agent_outputs") if mission_dir else None,
    )
    logger.info("Running attempt %s (#%d)", attempt_id, attempt_number)
    if event_writer:
        start_data: dict[str, Any] = {
            "agent_id": agent_id,
            "attempt": attempt_number,
            "attempt_id": attempt_id,
        }
        if contract is not None:
            focus_names = [g.name for g in groups if g.id in contract.focus_groups]
            start_data["scope"] = (
                ", ".join(focus_names) if focus_names else "all groups"
            )
        event_writer.emit("attempt_start", start_data)
    if mission_logger:
        scope_str = (
            "all groups (first attempt)"
            if contract is None
            else _format_scope(contract, groups)
        )
        mission_logger.attempt_start(
            attempt_number=attempt_number,
            agent_id=agent_id,
            scope=scope_str,
        )
        mission_logger.attempt_prompt(prompt=prompt, prompt_len=len(prompt))
    attempt_result = backend.run_attempt(spec)

    # ── Auto-commit ──
    commit_hash = _git_commit_if_changed(workdir, attempt_number)

    if mission_logger:
        stdout_size = None
        if attempt_result.stdout_path and attempt_result.stdout_path.exists():
            stdout_size = attempt_result.stdout_path.stat().st_size
        mission_logger.attempt_execution(
            status=attempt_result.status,
            exit_code=attempt_result.exit_code,
            duration_s=attempt_result.duration_s,
            token_input=attempt_result.token_usage.input_tokens,
            token_output=attempt_result.token_usage.output_tokens,
            cost_usd=attempt_result.cost_usd,
            changed_files=attempt_result.changed_files,
            commit_hash=commit_hash,
            stdout_path=str(attempt_result.stdout_path)
            if attempt_result.stdout_path
            else None,
            stdout_size=stdout_size,
        )

    # ── Verify: Harness (deterministic) then Critic (LLM) ──
    verify_sh = workdir / "verify.sh"
    t_harness_start = time.monotonic()
    harness_result = harness.run(workdir, verify_sh if verify_sh.exists() else None)
    t_harness_end = time.monotonic()

    t_critic_start = time.monotonic()
    critic_result = critic.analyze(harness_result, groups)
    t_critic_end = time.monotonic()

    verification = VerificationResult(harness=harness_result, critic=critic_result)

    # ── Record to ledger ──
    ledger.record_attempt(
        attempt_id=attempt_id,
        mission_id=mission_id,
        agent_id=agent_id,
        attempt_number=attempt_number,
        status=attempt_result.status,
        exit_code=attempt_result.exit_code,
        duration_s=attempt_result.duration_s,
        cost_usd=attempt_result.cost_usd,
        token_input=attempt_result.token_usage.input_tokens,
        token_output=attempt_result.token_usage.output_tokens,
        changed_files=attempt_result.changed_files,
        verification_passed=verification.gate_passed,
        verification_result=verification.to_json(),
        commit_hash=commit_hash or "",
    )

    if event_writer:
        event_writer.emit(
            "attempt_end",
            {
                "agent_id": agent_id,
                "attempt": attempt_number,
                "status": attempt_result.status,
                "token_input": attempt_result.token_usage.input_tokens,
                "token_output": attempt_result.token_usage.output_tokens,
                "changed_files": attempt_result.changed_files,
            },
        )
        event_writer.emit(
            "verification",
            {
                "passed": verification.gate_passed,
                "summary": verification.critic.summary,
                "group_analysis": verification.group_analysis,
                "next_actions": verification.critic.next_actions,
            },
        )

    if mission_logger:
        group_names = {g.id: g.name for g in groups}
        group_statuses = {
            group_names.get(gid, gid): done
            for gid, done in verification.group_analysis.items()
        }
        mission_logger.verification(
            passed=verification.gate_passed,
            exit_code=harness_result.exit_code,
            harness_duration_s=t_harness_end - t_harness_start,
            stdout=harness_result.stdout,
            stderr=harness_result.stderr,
            critic_duration_s=t_critic_end - t_critic_start,
            critic_cost_usd=None,
            summary=critic_result.summary,
            root_cause=critic_result.root_cause,
            next_actions=critic_result.next_actions,
            group_statuses=group_statuses,
        )
        mission_logger.timing(
            prompt_s=t_prompt_end - t_prompt_start,
            agent_s=attempt_result.duration_s,
            harness_s=t_harness_end - t_harness_start,
            critic_s=t_critic_end - t_critic_start,
        )

    logger.info(
        "Attempt %d: gate %s",
        attempt_number,
        "PASS" if verification.gate_passed else "FAIL",
    )

    return verification


def _derive_contract(
    last_verification: VerificationResult,
    all_groups: list[AcceptanceGroup],
    target_groups: list[AcceptanceGroup] | None = None,
) -> AttemptContract:
    """Derive attempt contract from last verification using group statuses + critic feedback.

    Uses group_analysis from Critic (group-level completion) and next_actions for
    focused retry guidance. No criterion text matching needed.
    """
    group_analysis = last_verification.group_analysis
    groups = target_groups if target_groups is not None else all_groups

    # Focus groups: incomplete groups with deps satisfied
    completed_ids = {gid for gid, done in group_analysis.items() if done}
    focus_groups = []
    for g in groups:
        if g.id not in completed_ids:
            deps_satisfied = all(dep in completed_ids for dep in g.depends_on)
            if deps_satisfied:
                focus_groups.append(g.id)

    # Preserve groups: completed groups
    preserve_groups = [g.id for g in groups if g.id in completed_ids]

    # Evidence: key excerpts from harness output
    evidence = []
    if last_verification.harness.stderr:
        evidence.append(last_verification.harness.stderr[:500])
    elif last_verification.harness.stdout:
        evidence.append(last_verification.harness.stdout[:500])

    return AttemptContract(
        focus_groups=focus_groups,
        preserve_groups=preserve_groups,
        evidence=evidence,
        blockers=list(last_verification.critic.blockers),
        next_actions=list(last_verification.critic.next_actions),
    )


def _build_first_attempt_prompt(
    dirty_state: str | None = None,
    target_groups: list[AcceptanceGroup] | None = None,
) -> str:
    """Build the -p prompt for the first attempt."""
    prompt = """## First Attempt

This is your first attempt at this mission.

1. Read MISSION.md to understand the goal.
2. Read ACCEPTANCE.md for the detailed acceptance criteria.
3. Observe the current workspace (ls, check existing files).
4. Implement what's needed to satisfy the acceptance criteria.
5. Run `bash verify.sh` to check your work before finishing.

Focus on making verify.sh pass. Start by reading the existing files."""

    if target_groups is not None:
        group_names = [g.name for g in target_groups]
        criteria_lines = []
        for g in target_groups:
            for c in g.criteria:
                criteria_lines.append(f"- [{g.name}] {c.text}")
        prompt += f"""

## Current Focus

You are working on the following acceptance group(s): **{", ".join(group_names)}**

Criteria to satisfy:
{chr(10).join(criteria_lines)}

Focus ONLY on these criteria. Other groups will be handled separately."""

    if dirty_state:
        prompt += f"""

## Uncommitted Changes Detected

The workspace has uncommitted changes from a previous attempt:

```
{dirty_state}
```

Review these changes — they may contain partial progress you can build on."""

    return prompt


def _build_retry_prompt(
    last_verification: VerificationResult,
    contract: AttemptContract,
    attempt_number: int,
    all_groups: list[AcceptanceGroup],
    stall_hint: bool = False,
    dirty_state: str | None = None,
) -> str:
    """Build prompt for retry attempts with feedback from last verification."""
    lines = [f"## Retry Attempt #{attempt_number}", ""]

    # Focus groups with criteria
    if contract.focus_groups:
        lines.append("### Focus Groups (need work)")
        group_map = {g.id: g for g in all_groups}
        for gid in contract.focus_groups:
            group = group_map.get(gid)
            if group:
                lines.append(f"**{group.name}:**")
                for c in group.criteria:
                    lines.append(f"- {c.text}")
        lines.append("")

    if contract.preserve_groups:
        lines.append("### Completed Groups (don't break)")
        group_map = {g.id: g for g in all_groups}
        for gid in contract.preserve_groups:
            group = group_map.get(gid)
            if group:
                lines.append(f"- {group.name}")
        lines.append("")

    # Critic feedback
    lines.append("### Last Verification")
    gate_str = "PASS" if last_verification.gate_passed else "FAIL"
    lines.append(f"- Gate: **{gate_str}**")
    lines.append(f"- Summary: {last_verification.critic.summary}")
    lines.append("")

    if last_verification.critic.root_cause:
        lines.append(f"**Root cause:** {last_verification.critic.root_cause}")
        lines.append("")

    if contract.next_actions:
        lines.append("**Suggested actions:**")
        for action in contract.next_actions:
            lines.append(f"- {action}")
        lines.append("")

    if contract.blockers:
        lines.append("**Blockers:**")
        for blocker in contract.blockers:
            lines.append(f"- {blocker}")
        lines.append("")

    # Stall hint
    if stall_hint:
        lines.append("### STRATEGY CHANGE NEEDED")
        lines.append("")
        lines.append(
            "Multiple attempts have not improved. Try a fundamentally different approach:"
        )
        lines.append("- Re-read the acceptance criteria from scratch")
        lines.append("- Consider alternative implementations")
        lines.append("- Check if you're misunderstanding a requirement")
        lines.append("")

    # Dirty state
    if dirty_state:
        lines.append("### Uncommitted Changes")
        lines.append("")
        lines.append("```")
        lines.append(dirty_state)
        lines.append("```")
        lines.append("")

    # Instructions
    lines.append("### Instructions")
    lines.append("Focus on fixing the failing groups above.")
    lines.append("Run `bash verify.sh` to check your work before finishing.")
    lines.append("Do NOT break the already-completed groups.")

    return "\n".join(lines)


def _count_stall(ledger: Ledger, mission_id: str) -> int:
    """Count consecutive failed attempts from the end.

    Counts how many consecutive attempts have gate_passed=False.
    Any gate pass resets the counter.
    """
    attempts = ledger.get_attempts(mission_id)
    if len(attempts) < 2:
        return 0

    stall_count = 0
    for a in reversed(attempts):
        if a.get("verification_passed"):
            break
        stall_count += 1

    return stall_count


def _rollback_to_best(workdir: Path, commit_hash: str) -> None:
    """Tag current HEAD for reference, then git reset --hard to best commit."""
    tag_name = f"pre-rollback-{uuid.uuid4().hex[:6]}"
    tag_result = subprocess.run(
        ["git", "tag", tag_name],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if tag_result.returncode != 0:
        logger.warning(
            "Failed to tag HEAD before rollback: %s", tag_result.stderr.strip()
        )

    reset_result = subprocess.run(
        ["git", "reset", "--hard", commit_hash],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if reset_result.returncode != 0:
        logger.error(
            "Failed to rollback to %s: %s", commit_hash, reset_result.stderr.strip()
        )


def _get_dirty_state(workdir: Path, max_lines: int = 50) -> str | None:
    """Check git status, return formatted string or None if clean."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    status = result.stdout.strip()
    if not status:
        return None
    lines = status.splitlines()
    if len(lines) > max_lines:
        status = (
            "\n".join(lines[:max_lines])
            + f"\n... and {len(lines) - max_lines} more files"
        )
    return status


def _format_scope(contract: AttemptContract, groups: list[AcceptanceGroup]) -> str:
    """Format scope string for mission log from contract."""
    if contract.focus_groups:
        group_map = {g.id: g.name for g in groups}
        focus_names = [group_map.get(gid, gid) for gid in contract.focus_groups]
        preserve_names = [group_map.get(gid, gid) for gid in contract.preserve_groups]
        scope = f"focus [{', '.join(focus_names)}]"
        if preserve_names:
            scope += f" | preserve [{', '.join(preserve_names)}]"
        return scope
    return "all groups"


def _load_last_verification(
    ledger: Ledger,
    mission_id: str,
) -> VerificationResult | None:
    """Load last verification result from ledger for resume."""
    last = ledger.get_last_attempt(mission_id)
    if not last:
        return None
    vr_raw = last.get("verification_result", "")
    if not vr_raw:
        return None
    try:
        return VerificationResult.from_json(vr_raw)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Could not parse last verification result for resume: %s", e)
        return None


def _git_commit_if_changed(workdir: Path, attempt_number: int) -> str | None:
    """Auto-commit any workspace changes. Returns commit hash or None."""
    # Check for changes
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        return None

    # Stage and commit
    subprocess.run(["git", "add", "-A"], cwd=workdir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"automission: attempt {attempt_number}"],
        cwd=workdir,
        capture_output=True,
    )

    # Get commit hash
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None
