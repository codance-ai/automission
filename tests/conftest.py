"""Shared test fixtures."""

import subprocess as _subprocess
from pathlib import Path

import pytest


@pytest.fixture
def fixture_dir():
    """Path to the m1-calculator test fixture."""
    return Path(__file__).parent / "fixtures" / "m1-calculator"


@pytest.fixture
def mission_dir(tmp_path):
    """Temporary directory for mission workspaces."""
    d = tmp_path / "missions"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _unwrap_docker_in_verifier(monkeypatch):
    """Intercept Docker commands from verifier and run the inner command directly.

    This allows integration tests to work without Docker installed.
    """
    _real_run = _subprocess.run

    def _patched_run(cmd, **kwargs):
        if not (
            isinstance(cmd, list)
            and len(cmd) >= 3
            and cmd[0] == "docker"
            and cmd[1] == "run"
        ):
            return _real_run(cmd, **kwargs)
        workdir = None
        i = 2
        inner_cmd = []
        while i < len(cmd):
            arg = cmd[i]
            if arg == "--rm":
                i += 1
            elif arg == "-v" and i + 1 < len(cmd):
                mount = cmd[i + 1]
                if ":/workspace" in mount:
                    workdir = mount.split(":/workspace")[0]
                i += 2
            elif arg in ("-w", "-e") and i + 1 < len(cmd):
                i += 2
            elif arg.startswith("-"):
                i += 1
            else:
                # This is the image name; everything after is the inner command
                inner_cmd = cmd[i + 1 :]
                break
        else:
            return _real_run(cmd, **kwargs)
        return _real_run(
            inner_cmd,
            cwd=workdir,
            capture_output=kwargs.get("capture_output", False),
            text=kwargs.get("text", False),
            timeout=kwargs.get("timeout", None),
            encoding=kwargs.get("encoding", None),
        )

    monkeypatch.setattr("automission.verifier.subprocess.run", _patched_run)
