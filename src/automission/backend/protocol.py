"""AgentBackend protocol — interface for coding agent adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from automission.models import AttemptResult, AttemptSpec, StableContext


class AgentBackend(Protocol):
    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None:
        """Write stable mission context into AUTOMISSION.md + native instruction file.
        Called once at mission creation."""
        ...

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        """Execute one attempt. Fresh session, no prior state."""
        ...


def format_automission_md(stable: StableContext) -> str:
    """Format StableContext into AUTOMISSION.md content."""
    lines = [
        "# AUTOMISSION.md — Mission Instructions (DO NOT EDIT)",
        "",
        "## Mission",
        stable.goal,
        "See MISSION.md for full goal description.",
        "See ACCEPTANCE.md for acceptance criteria and dependencies.",
        "Run `bash verify.sh` to check your work.",
        "",
    ]

    if stable.skills:
        lines.append("## Skills")
        lines.append("")
        for skill in stable.skills:
            lines.append(skill)
            lines.append("")

    lines.append("## Rules")
    lines.append(f"- {stable.side_effect_policy}")
    lines.append(
        "- Do not modify: AUTOMISSION.md, MISSION.md, ACCEPTANCE.md, mission.db"
    )
    lines.append("- Read ACCEPTANCE.md before starting work")
    lines.append("- Run verify.sh after making changes")
    lines.append(
        "- verify.sh is the test gate. Update it if the default test command "
        "does not match your implementation (e.g., wrong runner or test directory)."
    )
    lines.append(
        "- Never hardcode /workspace or any absolute path in code or tests. "
        "Use relative paths (e.g., ./file.py), Path(__file__).parent, or os.getcwd(). "
        "Your code must be portable and work outside of Docker."
    )
    for rule in stable.rules:
        lines.append(f"- {rule}")
    lines.append("")

    return "\n".join(lines)
