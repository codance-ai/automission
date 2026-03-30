"""Shared test fixtures."""

import re
import subprocess as _subprocess
from pathlib import Path

import pytest


class MockCriticBackend:
    """Mock StructuredOutputBackend for the new Critic.

    Parses the critic prompt to determine gate pass/fail and group IDs,
    then returns appropriate per-group statuses. Used in tests as a
    deterministic replacement for the real LLM critic.
    """

    def query(
        self, prompt: str, model: str, json_schema: dict, timeout: int = 300
    ) -> dict:
        gate_passed = "Passed: True" in prompt
        # Extract group IDs from "## Groups\n<id1>, <id2>"
        groups_match = re.search(r"## Groups\n(.+)", prompt)
        group_ids = (
            [g.strip() for g in groups_match.group(1).split(",")]
            if groups_match
            else []
        )

        if gate_passed:
            return {
                "summary": "All tests pass.",
                "root_cause": "",
                "next_actions": [],
                "blockers": [],
                "group_analysis": [
                    {"group_id": gid, "completed": True} for gid in group_ids
                ],
            }
        return {
            "summary": "Tests failing, implementation incomplete.",
            "root_cause": "Gate verification failed.",
            "next_actions": ["Review the test output and fix failing tests."],
            "blockers": [],
            "group_analysis": [
                {"group_id": gid, "completed": False} for gid in group_ids
            ],
        }


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
        container_workdir = None
        i = 2
        inner_cmd = []
        volumes = []
        while i < len(cmd):
            arg = cmd[i]
            if arg == "--rm":
                i += 1
            elif arg == "-v" and i + 1 < len(cmd):
                volumes.append(cmd[i + 1])
                i += 2
            elif arg == "-w" and i + 1 < len(cmd):
                container_workdir = cmd[i + 1]
                i += 2
            elif arg == "-e" and i + 1 < len(cmd):
                i += 2
            elif arg.startswith("-"):
                i += 1
            else:
                # This is the image name; everything after is the inner command
                inner_cmd = cmd[i + 1 :]
                break
        else:
            return _real_run(cmd, **kwargs)
        # Find host path from volume mount matching container_workdir
        if container_workdir:
            for vol in volumes:
                # Format: host_path:container_path[:ro]
                if f":{container_workdir}" in vol:
                    workdir = vol.split(f":{container_workdir}")[0]
                    break
        # Strip Docker-only setup commands (e.g., git config --global)
        # from "bash -c 'setup && actual_cmd'" to avoid polluting host env.
        if (
            len(inner_cmd) == 3
            and inner_cmd[0] == "bash"
            and inner_cmd[1] == "-c"
            and "&&" in inner_cmd[2]
        ):
            actual_cmd = inner_cmd[2].split("&&", 1)[1].strip()
            inner_cmd = ["bash", "-c", actual_cmd]
        return _real_run(
            inner_cmd,
            cwd=workdir,
            capture_output=kwargs.get("capture_output", False),
            text=kwargs.get("text", False),
            timeout=kwargs.get("timeout", None),
            encoding=kwargs.get("encoding", None),
        )

    monkeypatch.setattr("automission.harness.subprocess.run", _patched_run)
