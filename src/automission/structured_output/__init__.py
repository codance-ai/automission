"""Structured output backends for Planner/Critic."""

from automission.structured_output.protocol import StructuredOutputBackend
from automission.structured_output._errors import (
    CLIResponseError,
    SchemaValidationError,
)
from automission.structured_output.claude import ClaudeStructuredOutput
from automission.structured_output.codex import CodexStructuredOutput
from automission.structured_output.gemini import GeminiStructuredOutput
from automission.structured_output.factory import create_structured_backend

__all__ = [
    "StructuredOutputBackend",
    "CLIResponseError",
    "ClaudeStructuredOutput",
    "CodexStructuredOutput",
    "GeminiStructuredOutput",
    "SchemaValidationError",
    "create_structured_backend",
]
