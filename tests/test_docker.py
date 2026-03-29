"""Tests for shared Docker utilities."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from automission.docker import build_docker_cmd, ensure_docker


class TestBuildDockerCmd:
    def test_with_workdir_and_env_keys(self, tmp_path):
        workdir = tmp_path / "project"
        workdir.mkdir()
        cmd = build_docker_cmd(
            "myimage:latest",
            ["bash", "run.sh"],
            workdir=workdir,
            env_keys=["ANTHROPIC_API_KEY", "HOME"],
        )
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert cmd[2] == "--rm"
        assert "-v" in cmd
        v_idx = cmd.index("-v")
        assert ":/workspace" in cmd[v_idx + 1]
        assert "-w" in cmd
        w_idx = cmd.index("-w")
        assert cmd[w_idx + 1] == "/workspace"
        # env keys passed as -e NAME (no value)
        e_indices = [i for i, x in enumerate(cmd) if x == "-e"]
        assert len(e_indices) == 2
        assert cmd[e_indices[0] + 1] == "ANTHROPIC_API_KEY"
        assert cmd[e_indices[1] + 1] == "HOME"
        # image and inner command
        assert "myimage:latest" in cmd
        assert cmd[-2:] == ["bash", "run.sh"]

    def test_without_workdir(self):
        cmd = build_docker_cmd("myimage:latest", ["echo", "hello"])
        assert cmd == ["docker", "run", "--rm", "myimage:latest", "echo", "hello"]

    def test_invalid_image_raises_valueerror(self):
        with pytest.raises(ValueError, match="Invalid Docker image name"):
            build_docker_cmd("invalid image!!", ["echo"])

    def test_with_env_pairs(self):
        cmd = build_docker_cmd(
            "myimage:latest",
            ["echo"],
            env_pairs={"FOO": "bar", "BAZ": "qux"},
        )
        # Find -e flags with KEY=VALUE
        e_indices = [i for i, x in enumerate(cmd) if x == "-e"]
        env_values = [cmd[i + 1] for i in e_indices]
        assert "FOO=bar" in env_values
        assert "BAZ=qux" in env_values

    def test_with_both_env_keys_and_env_pairs(self, tmp_path):
        workdir = tmp_path / "ws"
        workdir.mkdir()
        cmd = build_docker_cmd(
            "myimage:latest",
            ["run"],
            workdir=workdir,
            env_keys=["API_KEY"],
            env_pairs={"MODE": "test"},
        )
        e_indices = [i for i, x in enumerate(cmd) if x == "-e"]
        env_values = [cmd[i + 1] for i in e_indices]
        assert "API_KEY" in env_values
        assert "MODE=test" in env_values

    def test_with_volumes(self):
        cmd = build_docker_cmd(
            "myimage:latest",
            ["echo", "hi"],
            volumes=[
                ("/host/creds.json", "/container/creds.json"),
                ("/host/config", "/container/config"),
            ],
        )
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        assert len(v_indices) == 2
        assert cmd[v_indices[0] + 1] == "/host/creds.json:/container/creds.json:ro"
        assert cmd[v_indices[1] + 1] == "/host/config:/container/config:ro"

    def test_with_volumes_and_workdir(self, tmp_path):
        workdir = tmp_path / "project"
        workdir.mkdir()
        cmd = build_docker_cmd(
            "myimage:latest",
            ["run"],
            workdir=workdir,
            volumes=[("/host/token.json", "/root/.token.json")],
            env_keys=["API_KEY"],
        )
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        # First -v is the workdir mount, second is the volume mount
        assert len(v_indices) == 2
        assert ":/workspace" in cmd[v_indices[0] + 1]
        assert cmd[v_indices[1] + 1] == "/host/token.json:/root/.token.json:ro"
        # Volume mounts come after workdir but before env vars
        e_indices = [i for i, x in enumerate(cmd) if x == "-e"]
        assert v_indices[1] < e_indices[0]

    def test_with_rw_volumes(self):
        """Read-write volumes should not have :ro suffix."""
        cmd = build_docker_cmd(
            "myimage:latest",
            ["echo", "hi"],
            rw_volumes=[
                ("/home/user/.codex", "/root/.codex"),
                ("/home/user/.gemini", "/root/.gemini"),
            ],
        )
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        assert len(v_indices) == 2
        assert cmd[v_indices[0] + 1] == "/home/user/.codex:/root/.codex"
        assert cmd[v_indices[1] + 1] == "/home/user/.gemini:/root/.gemini"
        # No :ro suffix
        for idx in v_indices:
            assert not cmd[idx + 1].endswith(":ro")

    def test_with_both_ro_and_rw_volumes(self, tmp_path):
        """Both read-only and read-write volumes can coexist."""
        workdir = tmp_path / "project"
        workdir.mkdir()
        cmd = build_docker_cmd(
            "myimage:latest",
            ["run"],
            workdir=workdir,
            volumes=[("/host/creds.json", "/container/creds.json")],
            rw_volumes=[("/home/user/.codex", "/root/.codex")],
            env_keys=["API_KEY"],
        )
        v_indices = [i for i, x in enumerate(cmd) if x == "-v"]
        # workdir + ro volume + rw volume = 3
        assert len(v_indices) == 3
        assert ":/workspace" in cmd[v_indices[0] + 1]
        assert cmd[v_indices[1] + 1] == "/host/creds.json:/container/creds.json:ro"
        assert cmd[v_indices[2] + 1] == "/home/user/.codex:/root/.codex"

    def test_custom_container_workdir(self, tmp_path):
        workdir = tmp_path / "project"
        workdir.mkdir()
        cmd = build_docker_cmd(
            "myimage:latest",
            ["bash", "verify.sh"],
            workdir=workdir,
            container_workdir="/tmp/_verify_abc123",
        )
        v_idx = cmd.index("-v")
        assert ":/tmp/_verify_abc123" in cmd[v_idx + 1]
        w_idx = cmd.index("-w")
        assert cmd[w_idx + 1] == "/tmp/_verify_abc123"
        # Default /workspace should not appear
        assert "/workspace" not in cmd[v_idx + 1]


class TestEnsureDocker:
    def test_daemon_not_available(self):
        with patch("automission.docker.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("docker not found")
            with pytest.raises(RuntimeError, match="Docker is not available"):
                ensure_docker("myimage:latest")

    def test_daemon_check_fails(self):
        with patch("automission.docker.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "docker version")
            with pytest.raises(RuntimeError, match="Docker is not available"):
                ensure_docker("myimage:latest")

    def test_image_exists_no_pull(self):
        with patch("automission.docker.subprocess.run") as mock_run:
            # docker version succeeds, docker image inspect succeeds
            mock_run.side_effect = [
                MagicMock(returncode=0),  # docker version
                MagicMock(returncode=0),  # docker image inspect
            ]
            ensure_docker("myimage:latest")
            assert mock_run.call_count == 2

    def test_image_missing_auto_pull_succeeds(self):
        with patch("automission.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # docker version
                MagicMock(returncode=1),  # docker image inspect (not found)
                MagicMock(returncode=0),  # docker pull
            ]
            ensure_docker("myimage:latest")
            assert mock_run.call_count == 3

    def test_image_missing_auto_pull_fails(self):
        with patch("automission.docker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # docker version
                MagicMock(returncode=1),  # docker image inspect (not found)
                MagicMock(returncode=1, stderr=b"not found"),  # docker pull fails
            ]
            with pytest.raises(RuntimeError, match="auto-pull failed"):
                ensure_docker("myimage:latest")

    def test_invalid_image_name(self):
        with pytest.raises(ValueError, match="Invalid Docker image name"):
            ensure_docker("invalid image!!")
