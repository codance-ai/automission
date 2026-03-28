"""Tests for shared LLM CLI caller."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from automission.llm import call_claude_cli, CLIResponseError


class TestCallClaudeCli:
    def test_successful_call_returns_structured_output(self):
        cli_output = json.dumps(
            {
                "type": "result",
                "result": "",
                "structured_output": {"mission_summary": "test", "groups": []},
                "cost_usd": 0.01,
                "input_tokens": 100,
                "output_tokens": 50,
            }
        )
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=cli_output, stderr=""
            )
            result = call_claude_cli(
                prompt="test prompt",
                model="claude-sonnet-4-6",
                json_schema={"type": "object", "properties": {}},
            )
        assert result == {"mission_summary": "test", "groups": []}

    def test_passes_correct_cli_args(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        cli_output = json.dumps(
            {
                "type": "result",
                "result": "",
                "structured_output": {"x": "hello"},
            }
        )
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=cli_output, stderr=""
            )
            call_claude_cli(
                prompt="do something",
                model="claude-sonnet-4-6",
                json_schema=schema,
            )
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "-p" in cmd
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-sonnet-4-6"
        assert "--json-schema" in cmd

    def test_nonzero_exit_code_raises(self):
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with pytest.raises(CLIResponseError, match="exit code 1"):
                call_claude_cli("prompt", "model", {"type": "object"})

    def test_invalid_json_output_raises(self):
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="not json", stderr=""
            )
            with pytest.raises(CLIResponseError, match="parse"):
                call_claude_cli("prompt", "model", {"type": "object"})

    def test_timeout_raises(self):
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=300)
            with pytest.raises(CLIResponseError, match="timed out"):
                call_claude_cli("prompt", "model", {"type": "object"})

    def test_missing_structured_output_key_raises(self):
        """CLI output without 'structured_output' key should raise."""
        cli_output = json.dumps({"type": "result", "result": "some text"})
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=cli_output, stderr=""
            )
            with pytest.raises(CLIResponseError, match="missing 'structured_output'"):
                call_claude_cli("prompt", "model", {"type": "object"})

    def test_oserror_raises(self):
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("command not found")
            with pytest.raises(CLIResponseError, match="Failed to run"):
                call_claude_cli("prompt", "model", {"type": "object"})

    def test_structured_output_not_dict_raises(self):
        """structured_output that is not a dict (e.g. None) should raise."""
        cli_output = json.dumps(
            {
                "type": "result",
                "result": "",
                "structured_output": None,
            }
        )
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=cli_output, stderr=""
            )
            with pytest.raises(CLIResponseError, match="not a dict"):
                call_claude_cli("prompt", "model", {"type": "object"})

    def test_error_response_missing_structured_output_raises(self):
        """Error-type CLI output without 'structured_output' should raise."""
        cli_output = json.dumps({"type": "error", "message": "something went wrong"})
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=cli_output, stderr=""
            )
            with pytest.raises(CLIResponseError, match="missing 'structured_output'"):
                call_claude_cli("prompt", "model", {"type": "object"})
