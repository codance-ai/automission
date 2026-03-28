"""Shared error classes and schema validation for structured output backends."""

from __future__ import annotations

import jsonschema


class CLIResponseError(Exception):
    """Raised when the CLI call fails or returns unparseable output."""


class SchemaValidationError(Exception):
    """Raised when jsonschema validation fails."""


def _validate_schema(data: dict, schema: dict) -> None:
    """Validate data against JSON schema. Raises SchemaValidationError on failure."""
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        raise SchemaValidationError(f"Schema validation failed: {e.message}") from e
