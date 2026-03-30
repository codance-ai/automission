"""Tests for shared backend helpers."""

from unittest.mock import patch, MagicMock
import subprocess


from automission.models import AttemptSpec, TokenUsage
from automission.backend._helpers import (
    write_instruction_pointer,
    run_docker_attempt,
)


class TestWriteInstructionPointer:
    def test_creates_new_file(self, tmp_path):
        write_instruction_pointer(tmp_path, "TEST.md", "pointer text")
        assert (tmp_path / "TEST.md").read_text() == "pointer text"

    def test_appends_to_existing(self, tmp_path):
        (tmp_path / "TEST.md").write_text("existing content")
        write_instruction_pointer(tmp_path, "TEST.md", "\nnew pointer")
        content = (tmp_path / "TEST.md").read_text()
        assert "existing content" in content
        assert "new pointer" in content


class TestRunDockerAttempt:
    @patch("automission.backend._helpers.subprocess.run")
    @patch("automission.backend._helpers._git_file_set", return_value=set())
    def test_successful_attempt(self, mock_git, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"{}")

        def parse(stdout):
            return 0.5, TokenUsage(input_tokens=100, output_tokens=50)

        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
            timeout_s=300,
        )
        result = run_docker_attempt(spec, "img:latest", ["cmd"], ["KEY"], parse)
        assert result.status == "completed"
        assert result.cost_usd == 0.5

    @patch("automission.backend._helpers.subprocess.run")
    @patch("automission.backend._helpers._git_file_set", return_value=set())
    def test_failed_attempt(self, mock_git, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout=b"{}")

        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
            timeout_s=300,
        )
        result = run_docker_attempt(
            spec,
            "img:latest",
            ["cmd"],
            ["KEY"],
            lambda s: (0.0, TokenUsage()),
        )
        assert result.status == "failed"

    @patch("automission.backend._helpers.subprocess.run")
    @patch("automission.backend._helpers._git_file_set", return_value=set())
    def test_timeout(self, mock_git, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="cmd", timeout=300)

        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
            timeout_s=300,
        )
        result = run_docker_attempt(
            spec,
            "img:latest",
            ["cmd"],
            ["KEY"],
            lambda s: (0.0, TokenUsage()),
        )
        assert result.status == "timed_out"

    @patch("automission.backend._helpers.subprocess.run")
    @patch("automission.backend._helpers._git_file_set", return_value=set())
    def test_crash(self, mock_git, mock_run, tmp_path):
        mock_run.side_effect = OSError("not found")

        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
            timeout_s=300,
        )
        result = run_docker_attempt(
            spec,
            "img:latest",
            ["cmd"],
            ["KEY"],
            lambda s: (0.0, TokenUsage()),
        )
        assert result.status == "crashed"


class TestRunDockerAttemptOutputSaving:
    @patch("automission.backend._helpers.subprocess.run")
    @patch("automission.backend._helpers._git_file_set", return_value=set())
    def test_saves_stdout_when_output_dir_set(self, mock_git, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b'{"cost_usd": 0.1}',
            stderr=b"debug info",
        )
        output_dir = tmp_path / "agent_outputs"
        spec = AttemptSpec(
            attempt_id="test-a1",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
            timeout_s=300,
            output_dir=output_dir,
        )
        result = run_docker_attempt(
            spec,
            "img:latest",
            ["cmd"],
            ["KEY"],
            lambda s: (0.5, TokenUsage(input_tokens=100, output_tokens=50)),
        )
        assert result.stdout_path is not None
        assert result.stdout_path.exists()
        assert b'{"cost_usd"' in result.stdout_path.read_bytes()
        assert result.stderr_path is not None
        assert result.stderr_path.exists()

    @patch("automission.backend._helpers.subprocess.run")
    @patch("automission.backend._helpers._git_file_set", return_value=set())
    def test_no_output_dir_means_no_files(self, mock_git, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"{}", stderr=b"")
        spec = AttemptSpec(
            attempt_id="test-a2",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
            timeout_s=300,
        )
        result = run_docker_attempt(
            spec,
            "img:latest",
            ["cmd"],
            ["KEY"],
            lambda s: (0.0, TokenUsage()),
        )
        assert result.stdout_path is None
        assert result.stderr_path is None

    @patch("automission.backend._helpers.subprocess.run")
    @patch("automission.backend._helpers._git_file_set", return_value=set())
    def test_no_stderr_file_when_stderr_empty(self, mock_git, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout=b'{"x":1}', stderr=b"")
        output_dir = tmp_path / "outputs"
        spec = AttemptSpec(
            attempt_id="test-a3",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
            timeout_s=300,
            output_dir=output_dir,
        )
        result = run_docker_attempt(
            spec,
            "img:latest",
            ["cmd"],
            ["KEY"],
            lambda s: (0.0, TokenUsage()),
        )
        assert result.stdout_path is not None
        assert result.stderr_path is None
