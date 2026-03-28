"""Codex CLI structured output backend."""

from __future__ import annotations

import json
import logging
import subprocess

from automission.docker import build_docker_cmd
from automission.structured_output._errors import (
    CLIResponseError,
    SchemaValidationError,
    _validate_schema,
)

logger = logging.getLogger(__name__)


class CodexStructuredOutput:
    """Structured output via `codex exec --output-schema`."""

    def __init__(
        self,
        docker_image: str = "ghcr.io/codance-ai/automission:latest",
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
        """Call codex exec with --output-schema and return parsed structured output."""
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
        """Run codex CLI and return parsed structured output dict."""
        schema_str = json.dumps(json_schema)
        inner_cmd = [
            "codex",
            "exec",
            prompt,
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            "--output-schema",
            schema_str,
        ]
        from automission.config import get_oauth_volumes

        env_keys = ["CODEX_API_KEY"] if self.auth_method == "api_key" else []
        oauth_vols = get_oauth_volumes("codex", self.auth_method)
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
            raise CLIResponseError(f"Failed to run codex CLI: {e}")

        if result.returncode != 0:
            stderr = result.stderr[:500]
            if len(result.stderr) > 500:
                stderr += " ... [truncated]"
            raise CLIResponseError(
                f"CLI exited with exit code {result.returncode}: {stderr}"
            )

        # Parse JSONL stream — find structured output from item.completed messages
        structured_output = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Extract from item.completed → message → content → output_text
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "message":
                    for content_block in item.get("content", []):
                        if content_block.get("type") == "output_text":
                            text = content_block.get("text", "")
                            try:
                                structured_output = json.loads(text)
                            except json.JSONDecodeError:
                                pass

        if structured_output is None:
            raise CLIResponseError(
                "Could not extract structured output from Codex JSONL stream"
            )
        if not isinstance(structured_output, dict):
            raise CLIResponseError(
                f"Structured output is not a dict, got {type(structured_output).__name__}: {structured_output!r}"
            )

        return structured_output
