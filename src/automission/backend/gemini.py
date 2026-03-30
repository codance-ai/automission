"""GeminiBackend — runs attempts via `gemini -p`."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from automission import DEFAULT_DOCKER_IMAGE
from automission.config import get_oauth_volumes
from automission.models import AttemptResult, AttemptSpec, StableContext, TokenUsage
from automission.backend.protocol import format_automission_md
from automission.backend._helpers import write_instruction_pointer, run_docker_attempt

logger = logging.getLogger(__name__)

_INSTRUCTION_POINTER = "\nRead AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.\n"


class GeminiBackend:
    """Runs agent attempts via `gemini -p` CLI."""

    def __init__(
        self,
        docker_image: str = DEFAULT_DOCKER_IMAGE,
        auth_method: str = "api_key",
        model: str | None = None,
    ):
        self.docker_image = docker_image
        self.auth_method = auth_method
        self.model = model

    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None:
        (workdir / "AUTOMISSION.md").write_text(format_automission_md(stable))
        write_instruction_pointer(workdir, "GEMINI.md", _INSTRUCTION_POINTER)

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        inner_cmd = [
            "gemini",
            "-p",
            spec.prompt,
            "--yolo",
            "--output-format",
            "json",
        ]
        if self.model:
            inner_cmd.extend(["--model", self.model])
        env_keys = ["GEMINI_API_KEY"] if self.auth_method == "api_key" else []
        oauth_vols = get_oauth_volumes("gemini", self.auth_method)
        rw_volumes = [(h, c) for h, c, _mode in oauth_vols]
        return run_docker_attempt(
            spec,
            self.docker_image,
            inner_cmd,
            env_keys=env_keys,
            parse_output=_parse_gemini_output,
            rw_volumes=rw_volumes or None,
        )


def _parse_gemini_output(stdout: bytes) -> tuple[float, TokenUsage]:
    """Parse Gemini single-JSON output for usage data.

    Gemini --output-format json returns a single JSON object:
    {session_id, response, stats: {tokens_input, tokens_output}}

    Note: response may be empty for tool-only execution — that's not an error.
    """
    try:
        output = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Could not parse Gemini JSON output")
        return 0.0, TokenUsage()

    stats = output.get("stats", {})
    return 0.0, TokenUsage(
        input_tokens=stats.get("tokens_input", 0),
        output_tokens=stats.get("tokens_output", 0),
    )
