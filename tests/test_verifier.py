"""Tests for verifier (gate + critic)."""

from unittest.mock import Mock
import json

import pytest

from automission.models import AcceptanceGroup, Criterion
from automission.verifier import Verifier, run_verify_sh


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


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


class TestVerifyShGate:
    def test_passing_script(self, workspace):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\necho 'all tests passed'\nexit 0\n")
        script.chmod(0o755)

        result = run_verify_sh(workspace, script)
        assert result["passed"] is True
        assert result["exit_code"] == 0
        assert "all tests passed" in result["stdout"]

    def test_failing_script(self, workspace):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\necho 'FAILED: test_add'\nexit 1\n")
        script.chmod(0o755)

        result = run_verify_sh(workspace, script)
        assert result["passed"] is False
        assert result["exit_code"] == 1

    def test_script_with_json_output(self, workspace):
        json_output = json.dumps(
            {
                "passed": False,
                "score": 0.6,
                "metrics": {"test_pass_rate": "6/10"},
            }
        )
        script = workspace / "verify.sh"
        script.write_text(f"#!/bin/bash\necho '{json_output}'\nexit 1\n")
        script.chmod(0o755)

        result = run_verify_sh(workspace, script)
        assert result["passed"] is False
        assert result["json_output"] is not None
        assert result["json_output"]["score"] == 0.6

    def test_missing_script(self, workspace):
        result = run_verify_sh(workspace, workspace / "nonexistent.sh")
        assert result["passed"] is False


class TestVerifier:
    def test_gate_pass_no_critic(self, workspace, sample_groups):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        script.chmod(0o755)

        verifier = Verifier()
        result = verifier.evaluate(workspace, script, sample_groups)

        assert result.contract_passed is True
        assert result.gate_source == "script"

    def test_gate_fail_no_critic(self, workspace, sample_groups):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\necho 'test_add FAILED'\nexit 1\n")
        script.chmod(0o755)

        verifier = Verifier()
        result = verifier.evaluate(workspace, script, sample_groups)

        assert result.contract_passed is False
        assert result.gate_source == "script"

    def test_with_mock_critic(self, workspace, sample_groups):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\necho 'test_add FAILED'\nexit 1\n")
        script.chmod(0o755)

        critic_output = {
            "passed_criteria": [
                {"criterion": "subtract works", "passed": True, "detail": "ok"}
            ],
            "failed_criteria": [
                {"criterion": "add works", "passed": False, "detail": "not implemented"}
            ],
            "group_statuses": {"basic": False},
            "suggestion": "Implement the add function",
            "reason": "add() is not defined",
            "score": 0.5,
        }

        backend = Mock()
        backend.query = Mock(return_value=critic_output)
        verifier = Verifier(backend=backend, verifier_model="claude-sonnet-4-6")
        result = verifier.evaluate(workspace, script, sample_groups)

        assert result.contract_passed is False
        assert result.gate_source == "script"
        assert len(result.passed_criteria) == 1
        assert len(result.failed_criteria) == 1
        assert result.suggestion == "Implement the add function"
        assert result.group_statuses == {"basic": False}

    def test_gate_pass_sets_mission_passed_when_all_groups_done(
        self, workspace, sample_groups
    ):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        script.chmod(0o755)

        critic_output = {
            "passed_criteria": [
                {"criterion": "add works", "passed": True, "detail": "ok"},
                {"criterion": "subtract works", "passed": True, "detail": "ok"},
            ],
            "failed_criteria": [],
            "group_statuses": {"basic": True},
            "suggestion": "",
            "reason": "All criteria pass",
            "score": 1.0,
        }

        backend = Mock()
        backend.query = Mock(return_value=critic_output)
        verifier = Verifier(backend=backend, verifier_model="claude-sonnet-4-6")
        result = verifier.evaluate(workspace, script, sample_groups)

        assert result.contract_passed is True
        assert result.mission_passed is True

    def test_critic_cli_failure_falls_back_to_basic(self, workspace, sample_groups):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\nexit 1\n")
        script.chmod(0o755)

        from automission.structured_output import CLIResponseError

        backend = Mock()
        backend.query = Mock(side_effect=CLIResponseError("CLI error"))
        verifier = Verifier(backend=backend, verifier_model="claude-sonnet-4-6")
        result = verifier.evaluate(workspace, script, sample_groups)

        # Should fall back to basic critic
        assert result.contract_passed is False
        assert result.reason == "Gate verification failed."
