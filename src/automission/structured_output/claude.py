"""Claude CLI structured output backend."""

from __future__ import annotations

import json
import logging
import subprocess

from automission import DEFAULT_DOCKER_IMAGE
from automission.docker import build_docker_cmd
from automission.structured_output._errors import (
    CLIResponseError,
    SchemaValidationError,
    _validate_schema,
)

logger = logging.getLogger(__name__)


class ClaudeStructuredOutput:
    """Structured output via `claude -p --json-schema`."""

    def __init__(self, docker_image: str = DEFAULT_DOCKER_IMAGE):
        self.docker_image = docker_image

    def query(
        self,
        prompt: str,
        model: str,
        json_schema: dict,
        timeout: int = 300,
    ) -> dict:
        """Call `claude -p` with --json-schema and return parsed structured output."""
        value = self._invoke_cli(prompt, model, json_schema, timeout)

        # Local schema validation with one retry
        try:
            _validate_schema(value, json_schema)
        except SchemaValidationError:
            logger.warning("Schema validation failed on first attempt, retrying...")
            value = self._invoke_cli(prompt, model, json_schema, timeout)
            _validate_schema(value, json_schema)  # raises on second failure

        return value

    def _invoke_cli(
        self,
        prompt: str,
        model: str,
        json_schema: dict,
        timeout: int,
    ) -> dict:
        """Run claude CLI and return parsed structured_output dict."""
        schema_str = json.dumps(json_schema)
        inner_cmd = [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--json-schema",
            schema_str,
        ]
        cmd = build_docker_cmd(
            self.docker_image, inner_cmd, env_keys=["ANTHROPIC_API_KEY"]
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise CLIResponseError(f"CLI call timed out after {timeout}s")
        except OSError as e:
            raise CLIResponseError(f"Failed to run claude CLI: {e}")

        if result.returncode != 0:
            stderr = result.stderr[:500]
            if len(result.stderr) > 500:
                stderr += " ... [truncated]"
            raise CLIResponseError(
                f"CLI exited with exit code {result.returncode}: {stderr}"
            )

        try:
            output = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as e:
            raise CLIResponseError(f"Failed to parse CLI output as JSON: {e}")

        if "structured_output" not in output:
            raise CLIResponseError(
                f"CLI output missing 'structured_output' key, got keys: {list(output.keys())}"
            )
        value = output["structured_output"]
        if not isinstance(value, dict):
            raise CLIResponseError(
                f"'structured_output' is not a dict, got {type(value).__name__}: {value!r}"
            )

        return value
