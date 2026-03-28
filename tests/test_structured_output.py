"""Tests for structured output module — Claude backend, validation, factory."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from automission.structured_output import (
    CLIResponseError,
    ClaudeStructuredOutput,
    SchemaValidationError,
    create_structured_backend,
)
from automission.structured_output.claude import _validate_schema


# ── Helpers ──


def _cli_output(structured: dict) -> str:
    return json.dumps(
        {
            "type": "result",
            "result": "",
            "structured_output": structured,
        }
    )


SIMPLE_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


# ── ClaudeStructuredOutput tests ──


class TestClaudeStructuredOutput:
    def test_successful_query(self):
        backend = ClaudeStructuredOutput()
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=_cli_output({"x": "hello"}), stderr=""
            )
            result = backend.query("test prompt", "claude-sonnet-4-6", SIMPLE_SCHEMA)
        assert result == {"x": "hello"}

    def test_passes_correct_cli_args(self):
        backend = ClaudeStructuredOutput()
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=_cli_output({"x": "hello"}), stderr=""
            )
            backend.query("do something", "claude-sonnet-4-6", SIMPLE_SCHEMA)
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
        backend = ClaudeStructuredOutput()
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with pytest.raises(CLIResponseError, match="exit code 1"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_invalid_json_raises(self):
        backend = ClaudeStructuredOutput()
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="not json", stderr=""
            )
            with pytest.raises(CLIResponseError, match="parse"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_timeout_raises(self):
        backend = ClaudeStructuredOutput()
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=300)
            with pytest.raises(CLIResponseError, match="timed out"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_missing_structured_output_raises(self):
        backend = ClaudeStructuredOutput()
        cli_output = json.dumps({"type": "result", "result": "text"})
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=cli_output, stderr=""
            )
            with pytest.raises(CLIResponseError, match="missing 'structured_output'"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_oserror_raises(self):
        backend = ClaudeStructuredOutput()
        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("command not found")
            with pytest.raises(CLIResponseError, match="Failed to run"):
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_structured_output_not_dict_raises(self):
        backend = ClaudeStructuredOutput()
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
                backend.query("prompt", "model", SIMPLE_SCHEMA)

    def test_schema_validation_failure_retries(self):
        """First call returns invalid data, retry returns valid data."""
        backend = ClaudeStructuredOutput()
        bad_output = _cli_output({"x": 123})  # x should be string
        good_output = _cli_output({"x": "valid"})

        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=bad_output, stderr=""),
                MagicMock(returncode=0, stdout=good_output, stderr=""),
            ]
            result = backend.query("prompt", "model", schema)
        assert result == {"x": "valid"}
        assert mock_run.call_count == 2

    def test_schema_validation_double_failure_raises(self):
        """Both attempts fail schema validation."""
        backend = ClaudeStructuredOutput()
        bad_output = _cli_output({"x": 123})
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

        with patch("automission.structured_output.claude.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=bad_output, stderr=""
            )
            with pytest.raises(SchemaValidationError):
                backend.query("prompt", "model", schema)


# ── Schema validation tests ──


class TestValidateSchema:
    def test_valid_data_passes(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        _validate_schema({"x": "hello"}, schema)  # should not raise

    def test_invalid_data_raises(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        with pytest.raises(SchemaValidationError):
            _validate_schema({"x": 123}, schema)

    def test_missing_required_field_raises(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
        with pytest.raises(SchemaValidationError):
            _validate_schema({}, schema)


# ── Factory tests ──


class TestFactory:
    def test_create_claude_backend(self):
        backend = create_structured_backend("claude")
        assert isinstance(backend, ClaudeStructuredOutput)

    def test_create_codex_backend(self):
        from automission.structured_output.codex import CodexStructuredOutput

        backend = create_structured_backend("codex")
        assert isinstance(backend, CodexStructuredOutput)

    def test_create_gemini_backend(self):
        from automission.structured_output.gemini import GeminiStructuredOutput

        backend = create_structured_backend("gemini")
        assert isinstance(backend, GeminiStructuredOutput)

    def test_unsupported_backend_raises(self):
        with pytest.raises(ValueError, match="does not support structured output"):
            create_structured_backend("nonexistent_backend")
