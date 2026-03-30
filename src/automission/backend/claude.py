"""ClaudeCodeBackend — runs attempts via `claude -p`."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from automission import DEFAULT_DOCKER_IMAGE
from automission.models import AttemptResult, AttemptSpec, StableContext, TokenUsage
from automission.backend.protocol import format_automission_md
from automission.backend._helpers import write_instruction_pointer, run_docker_attempt

logger = logging.getLogger(__name__)

_INSTRUCTION_POINTER = "\nRead AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.\n"


class ClaudeCodeBackend:
    """Runs agent attempts via `claude -p` CLI."""

    def __init__(
        self,
        docker_image: str = DEFAULT_DOCKER_IMAGE,
        auth_method: str = "api_key",
        model: str | None = None,
    ):
        self.docker_image = docker_image
        self.auth_method = auth_method  # Claude only supports api_key
        self.model = model

    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None:
        (workdir / "AUTOMISSION.md").write_text(format_automission_md(stable))
        write_instruction_pointer(workdir, "CLAUDE.md", _INSTRUCTION_POINTER)

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        inner_cmd = [
            "claude",
            "-p",
            spec.prompt,
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
            "--max-turns",
            "50",
        ]
        if self.model:
            inner_cmd.extend(["--model", self.model])
        return run_docker_attempt(
            spec,
            self.docker_image,
            inner_cmd,
            env_keys=["ANTHROPIC_API_KEY"],
            parse_output=_parse_claude_output,
        )


def _parse_claude_output(stdout: bytes) -> tuple[float, TokenUsage]:
    """Parse Claude CLI JSON output for cost and token usage."""
    try:
        output = json.loads(stdout)
        cost_usd = output.get("cost_usd", 0.0)
        token_usage = TokenUsage(
            input_tokens=output.get("input_tokens", 0),
            output_tokens=output.get("output_tokens", 0),
        )
        return cost_usd, token_usage
    except (json.JSONDecodeError, KeyError):
        logger.warning("Could not parse claude JSON output")
        return 0.0, TokenUsage()
