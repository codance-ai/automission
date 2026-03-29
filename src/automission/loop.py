"""Agent loop — single iteration (M1) and full loop with circuit breakers."""

from __future__ import annotations

import json
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from automission.backend.protocol import AgentBackend
from automission.db import Ledger
from automission.events import EventWriter
from automission.models import (
    AcceptanceGroup,
    AttemptContract,
    AttemptSpec,
    MissionOutcome,
    VerifierResult,
)
from automission.verifier import Verifier

logger = logging.getLogger(__name__)


# ── Public API ──


def run_single_iteration(
    mission_id: str,
    workdir: Path,
    backend: AgentBackend,
    verifier: Verifier,
    agent_id: str = "agent-1",
    timeout_s: int = 300,
    mission_dir: Path | None = None,
    target_groups: list[AcceptanceGroup] | None = None,
) -> VerifierResult:
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
            verifier=verifier,
            ledger=ledger,
            agent_id=agent_id,
            timeout_s=timeout_s,
            last_verification=None,
            stall_hint=False,
            mission_dir=mission_dir,
            target_groups=target_groups,
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
    verifier: Verifier,
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
) -> str:
    """Main agent loop with circuit breakers, stall detection, and resume.

    If target_groups is provided, the agent focuses on those groups only
    (used by orchestrator for claimed groups). The verifier still evaluates
    all groups to compute group_statuses.

    Returns a MissionOutcome string value: "completed", "failed",
    "cancelled", or "resource_limit".
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
                return MissionOutcome.FAILED

            # ── Circuit breakers ──

            # Cancel flag
            if cancel_flag():
                ledger.update_mission_status(mission_id, MissionOutcome.CANCELLED)
                logger.info("Mission %s cancelled by flag", mission_id)
                return MissionOutcome.CANCELLED

            # Max iterations
            if mission["total_attempts"] >= max_iterations:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                logger.info(
                    "Mission %s hit max iterations (%d)",
                    mission_id,
                    max_iterations,
                )
                return MissionOutcome.RESOURCE_LIMIT

            # Max cost
            if mission["total_cost"] >= max_cost:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                logger.info(
                    "Mission %s hit max cost ($%.2f)",
                    mission_id,
                    max_cost,
                )
                return MissionOutcome.RESOURCE_LIMIT

            # Timeout
            age_s = ledger.get_mission_age_s(mission_id)
            if age_s is not None and age_s >= timeout:
                ledger.update_mission_status(mission_id, MissionOutcome.RESOURCE_LIMIT)
                logger.info("Mission %s timed out", mission_id)
                return MissionOutcome.RESOURCE_LIMIT

            # ── Stall detection (pre-iteration) ──
            stall_hint = False
            stall_count = _count_stall(ledger, mission_id, stall_threshold)

            if stall_count >= stall_threshold * 2:
                # Too many stalls — give up
                ledger.update_mission_status(mission_id, MissionOutcome.FAILED)
                logger.info(
                    "Mission %s failed: stall count %d >= %d",
                    mission_id,
                    stall_count,
                    stall_threshold * 2,
                )
                return MissionOutcome.FAILED

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
                verifier=verifier,
                ledger=ledger,
                agent_id=agent_id,
                timeout_s=timeout_per_attempt,
                last_verification=last_verification,
                stall_hint=stall_hint,
                mission_dir=mission_dir,
                target_groups=target_groups,
                event_writer=event_writer,
            )

            # ── Check pass ──
            if target_groups is not None:
                # Scoped mode: only check if target groups passed.
                # Don't set mission status — caller decides mission completion.
                target_ids = {g.id for g in target_groups}
                all_targets_done = all(
                    verification.group_statuses.get(gid, False) for gid in target_ids
                )
                if all_targets_done:
                    logger.info(
                        "Target groups %s all completed",
                        target_ids,
                    )
                    return MissionOutcome.COMPLETED
            elif verification.mission_passed:
                ledger.update_mission_status(mission_id, MissionOutcome.COMPLETED)
                logger.info("Mission %s completed!", mission_id)
                return MissionOutcome.COMPLETED

            # Carry verification forward for next iteration
            last_verification = verification

    finally:
        ledger.close()


# ── Internal helpers ──


def _run_one_iteration(
    mission_id: str,
    workdir: Path,
    backend: AgentBackend,
    verifier: Verifier,
    ledger: Ledger,
    agent_id: str = "agent-1",
    timeout_s: int = 300,
    last_verification: VerifierResult | None = None,
    stall_hint: bool = False,
    mission_dir: Path | None = None,
    target_groups: list[AcceptanceGroup] | None = None,
    event_writer: "EventWriter | None" = None,
) -> VerifierResult:
    """Run one attempt: prompt -> execute -> commit -> verify -> record.

    Extracted core used by both run_single_iteration() and run_loop().

    If target_groups is set, the agent prompt focuses on those groups' criteria.
    The verifier still evaluates all groups for group_statuses computation.
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

    # Get ALL acceptance groups for verifier (needs full picture)
    groups = ledger.get_acceptance_groups(mission_id)

    # Check for dirty state
    dirty_state = _get_dirty_state(workdir)

    # ── Build prompt ──
    if last_verification is None:
        # First attempt
        prompt = _build_first_attempt_prompt(
            dirty_state=dirty_state,
            target_groups=target_groups,
        )
    else:
        # Retry: derive contract scoped to target groups
        contract = _derive_contract(last_verification, target_groups=target_groups)
        prompt = _build_retry_prompt(
            last_verification=last_verification,
            contract=contract,
            attempt_number=attempt_number,
            stall_hint=stall_hint,
            dirty_state=dirty_state,
        )

    # ── Run attempt ──
    spec = AttemptSpec(
        attempt_id=attempt_id,
        mission_id=mission_id,
        workdir=workdir,
        prompt=prompt,
        timeout_s=timeout_s,
    )
    logger.info("Running attempt %s (#%d)", attempt_id, attempt_number)
    if event_writer:
        event_writer.emit(
            "attempt_start",
            {
                "agent_id": agent_id,
                "attempt": attempt_number,
                "attempt_id": attempt_id,
            },
        )
    attempt_result = backend.run_attempt(spec)

    # ── Auto-commit ──
    commit_hash = _git_commit_if_changed(workdir, attempt_number)

    # ── Verify ──
    verify_sh = workdir / "verify.sh"
    verification = verifier.evaluate(
        workdir,
        verify_sh if verify_sh.exists() else None,
        groups,
    )

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
        verification_passed=verification.contract_passed,
        verification_result=verification.to_json(),
        commit_hash=commit_hash or "",
    )

    # ── Update group statuses ──
    if verification.group_statuses:
        statuses = verification.group_statuses
        if target_groups is not None:
            # Only update statuses for target groups to avoid marking
            # unevaluated groups as complete (basic_critic marks all True/False)
            target_ids = {g.id for g in target_groups}
            statuses = {k: v for k, v in statuses.items() if k in target_ids}
        if statuses:
            ledger.update_group_statuses(statuses)

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
                "passed": verification.contract_passed,
                "score": verification.score,
                "failed_criteria": [c.criterion for c in verification.failed_criteria],
            },
        )

    logger.info(
        "Attempt %d: gate %s (score=%s)",
        attempt_number,
        "PASS" if verification.contract_passed else "FAIL",
        verification.score,
    )

    return verification


