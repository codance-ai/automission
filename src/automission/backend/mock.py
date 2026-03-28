"""MockBackend for testing — simulates agent execution."""

from __future__ import annotations

from pathlib import Path
import time

from automission.models import AttemptResult, AttemptSpec, StableContext, TokenUsage
from automission.backend.protocol import format_automission_md


_INSTRUCTION_POINTER = "\nRead AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.\n"


class MockBackend:
    """Configurable mock that simulates agent behavior for testing."""

    def __init__(
        self,
        result_status: str = "completed",
        exit_code: int = 0,
        cost_usd: float = 0.0,
        changed_files: list[str] | None = None,
        simulate_files: dict[str, str] | None = None,
        simulate_sequence: list[dict[str, str]] | None = None,
    ):
        self.result_status = result_status
        self.exit_code = exit_code
        self.cost_usd = cost_usd
        self.changed_files = changed_files or []
        self.simulate_files = simulate_files or {}
        self.simulate_sequence = simulate_sequence
        self._attempt_count = 0
        self.attempts: list[AttemptSpec] = []

    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None:
        # Write AUTOMISSION.md
        (workdir / "AUTOMISSION.md").write_text(format_automission_md(stable))

        # Append pointer to CLAUDE.md
        claude_md = workdir / "CLAUDE.md"
        if claude_md.exists():
            claude_md.write_text(claude_md.read_text() + _INSTRUCTION_POINTER)
        else:
            claude_md.write_text(_INSTRUCTION_POINTER)

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        self.attempts.append(spec)
        start = time.monotonic()

        # Simulate file changes
        if self.simulate_sequence is not None:
            files = self.simulate_sequence[
                min(self._attempt_count, len(self.simulate_sequence) - 1)
            ]
        else:
            files = self.simulate_files
        self._attempt_count += 1
        for rel_path, content in files.items():
            full_path = spec.workdir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

        duration = time.monotonic() - start

        return AttemptResult(
            status=self.result_status,
            exit_code=self.exit_code,
            cost_usd=self.cost_usd,
            duration_s=duration,
            changed_files=self.changed_files,
            token_usage=TokenUsage(input_tokens=1000, output_tokens=500),
        )
