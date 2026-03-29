"""Shared Docker utilities for building and validating Docker commands."""

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DOCKER_IMAGE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_/:\-\.]*$")

# Must match the non-root user created in Dockerfile (USER agent).
CONTAINER_HOME = "/home/agent"


def build_docker_cmd(
    image: str,
    inner_cmd: list[str],
    workdir: Optional[Path] = None,
    env_keys: Optional[list[str]] = None,
    env_pairs: Optional[dict[str, str]] = None,
    volumes: Optional[list[tuple[str, str]]] = None,
    rw_volumes: Optional[list[tuple[str, str]]] = None,
) -> list[str]:
    """Build a ``docker run --rm`` command list.

    Args:
        image: Docker image name. Must match DOCKER_IMAGE_PATTERN.
        inner_cmd: Command to run inside the container.
        workdir: If provided, mount as ``-v {workdir.resolve()}:/workspace -w /workspace``.
        env_keys: Env var names to pass via ``-e NAME``. Docker inherits values from host env.
        env_pairs: Env var key-value pairs to pass via ``-e NAME=VALUE``.
        volumes: List of (host_path, container_path) tuples to mount read-only.
        rw_volumes: List of (host_path, container_path) tuples to mount read-write.
            Used for OAuth token files that need write access for token refresh.

    Returns:
        A list[str] ready for ``subprocess.run()``.

    Raises:
        ValueError: If *image* does not match the allowed pattern.
    """
    if not DOCKER_IMAGE_PATTERN.match(image):
        raise ValueError(f"Invalid Docker image name: {image!r}")

    cmd = ["docker", "run", "--rm"]

    if workdir is not None:
        cmd += ["-v", f"{workdir.resolve()}:/workspace", "-w", "/workspace"]

    for host_path, container_path in volumes or []:
        cmd += ["-v", f"{host_path}:{container_path}:ro"]

    for host_path, container_path in rw_volumes or []:
        cmd += ["-v", f"{host_path}:{container_path}"]

    for key in env_keys or []:
        cmd += ["-e", key]

    for key, val in (env_pairs or {}).items():
        cmd += ["-e", f"{key}={val}"]

    cmd.append(image)
    cmd.extend(inner_cmd)
    return cmd


def ensure_docker(image: str) -> None:
    """Pre-flight check: Docker daemon available + image exists (auto-pull if missing).

    Raises:
        ValueError: If *image* does not match DOCKER_IMAGE_PATTERN.
        RuntimeError: If Docker daemon is not available, or image not found and auto-pull fails.
    """
    if not DOCKER_IMAGE_PATTERN.match(image):
        raise ValueError(f"Invalid Docker image name: {image!r}")

    try:
        subprocess.run(["docker", "version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Docker is not available. Make sure Docker is installed and the daemon is running."
        ) from exc

    inspect = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    if inspect.returncode == 0:
        return

    logger.info("Docker image %r not found locally, pulling...", image)
    pull = subprocess.run(["docker", "pull", image], capture_output=True)
    if pull.returncode != 0:
        raise RuntimeError(
            f"Docker image {image!r} not found and auto-pull failed.\n"
            f"stderr: {pull.stderr.decode(errors='replace').strip()}"
        )
    logger.info("Successfully pulled Docker image %r.", image)