def _derive_contract(
    last_verification: VerifierResult,
    target_groups: list[AcceptanceGroup] | None = None,
) -> AttemptContract:
    """Auto-derive attempt contract from last verification result.

    If target_groups is set, only include criteria belonging to those groups
    in the must-fix list. Passed criteria from target groups go to non_goals.
    Criteria from other groups are excluded from the contract entirely.
    """
    failed = last_verification.failed_criteria
    passed = last_verification.passed_criteria

    if target_groups is not None:
        # Build set of criterion texts belonging to target groups
        target_criterion_texts = set()
        for g in target_groups:
            for c in g.criteria:
                target_criterion_texts.add(c.text)
        # Filter to target groups only
        failed = [c for c in failed if c.criterion in target_criterion_texts]
        passed = [c for c in passed if c.criterion in target_criterion_texts]

    # Scope: focused description from failed criteria + suggestion
    failed_texts = [c.criterion for c in failed]
    scope_parts = []
    if failed_texts:
        scope_parts.append(f"Fix: {', '.join(failed_texts[:3])}")
    if last_verification.suggestion:
        scope_parts.append(last_verification.suggestion)
    scope = ". ".join(scope_parts) if scope_parts else "Fix failing criteria"

    # done_criteria: failed criterion texts
    done_criteria = [c.criterion for c in failed]

    # non_goals: passed criterion texts (don't break these)
    non_goals = [c.criterion for c in passed]

    return AttemptContract(
        scope=scope,
        done_criteria=done_criteria,
        non_goals=non_goals,
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
    last_verification: VerifierResult,
    contract: AttemptContract,
    attempt_number: int,
    stall_hint: bool = False,
    dirty_state: str | None = None,
) -> str:
    """Build prompt for retry attempts with feedback from last verification."""
    lines = [
        f"## Retry Attempt #{attempt_number}",
        "",
    ]

    # Contract: focus/must-fix/don't-break
    lines.append("### Focus")
    lines.append(contract.scope)
    lines.append("")

    if contract.done_criteria:
        lines.append("### Must Fix (failed criteria)")
        for c in contract.done_criteria:
            lines.append(f"- {c}")
        lines.append("")

    if contract.non_goals:
        lines.append("### Don't Break (passed criteria)")
        for c in contract.non_goals:
            lines.append(f"- {c}")
        lines.append("")

    # Last verification feedback
    lines.append("### Last Verification Feedback")
    gate_str = "PASS" if last_verification.contract_passed else "FAIL"
    lines.append(f"- Gate: **{gate_str}**")
    if last_verification.score is not None:
        lines.append(f"- Score: {last_verification.score}")
    lines.append("")

    if last_verification.failed_criteria:
        lines.append("**Failed criteria:**")
        for c in last_verification.failed_criteria:
            detail = f" — {c.detail}" if c.detail else ""
            lines.append(f"- {c.criterion}{detail}")
        lines.append("")

    if last_verification.passed_criteria:
        lines.append("**Passed criteria:**")
        for c in last_verification.passed_criteria:
            lines.append(f"- {c.criterion}")
        lines.append("")

    if last_verification.suggestion:
        lines.append(f"**Suggestion:** {last_verification.suggestion}")
        lines.append("")

    # Stall hint
    if stall_hint:
        lines.append("### ⚠ STRATEGY CHANGE NEEDED")
        lines.append("")
        lines.append("Multiple attempts have not improved the score.")
        lines.append("You MUST try a fundamentally different approach:")
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
    lines.append("Focus on fixing the failed criteria above.")
    lines.append("Run `bash verify.sh` to check your work before finishing.")
    lines.append("Do NOT break the already-passing criteria.")

    return "\n".join(lines)


