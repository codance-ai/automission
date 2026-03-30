"""Shared helpers for agent backends."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Callable

from automission.docker import build_docker_cmd
from automission.models import AttemptResult, AttemptSpec, TokenUsage

logger = logging.getLogger(__name__)


def write_instruction_pointer(workdir: Path, filename: str, pointer: str) -> None:
    """Append instruction pointer to backend-specific file (CLAUDE.md / AGENTS.md / GEMINI.md).

    Creates the file if it doesn't exist, otherwise appends to existing content.
    """
    target = workdir / filename
    if target.exists():
        target.write_text(target.read_text() + pointer)
    else:
        target.write_text(pointer)


def run_docker_attempt(
    spec: AttemptSpec,
    docker_image: str,
    inner_cmd: list[str],
    env_keys: list[str],
    parse_output: Callable[[bytes], tuple[float, TokenUsage]],
    volumes: list[tuple[str, str]] | None = None,
    rw_volumes: list[tuple[str, str]] | None = None,
) -> AttemptResult:
    """Execute an agent attempt in Docker with shared timeout/crash/git-detection logic.

    Args:
        spec: Attempt specification (workdir, prompt, timeout, env).
        docker_image: Docker image to use.
        inner_cmd: CLI command to run inside Docker.
        env_keys: Environment variable names to pass through.
        parse_output: Backend-specific function to parse stdout → (cost_usd, TokenUsage).
        volumes: Optional read-only volume mounts [(host, container), ...].
        rw_volumes: Optional read-write volume mounts [(host, container), ...].
            Used for OAuth token files that need write access for token refresh.

    Returns:
        AttemptResult with status, cost, tokens, changed files.
    """
    start = time.monotonic()
    changed_before = _git_file_set(spec.workdir)

    try:
        cmd = build_docker_cmd(
            docker_image,
            inner_cmd,
            workdir=spec.workdir,
            env_keys=env_keys,
            env_pairs=spec.env,
            volumes=volumes,
            rw_volumes=rw_volumes,
        )
        result = subprocess.run(cmd, timeout=spec.timeout_s, capture_output=True)
        duration = time.monotonic() - start

        # Save agent output to files if output_dir is set
        stdout_path = None
        stderr_path = None
        if spec.output_dir is not None:
            spec.output_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = spec.output_dir / f"{spec.attempt_id}.stdout"
            stdout_path.write_bytes(result.stdout)
            if result.stderr:
                stderr_path = spec.output_dir / f"{spec.attempt_id}.stderr"
                stderr_path.write_bytes(result.stderr)

        cost_usd, token_usage = parse_output(result.stdout)

        changed_after = _git_file_set(spec.workdir)
        changed_files = list(changed_after - changed_before)

        return AttemptResult(
            status="completed" if result.returncode == 0 else "failed",
            exit_code=result.returncode,
            transcript_path=None,
            token_usage=token_usage,
            cost_usd=cost_usd,
            duration_s=duration,
            changed_files=changed_files,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        logger.warning(
            "Attempt %s timed out after %ds", spec.attempt_id, spec.timeout_s
        )
        return AttemptResult(status="timed_out", duration_s=duration)

    except OSError as e:
        duration = time.monotonic() - start
        logger.error("Attempt %s crashed: %s", spec.attempt_id, e)
        return AttemptResult(status="crashed", duration_s=duration)


def _git_file_set(workdir: Path) -> set[str]:
    """Get set of tracked + modified files via git."""
    try:
        files: set[str] = set()
        for cmd in (
            ["git", "diff", "--name-only", "HEAD"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ):
            proc = subprocess.Popen(
                cmd,
                cwd=str(workdir),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout, _ = proc.communicate()
            files.update(stdout.decode().strip().splitlines())
        return files
    except (subprocess.CalledProcessError, OSError):
        return set()
