"""Tests for Gemini structured output backend."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from automission.structured_output._errors import (
    CLIResponseError,
    SchemaValidationError,
)
from automission.structured_output.gemini import GeminiStructuredOutput


def _gemini_output(response_data) -> str:
    """Build Gemini JSON output with response field."""
    return json.dumps(
        {
            "session_id": "test-session",
            "response": response_data
            if isinstance(response_data, str)
            else response_data,
            "stats": {"tokens_input": 100, "tokens_output": 50},
        }
    )


def _gemini_output_response_str(data: dict) -> str:
    """Build Gemini JSON output where response is a JSON string."""
    return json.dumps(
        {
            "session_id": "test-session",
            "response": json.dumps(data),
            "stats": {"tokens_input": 100, "tokens_output": 50},
        }
    )


SIMPLE_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


class TestGeminiStructuredOutput:
    def test_successful_query_dict_response(self):
        """response is already a dict."""
        backend = GeminiStructuredOutput()
        output = _gemini_output({"x": "hello"})
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            result = backend.query("test prompt", "gemini-pro", SIMPLE_SCHEMA)
        assert result == {"x": "hello"}

    def test_successful_query_string_response(self):
        """response is a JSON string that needs parsing."""
        backend = GeminiStructuredOutput()
        output = _gemini_output_response_str({"x": "hello"})
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            result = backend.query("test prompt", "gemini-pro", SIMPLE_SCHEMA)
        assert result == {"x": "hello"}

    def test_passes_correct_cli_args(self):
        backend = GeminiStructuredOutput()
        output = _gemini_output({"x": "hello"})
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            backend.query("do something", "gemini-pro", SIMPLE_SCHEMA)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "gemini" in cmd
        assert "-p" in cmd
        assert "--yolo" in cmd
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

    def test_schema_embedded_in_prompt(self):
        """Verify the JSON schema is embedded in the augmented prompt."""
        backend = GeminiStructuredOutput()
        output = _gemini_output({"x": "hello"})
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            backend.query("original prompt", "gemini-pro", SIMPLE_SCHEMA)
        cmd = mock_run.call_args[0][0]
        # Find the prompt argument (after -p)
        p_idx = cmd.index("-p")
        augmented = cmd[p_idx + 1]
        assert "original prompt" in augmented
        assert '"type": "object"' in augmented

    def test_nonzero_exit_code_raises(self):
        backend = GeminiStructuredOutput()
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with pytest.raises(CLIResponseError, match="exit code 1"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_missing_response_key_raises(self):
        backend = GeminiStructuredOutput()
        output = json.dumps({"session_id": "test", "stats": {}})
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with pytest.raises(CLIResponseError, match="missing 'response'"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_invalid_json_response_raises(self):
        """response is a string but not valid JSON."""
        backend = GeminiStructuredOutput()
        output = json.dumps(
            {
                "session_id": "test",
                "response": "not valid json {{{",
                "stats": {},
            }
        )
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with pytest.raises(CLIResponseError, match="parse Gemini response"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_timeout_raises(self):
        backend = GeminiStructuredOutput()
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gemini", timeout=300)
            with pytest.raises(CLIResponseError, match="timed out"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_oserror_raises(self):
        backend = GeminiStructuredOutput()
        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("command not found")
            with pytest.raises(CLIResponseError, match="Failed to run"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_schema_validation_failure_retries(self):
        backend = GeminiStructuredOutput()
        bad_output = _gemini_output({"x": 123})
        good_output = _gemini_output({"x": "valid"})

        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=bad_output, stderr=""),
                MagicMock(returncode=0, stdout=good_output, stderr=""),
            ]
            result = backend.query("prompt", "model", schema)
        assert result == {"x": "valid"}
        assert mock_run.call_count == 2

    def test_schema_validation_double_failure_raises(self):
        backend = GeminiStructuredOutput()
        bad_output = _gemini_output({"x": 123})
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        with patch("automission.structured_output.gemini.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=bad_output, stderr=""
            )
            with pytest.raises(SchemaValidationError):
                backend.query("prompt", "model", schema)