def _count_stall(ledger: Ledger, mission_id: str, threshold: int) -> int:
    """Count consecutive no-improvement attempts from the end.

    Walks backwards through attempts counting how many in a row
    have a score at or below the best score seen before them.
    """
    attempts = ledger.get_attempts(mission_id)
    if len(attempts) < 2:
        return 0

    # Parse scores
    scores: list[float] = []
    for a in attempts:
        vr_raw = a.get("verification_result", "")
        if not vr_raw:
            scores.append(0.0)
            continue
        try:
            vr = json.loads(vr_raw)
            s = vr.get("score")
            scores.append(s if s is not None else 0.0)
        except (json.JSONDecodeError, KeyError):
            scores.append(0.0)

    # O(n) forward scan: count trailing consecutive non-improvements
    best_so_far = scores[0]
    stall_count = 0
    for i in range(1, len(scores)):
        if scores[i] > best_so_far:
            best_so_far = scores[i]
            stall_count = 0  # improvement resets counter
        else:
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


def _load_last_verification(
    ledger: Ledger,
    mission_id: str,
) -> VerifierResult | None:
    """Load last verification result from ledger for resume."""
    last = ledger.get_last_attempt(mission_id)
    if not last:
        return None
    vr_raw = last.get("verification_result", "")
    if not vr_raw:
        return None
    try:
        return VerifierResult.from_json(vr_raw)
    except (json.JSONDecodeError, KeyError):
        logger.warning("Could not parse last verification result for resume")
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
