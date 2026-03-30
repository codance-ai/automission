"""Structured, human-readable mission execution log.

Writes a narrative log file covering: Plan -> Agent attempts -> Verification -> Outcome.
Thread-safe for multi-agent scenarios.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import IO


def _format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        kb = round(size_bytes / 1024)
        return f"{kb} KB"
    if size_bytes < 1024 * 1024 * 1024:
        mb = size_bytes / (1024 * 1024)
        return f"{mb:.1f} MB"
    gb = size_bytes / (1024 * 1024 * 1024)
    return f"{gb:.1f} GB"


def _format_group_statuses(group_statuses: dict[str, bool]) -> str:
    """Format group statuses as 'name >' or 'name x'."""
    parts = []
    for name, passed in group_statuses.items():
        parts.append(f"{name} {'>' if passed else 'x'}")
    return " | ".join(parts)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class MissionLogger:
    """Writes structured narrative log to a file.

    Thread-safe: all writes go through _write() which holds a lock.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] = open(path, "a", encoding="utf-8")  # noqa: SIM115
        self._lock = threading.Lock()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> MissionLogger:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def _write(self, text: str) -> None:
        """Thread-safe write with immediate flush."""
        with self._lock:
            self._file.write(text)
            self._file.flush()

    # ── Header / Footer ─────────────────────────────────────────────────

    def header(
        self,
        mission_id: str,
        backend: str,
        model: str,
        docker_image: str,
        agents: int,
        max_attempts: int,
        max_cost: float,
        timeout: int,
    ) -> None:
        ts = _now_utc()
        line = "=" * 80
        self._write(
            f"\n{line}\n"
            f"  AUTOMISSION — {mission_id}\n"
            f"  {ts}\n"
            f"  Backend: {backend} ({model})"
            f" | Image: {docker_image}\n"
            f"  Limits: max_attempts={max_attempts}"
            f" | max_cost=${max_cost:.2f}"
            f" | timeout={timeout}s\n"
            f"{line}\n"
        )

    def footer(
        self,
        outcome: str,
        total_attempts: int,
        total_cost: float,
        total_duration_s: float,
        group_statuses: dict[str, bool],
    ) -> None:
        line = "=" * 80
        groups_str = _format_group_statuses(group_statuses)
        self._write(
            f"\n{line}\n"
            f"  MISSION {outcome}\n"
            f"  Attempts: {total_attempts}"
            f" | Cost: ${total_cost:.3f}"
            f" | Duration: {total_duration_s}s\n"
            f"  Groups: {groups_str}\n"
            f"{line}\n"
        )

    # ── Plan ─────────────────────────────────────────────────────────────

    def plan(self, groups: list[dict], duration_s: float) -> None:
        section = "==== PLAN " + "=" * 70
        lines = [f"\n{section}\n", f"Duration: {duration_s}s\n\n"]

        for i, group in enumerate(groups, 1):
            name = group["name"]
            title = group["title"]
            depends = group.get("depends")
            dep_str = f" (depends: {', '.join(depends)})" if depends else ""
            lines.append(f"  Group {i}: [{name}] {title}{dep_str}\n")
            for criterion in group.get("criteria", []):
                lines.append(f"    - {criterion}\n")

        lines.append("\n")
        self._write("".join(lines))

    # ── Orchestrator ─────────────────────────────────────────────────────

    def orchestrator_round(
        self,
        round_number: int,
        frontier: list[str],
        assignments: dict[str, str],
    ) -> None:
        section = f"==== ORCHESTRATOR ROUND {round_number} " + "=" * (
            80 - len(f"==== ORCHESTRATOR ROUND {round_number} ")
        )
        lines = [f"\n{section}\n"]
        lines.append(f"Frontier: {', '.join(frontier)}\n")
        lines.append("Assignments:\n")
        for agent_id, group in assignments.items():
            lines.append(f"  {agent_id} -> {group}\n")
        lines.append("\n")
        self._write("".join(lines))

    def merge_result(
        self,
        agent_id: str,
        success: bool,
        commit_hash: str | None,
        verify_passed: bool | None,
        rejected_reason: str | None,
    ) -> None:
        status = "MERGED" if success else "REJECTED"
        lines = [f"  {agent_id}: {status}"]
        if commit_hash:
            lines[0] += f" ({commit_hash})"
        if success and verify_passed:
            lines[0] += " — verified"
        lines[0] += "\n"
        if rejected_reason:
            lines.append(f"    Reason: {rejected_reason}\n")
        self._write("".join(lines))

    # ── Attempt ──────────────────────────────────────────────────────────

    def attempt_start(
        self,
        attempt_number: int,
        agent_id: str,
        scope: str,
    ) -> None:
        section = f"==== ATTEMPT {attempt_number} " + "=" * (
            80 - len(f"==== ATTEMPT {attempt_number} ")
        )
        ts = _now_utc()
        self._write(f"\n{section}\nScope: {scope}\n{ts}\n\n")

    def attempt_prompt(self, prompt: str, prompt_len: int) -> None:
        label = f"---- prompt ({prompt_len:,} chars) "
        dash_line = label + "-" * (80 - len(label))
        end_line = "-" * 80
        self._write(f"{dash_line}\n{prompt}\n{end_line}\n\n")

    def attempt_execution(
        self,
        status: str,
        exit_code: int,
        duration_s: float,
        token_input: int,
        token_output: int,
        cost_usd: float,
        changed_files: list[str],
        commit_hash: str | None,
        stdout_path: str | None,
        stdout_size: int | None,
    ) -> None:
        label = "---- agent execution "
        dash_line = label + "-" * (80 - len(label))
        end_line = "-" * 80

        lines = [f"{dash_line}\n"]
        lines.append(
            f"Duration: {duration_s}s"
            f" | Tokens: {token_input:,} in + {token_output:,} out"
            f" | Cost: ${cost_usd:.3f}\n"
        )
        lines.append(f"Status: {status} (exit {exit_code})\n")

        if changed_files:
            lines.append("Changed:\n")
            for f in changed_files:
                lines.append(f"  {f}\n")

        if commit_hash:
            lines.append(f"Commit: {commit_hash}\n")

        if stdout_path and stdout_size is not None:
            lines.append(
                f"\nFull output: {stdout_path} ({_format_size(stdout_size)})\n"
            )

        lines.append(f"{end_line}\n\n")
        self._write("".join(lines))

    # ── Verification ─────────────────────────────────────────────────────

    def verification(
        self,
        passed: bool,
        exit_code: int,
        harness_duration_s: float,
        stdout: str,
        stderr: str,
        critic_duration_s: float | None,
        critic_cost_usd: float | None,
        summary: str | None,
        root_cause: str | None,
        next_actions: list[str] | None,
        group_statuses: dict[str, bool],
    ) -> None:
        label = "---- verification "
        dash_line = label + "-" * (80 - len(label))
        end_line = "-" * 80

        result = "PASS" if passed else "FAIL"
        lines = [f"{dash_line}\n"]
        lines.append(
            f"Harness: {result} (exit {exit_code}) | Duration: {harness_duration_s}s\n"
        )

        if stdout:
            lines.append("\nverify.sh stdout:\n")
            for sline in stdout.splitlines():
                lines.append(f"  {sline}\n")

        if stderr:
            lines.append("\nverify.sh stderr:\n")
            for sline in stderr.splitlines():
                lines.append(f"  {sline}\n")

        if critic_duration_s is not None and critic_cost_usd is not None:
            lines.append(f"\nCritic: ({critic_duration_s}s | ${critic_cost_usd:.3f})\n")
            if summary:
                lines.append(f"  Summary: {summary}\n")
            if root_cause:
                lines.append(f"  Root cause: {root_cause}\n")
            if next_actions:
                lines.append("  Next actions:\n")
                for i, action in enumerate(next_actions, 1):
                    lines.append(f"    {i}. {action}\n")

        groups_str = _format_group_statuses(group_statuses)
        lines.append(f"  Groups: {groups_str}\n")
        lines.append(f"{end_line}\n\n")
        self._write("".join(lines))

    # ── Timing ───────────────────────────────────────────────────────────

    def timing(
        self,
        prompt_s: float,
        agent_s: float,
        harness_s: float,
        critic_s: float | None,
    ) -> None:
        total = prompt_s + agent_s + harness_s + (critic_s or 0.0)
        parts = [
            f"prompt {prompt_s}s",
            f"agent {agent_s}s",
            f"harness {harness_s}s",
        ]
        if critic_s is not None:
            parts.append(f"critic {critic_s}s")
        # Round to avoid floating point artifacts
        total = round(total, 1)
        parts.append(f"total {total}s")
        self._write(f"Timing: {' | '.join(parts)}\n")
