"""Shared CLI caller for LLM interactions via `claude -p`.

Deprecated: Use automission.structured_output instead.
This module is kept for backward compatibility.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from automission.structured_output.claude import CLIResponseError  # noqa: E402, F401  # Re-export for backward compatibility


def call_claude_cli(
    prompt: str,
    model: str,
    json_schema: dict,
    timeout: int = 300,
) -> dict:
    """Call `claude -p` with --json-schema and return parsed JSON response.

    Deprecated: Use ClaudeStructuredOutput.query() instead.
    """
    from automission.structured_output import ClaudeStructuredOutput

    backend = ClaudeStructuredOutput()
    return backend.query(
        prompt=prompt, model=model, json_schema=json_schema, timeout=timeout
    )
