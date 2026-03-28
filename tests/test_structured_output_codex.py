"""Tests for Codex structured output backend."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from automission.structured_output._errors import (
    CLIResponseError,
    SchemaValidationError,
)
from automission.structured_output.codex import CodexStructuredOutput


def _codex_jsonl(*events: dict) -> str:
    """Build JSONL string from events."""
    return "\n".join(json.dumps(e) for e in events)


def _codex_message_event(structured_data: dict) -> dict:
    """Build an item.completed event with a message containing structured output."""
    return {
        "type": "item.completed",
        "item": {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": json.dumps(structured_data),
                }
            ],
        },
    }


SIMPLE_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


class TestCodexStructuredOutput:
    def test_successful_query(self):
        backend = CodexStructuredOutput()
        output = _codex_jsonl(_codex_message_event({"x": "hello"}))
        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            result = backend.query("test prompt", "gpt-4o", SIMPLE_SCHEMA)
        assert result == {"x": "hello"}

    def test_passes_correct_cli_args(self):
        backend = CodexStructuredOutput()
        output = _codex_jsonl(_codex_message_event({"x": "hello"}))
        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            backend.query("do something", "gpt-4o", SIMPLE_SCHEMA)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "codex" in cmd
        assert "exec" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--json" in cmd
        assert "--output-schema" in cmd

    def test_nonzero_exit_code_raises(self):
        backend = CodexStructuredOutput()
        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with pytest.raises(CLIResponseError, match="exit code 1"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_no_structured_output_in_stream_raises(self):
        backend = CodexStructuredOutput()
        output = _codex_jsonl({"type": "turn.completed", "usage": {}})
        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with pytest.raises(CLIResponseError, match="Could not extract"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_timeout_raises(self):
        backend = CodexStructuredOutput()
        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=300)
            with pytest.raises(CLIResponseError, match="timed out"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_oserror_raises(self):
        backend = CodexStructuredOutput()
        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("command not found")
            with pytest.raises(CLIResponseError, match="Failed to run"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_structured_output_not_dict_raises(self):
        backend = CodexStructuredOutput()
        output = _codex_jsonl(_codex_message_event("just a string"))
        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with pytest.raises(CLIResponseError, match="not a dict"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_schema_validation_failure_retries(self):
        backend = CodexStructuredOutput()
        bad_output = _codex_jsonl(
            _codex_message_event({"x": 123})
        )  # x should be string
        good_output = _codex_jsonl(_codex_message_event({"x": "valid"}))

        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=bad_output, stderr=""),
                MagicMock(returncode=0, stdout=good_output, stderr=""),
            ]
            result = backend.query("prompt", "model", schema)
        assert result == {"x": "valid"}
        assert mock_run.call_count == 2

    def test_schema_validation_double_failure_raises(self):
        backend = CodexStructuredOutput()
        bad_output = _codex_jsonl(_codex_message_event({"x": 123}))
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        with patch("automission.structured_output.codex.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=bad_output, stderr=""
            )
            with pytest.raises(SchemaValidationError):
                backend.query("prompt", "model", schema)
