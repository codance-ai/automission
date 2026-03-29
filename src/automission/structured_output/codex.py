"""Codex CLI structured output backend."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from automission.docker import build_docker_cmd
from automission.structured_output._errors import (
    CLIResponseError,
    SchemaValidationError,
    _validate_schema,
)

logger = logging.getLogger(__name__)


def _openai_strict_schema(schema: dict) -> dict:
    """Recursively make a JSON schema compatible with OpenAI structured output.

    OpenAI requires:
    1. ``additionalProperties: false`` on every object
    2. ``required`` must list ALL keys in ``properties``

    Returns a deep copy — the original schema is not mutated.
    """
    schema = dict(schema)
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        if "properties" in schema:
            schema["required"] = list(schema["properties"].keys())
            schema["properties"] = {
                k: _openai_strict_schema(v)
                for k, v in schema["properties"].items()
            }
    if "items" in schema:
        schema["items"] = _openai_strict_schema(schema["items"])
    return schema


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
        # OpenAI structured output has strict schema requirements.
        openai_schema = _openai_strict_schema(json_schema)
        # Codex --output-schema expects a file path, not inline JSON.
        # Write schema to a temp file on the host and mount it into the container.
        schema_tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        try:
            json.dump(openai_schema, schema_tmp)
            schema_tmp.close()
            schema_container = "/tmp/output-schema.json"

            inner_cmd = [
                "codex",
                "exec",
                prompt,
                "--dangerously-bypass-approvals-and-sandbox",
                "--json",
                "--output-schema",
                schema_container,
            ]
            from automission.config import get_oauth_volumes

            env_keys = ["CODEX_API_KEY"] if self.auth_method == "api_key" else []
            oauth_vols = get_oauth_volumes("codex", self.auth_method)
            rw_volumes = [(h, c) for h, c, _mode in oauth_vols] or None
            volumes = [(schema_tmp.name, schema_container)]
            cmd = build_docker_cmd(
                self.docker_image,
                inner_cmd,
                env_keys=env_keys,
                volumes=volumes,
                rw_volumes=rw_volumes,
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
        finally:
            Path(schema_tmp.name).unlink(missing_ok=True)

        if result.returncode != 0:
            # Codex often reports errors in stdout JSONL, not stderr
            raw = result.stderr or result.stdout
            detail = raw[:500]
            if len(raw) > 500:
                detail += " ... [truncated]"
            raise CLIResponseError(
                f"CLI exited with exit code {result.returncode}: {detail}"
            )

        # Parse JSONL stream — find structured output from item.completed events.
        # Codex emits two item formats:
        #   - agent_message: item.text contains the JSON string directly
        #   - message: item.content[].output_text contains the JSON string
        structured_output = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "item.completed":
                continue
            item = event.get("item", {})
            item_type = item.get("type", "")

            texts: list[str] = []
            if item_type == "agent_message" and "text" in item:
                texts.append(item["text"])
            elif item_type == "message":
                for content_block in item.get("content", []):
                    if content_block.get("type") == "output_text":
                        texts.append(content_block.get("text", ""))

            for text in texts:
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
