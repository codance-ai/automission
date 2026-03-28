"""Tests for Codex agent backend."""

from unittest.mock import patch, MagicMock
import json
import subprocess

import pytest

from automission.models import AttemptSpec, StableContext
from automission.backend.codex import CodexBackend, _parse_codex_output


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


class TestCodexBackend:
    def test_prepare_workspace(self, workspace):
        backend = CodexBackend()
        stable = StableContext(goal="Build calculator")
        backend.prepare_workspace(workspace, stable)

        assert (workspace / "AUTOMISSION.md").exists()
        assert (workspace / "AGENTS.md").exists()
        assert "Build calculator" in (workspace / "AUTOMISSION.md").read_text()
        assert "AUTOMISSION.md" in (workspace / "AGENTS.md").read_text()

    def test_prepare_workspace_appends_to_existing_agents_md(self, workspace):
        (workspace / "AGENTS.md").write_text("# Existing rules\nBe nice.\n")
        backend = CodexBackend()
        stable = StableContext(goal="Test")
        backend.prepare_workspace(workspace, stable)

        content = (workspace / "AGENTS.md").read_text()
        assert "Existing rules" in content
        assert "AUTOMISSION.md" in content

    @patch("automission.backend._helpers.subprocess.run")
    def test_run_attempt_calls_codex(self, mock_run, workspace):
        jsonl = "\n".join(
            [
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 500, "output_tokens": 200},
                    }
                ),
            ]
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=jsonl.encode(),
            stderr=b"",
        )
        backend = CodexBackend()
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
        assert "codex" in cmd
        assert "exec" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--json" in cmd
        assert result.status == "completed"
        assert result.token_usage.input_tokens == 500
        assert result.token_usage.output_tokens == 200

    @patch("automission.backend._helpers.subprocess.run")
    def test_run_attempt_timeout(self, mock_run, workspace):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=300)
        backend = CodexBackend()
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
        mock_run.side_effect = OSError("codex not found")
        backend = CodexBackend()
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
            stdout=b"",
            stderr=b"",
        )
        backend = CodexBackend(model="o3")
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
        assert cmd[idx + 1] == "o3"


class TestParseCodexOutput:
    def test_parses_turn_completed(self):
        jsonl = json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1000, "output_tokens": 500},
            }
        )
        cost, usage = _parse_codex_output(jsonl.encode())
        assert cost == 0.0
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500

    def test_accumulates_multiple_turns(self):
        lines = "\n".join(
            [
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 100, "output_tokens": 50},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 200, "output_tokens": 100},
                    }
                ),
            ]
        )
        cost, usage = _parse_codex_output(lines.encode())
        assert usage.input_tokens == 300
        assert usage.output_tokens == 150

    def test_ignores_non_turn_events(self):
        lines = "\n".join(
            [
                json.dumps({"type": "item.created"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 100, "output_tokens": 50},
                    }
                ),
                json.dumps({"type": "item.completed", "item": {}}),
            ]
        )
        cost, usage = _parse_codex_output(lines.encode())
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_handles_empty_output(self):
        cost, usage = _parse_codex_output(b"")
        assert cost == 0.0
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_handles_malformed_json(self):
        cost, usage = _parse_codex_output(b"not json\n{broken")
        assert cost == 0.0
        assert usage.input_tokens == 0
