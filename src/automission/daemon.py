"""Daemon process management — spawn, stop, liveness detection."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from automission.db import Ledger

logger = logging.getLogger(__name__)


def spawn_executor(
    workspace_dir: Path,
    mission_id: str,
    log_file: Path | None = None,
) -> int:
    if log_file is None:
        log_file = workspace_dir / "executor.log"

    log_fh = open(log_file, "a")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "automission.executor",
                str(workspace_dir),
                mission_id,
            ],
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
            cwd=str(workspace_dir),
        )
    finally:
        log_fh.close()  # Parent closes its copy; child keeps inherited fd

    logger.info("Spawned executor PID %d for mission %s", proc.pid, mission_id)
    return proc.pid


def read_pid_file(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def is_executor_alive(workspace_dir: Path, mission_id: str) -> bool:
    pid_file = workspace_dir / "mission.pid"
    pid = read_pid_file(pid_file)
    if pid is None:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass

    try:
        with Ledger(workspace_dir / "mission.db") as ledger:
            rt = ledger.get_executor_runtime(mission_id)
        if rt is None:
            return False
        return True
    except Exception:
        return False


def stop_executor(workspace_dir: Path, mission_id: str) -> bool:
    if not is_executor_alive(workspace_dir, mission_id):
        return False

    with Ledger(workspace_dir / "mission.db") as ledger:
        ledger.set_executor_desired_state(mission_id, "stopping")

    pid = read_pid_file(workspace_dir / "mission.pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("Sent SIGTERM to executor PID %d", pid)
        except ProcessLookupError:
            logger.warning("Executor PID %d already gone", pid)
        except PermissionError:
            logger.error("Cannot signal executor PID %d", pid)

    return True


def wait_for_executor_exit(
    workspace_dir: Path, mission_id: str, timeout: float = 30
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_executor_alive(workspace_dir, mission_id):
            return True
        time.sleep(0.5)
    return False
