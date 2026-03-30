"""Gemini CLI structured output backend."""

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


class GeminiStructuredOutput:
    """Structured output via Gemini CLI with prompt-based schema enforcement."""

    def __init__(
        self,
        docker_image: str = DEFAULT_DOCKER_IMAGE,
        auth_method: str = "api_key",
    ):
        self.docker_image = docker_image
        self.auth_method = auth_method

    def query(
        self,
        prompt: str,
        model: str,
        json_schema: dict,
        timeout: int = 300,
    ) -> dict:
        """Call gemini -p with schema in prompt, validate, and return parsed output."""
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
        """Run gemini CLI and return parsed structured output dict."""
        schema_str = json.dumps(json_schema, indent=2)
        augmented_prompt = (
            f"{prompt}\n\n"
            f"IMPORTANT: Respond with ONLY a JSON object matching this exact schema:\n"
            f"```json\n{schema_str}\n```\n"
            f"No other text, explanation, or markdown. Just the raw JSON object."
        )
        inner_cmd = [
            "gemini",
            "-p",
            augmented_prompt,
            "--yolo",
            "--output-format",
            "json",
        ]
        from automission.config import get_oauth_volumes

        env_keys = ["GEMINI_API_KEY"] if self.auth_method == "api_key" else []
        oauth_vols = get_oauth_volumes("gemini", self.auth_method)
        rw_volumes = [(h, c) for h, c, _mode in oauth_vols] or None
        cmd = build_docker_cmd(
            self.docker_image, inner_cmd, env_keys=env_keys, rw_volumes=rw_volumes
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
            raise CLIResponseError(f"Failed to run gemini CLI: {e}")

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
            raise CLIResponseError(f"Failed to parse Gemini output as JSON: {e}")

        # Gemini JSON mode returns {session_id, response, stats}
        # response contains the actual LLM output
        response = output.get("response")
        if response is None:
            raise CLIResponseError(
                f"Gemini output missing 'response' key, got keys: {list(output.keys())}"
            )

        # response may be a JSON string or already a dict
        if isinstance(response, str):
            try:
                value = json.loads(response)
            except json.JSONDecodeError as e:
                raise CLIResponseError(f"Failed to parse Gemini response as JSON: {e}")
        elif isinstance(response, dict):
            value = response
        else:
            raise CLIResponseError(
                f"Unexpected response type: {type(response).__name__}"
            )

        if not isinstance(value, dict):
            raise CLIResponseError(
                f"Structured output is not a dict, got {type(value).__name__}: {value!r}"
            )

        return value
