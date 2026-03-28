"""Tests for Gemini agent backend."""

from unittest.mock import patch, MagicMock
import json
import subprocess

import pytest

from automission.models import AttemptSpec, StableContext
from automission.backend.gemini import GeminiBackend, _parse_gemini_output


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    (ws / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True)
    return ws


class TestGeminiBackend:
    def test_prepare_workspace(self, workspace):
        backend = GeminiBackend()
        stable = StableContext(goal="Build calculator")
        backend.prepare_workspace(workspace, stable)

        assert (workspace / "AUTOMISSION.md").exists()
        assert (workspace / "GEMINI.md").exists()
        assert "Build calculator" in (workspace / "AUTOMISSION.md").read_text()
        assert "AUTOMISSION.md" in (workspace / "GEMINI.md").read_text()

    def test_prepare_workspace_appends_to_existing_gemini_md(self, workspace):
        (workspace / "GEMINI.md").write_text("# Existing rules\nBe nice.\n")
        backend = GeminiBackend()
        stable = StableContext(goal="Test")
        backend.prepare_workspace(workspace, stable)

        content = (workspace / "GEMINI.md").read_text()
        assert "Existing rules" in content
        assert "AUTOMISSION.md" in content

    @patch("automission.backend._helpers.subprocess.run")
    def test_run_attempt_calls_gemini(self, mock_run, workspace):
        output = json.dumps(
            {
                "session_id": "test",
                "response": "done",
                "stats": {"tokens_input": 800, "tokens_output": 300},
            }
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=output.encode(),
            stderr=b"",
        )
        backend = GeminiBackend()
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="write calculator",
            timeout_s=300,
        )
        result = backend.run_attempt(spec)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "gemini" in cmd
        assert "-p" in cmd
        assert "--yolo" in cmd
        assert "--output-format" in cmd
        assert result.status == "completed"
        assert result.token_usage.input_tokens == 800
        assert result.token_usage.output_tokens == 300

    @patch("automission.backend._helpers.subprocess.run")
    def test_run_attempt_timeout(self, mock_run, workspace):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gemini", timeout=300)
        backend = GeminiBackend()
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="test",
            timeout_s=300,
        )
        result = backend.run_attempt(spec)
        assert result.status == "timed_out"

    @patch("automission.backend._helpers.subprocess.run")
    def test_run_attempt_crash(self, mock_run, workspace):
        mock_run.side_effect = OSError("gemini not found")
        backend = GeminiBackend()
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="test",
            timeout_s=300,
        )
        result = backend.run_attempt(spec)
        assert result.status == "crashed"

    @patch("automission.backend._helpers.subprocess.run")
    def test_run_attempt_passes_model(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"{}",
            stderr=b"",
        )
        backend = GeminiBackend(model="gemini-2.5-pro")
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="test",
            timeout_s=300,
        )
        backend.run_attempt(spec)

        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "gemini-2.5-pro"


class TestParseGeminiOutput:
    def test_parses_stats(self):
        output = json.dumps(
            {
                "session_id": "s1",
                "response": "hello",
                "stats": {"tokens_input": 1000, "tokens_output": 500},
            }
        )
        cost, usage = _parse_gemini_output(output.encode())
        assert cost == 0.0
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500

    def test_empty_response_is_ok(self):
        """response can be empty for tool-only execution."""
        output = json.dumps(
            {
                "session_id": "s1",
                "response": "",
                "stats": {"tokens_input": 100, "tokens_output": 50},
            }
        )
        cost, usage = _parse_gemini_output(output.encode())
        assert usage.input_tokens == 100

    def test_missing_stats_returns_zero(self):
        output = json.dumps({"session_id": "s1", "response": "hello"})
        cost, usage = _parse_gemini_output(output.encode())
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_handles_empty_output(self):
        cost, usage = _parse_gemini_output(b"")
        assert cost == 0.0
        assert usage.input_tokens == 0

    def test_handles_malformed_json(self):
        cost, usage = _parse_gemini_output(b"not json")
        assert cost == 0.0
        assert usage.input_tokens == 0
