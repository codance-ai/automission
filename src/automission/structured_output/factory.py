"""Factory for creating structured output backends."""

from __future__ import annotations

from automission.structured_output.protocol import StructuredOutputBackend


def create_structured_backend(
    name: str,
    docker_image: str = "ghcr.io/codance-ai/automission:latest",
    auth_method: str = "api_key",
) -> StructuredOutputBackend:
    """Create a structured output backend by name.

    Supported: 'claude', 'codex', 'gemini'.
    Raises ValueError for unsupported backends.
    """
    if name == "claude":
        from automission.structured_output.claude import ClaudeStructuredOutput

        return ClaudeStructuredOutput(docker_image=docker_image)
    if name == "codex":
        from automission.structured_output.codex import CodexStructuredOutput

        return CodexStructuredOutput(docker_image=docker_image, auth_method=auth_method)
    if name == "gemini":
        from automission.structured_output.gemini import GeminiStructuredOutput

        return GeminiStructuredOutput(
            docker_image=docker_image, auth_method=auth_method
        )
    raise ValueError(
        f"Backend '{name}' does not support structured output. "
        f"Supported: claude, codex, gemini"
    )
