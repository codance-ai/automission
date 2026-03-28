"""StructuredOutputBackend protocol — interface for structured LLM queries."""

from __future__ import annotations

from typing import Protocol


class StructuredOutputBackend(Protocol):
    """Send a prompt with JSON schema constraint and return parsed dict."""

    def query(
        self,
        prompt: str,
        model: str,
        json_schema: dict,
        timeout: int = 300,
    ) -> dict:
        """Send prompt with schema constraint, return parsed JSON dict."""
        ...
