"""CodexBackend — runs attempts via `codex exec`."""

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


class CodexBackend:
    """Runs agent attempts via `codex exec` CLI."""

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
        write_instruction_pointer(workdir, "AGENTS.md", _INSTRUCTION_POINTER)

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        inner_cmd = [
            "codex",
            "exec",
            spec.prompt,
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
        ]
        if self.model:
            inner_cmd.extend(["--model", self.model])
        env_keys = ["CODEX_API_KEY"] if self.auth_method == "api_key" else []
        oauth_vols = get_oauth_volumes("codex", self.auth_method)
        rw_volumes = [(h, c) for h, c, _mode in oauth_vols]
        return run_docker_attempt(
            spec,
            self.docker_image,
            inner_cmd,
            env_keys=env_keys,
            parse_output=_parse_codex_output,
            rw_volumes=rw_volumes or None,
        )


def _parse_codex_output(stdout: bytes) -> tuple[float, TokenUsage]:
    """Parse Codex JSONL event stream for usage data.

    Codex --json outputs one JSON object per line. We look for
    turn.completed events which carry token usage data.
    """
    input_tokens = 0
    output_tokens = 0
    for line in stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
    return 0.0, TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
