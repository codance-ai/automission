"""Tests for critic (LLM analysis)."""

from unittest.mock import Mock

import pytest

from automission.models import AcceptanceGroup, Criterion, CriticResult, HarnessResult
from automission.critic import Critic
from automission.structured_output import CLIResponseError


@pytest.fixture
def sample_groups():
    return [
        AcceptanceGroup(
            id="basic",
            name="basic_operations",
            criteria=[
                Criterion(id="c1", group_id="basic", text="add works"),
                Criterion(id="c2", group_id="basic", text="subtract works"),
            ],
        ),
    ]


@pytest.fixture
def failing_harness():
    return HarnessResult(
        passed=False,
        exit_code=1,
        stdout="FAILED: test_add",
        stderr="AssertionError",
    )


@pytest.fixture
def passing_harness():
    return HarnessResult(passed=True, exit_code=0, stdout="All tests passed")


class TestCritic:
    def test_analyze_returns_critic_result(self, sample_groups, failing_harness):
        critic_output = {
            "summary": "add() not implemented",
            "root_cause": "Function missing",
            "next_actions": ["Implement add function"],
            "blockers": [],
            "group_analysis": [{"group_id": "basic", "completed": False}],
        }
        backend = Mock()
        backend.query = Mock(return_value=critic_output)
        critic = Critic(backend=backend)
        result = critic.analyze(failing_harness, sample_groups)

        assert isinstance(result, CriticResult)
        assert result.summary == "add() not implemented"
        assert result.root_cause == "Function missing"
        assert result.next_actions == ["Implement add function"]
        assert result.group_analysis == {"basic": False}

    def test_analyze_passing(self, sample_groups, passing_harness):
        critic_output = {
            "summary": "All tests pass.",
            "root_cause": "",
            "next_actions": [],
            "blockers": [],
            "group_analysis": [{"group_id": "basic", "completed": True}],
        }
        backend = Mock()
        backend.query = Mock(return_value=critic_output)
        critic = Critic(backend=backend)
        result = critic.analyze(passing_harness, sample_groups)

        assert result.group_analysis == {"basic": True}
        assert result.summary == "All tests pass."

    def test_malformed_group_analysis(self, sample_groups, failing_harness):
        critic_output = {
            "summary": "test",
            "root_cause": "",
            "next_actions": [],
            "blockers": [],
            "group_analysis": [{"bad_key": "oops"}],
        }
        backend = Mock()
        backend.query = Mock(return_value=critic_output)
        critic = Critic(backend=backend)
        result = critic.analyze(failing_harness, sample_groups)

        assert result.group_analysis == {}
        assert "Critic error" in result.summary

    def test_cli_failure_returns_empty(self, sample_groups, failing_harness):
        backend = Mock()
        backend.query = Mock(side_effect=CLIResponseError("CLI error"))
        critic = Critic(backend=backend)
        result = critic.analyze(failing_harness, sample_groups)

        assert result.group_analysis == {}
        assert "Critic error" in result.summary
