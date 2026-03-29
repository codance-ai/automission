"""Shared mock helpers for tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def mock_questionary_select(answers: list[str]):
    """Return a patch that makes questionary.select return *answers* in order.

    Raises AssertionError (instead of StopIteration) when called more times
    than there are answers, making test failures easier to diagnose.
    """
    it = iter(answers)
    call_count = 0

    def fake_select(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        try:
            value = next(it)
        except StopIteration:
            raise AssertionError(
                f"questionary.select called more times than expected "
                f"(call #{call_count}, only {len(answers)} answers provided), "
                f"args={args!r}"
            )
        mock_question = MagicMock()
        mock_question.ask.return_value = value
        return mock_question

    return patch("automission.cli.questionary.select", side_effect=fake_select)
