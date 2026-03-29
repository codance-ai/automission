"""Tests for harness (deterministic test execution)."""

import json

import pytest

from automission.models import HarnessResult, VerificationSurface
from automission.harness import Harness, run_verify_sh, render_verify_sh


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


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
            {"passed": False, "score": 0.6, "metrics": {"test_pass_rate": "6/10"}}
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


class TestHarness:
    def test_run_with_passing_script(self, workspace):
        script = workspace / "verify.sh"
        script.write_text("#!/bin/bash\necho 'ok'\nexit 0\n")
        script.chmod(0o755)

        harness = Harness()
        result = harness.run(workspace, script)
        assert isinstance(result, HarnessResult)
        assert result.passed is True
        assert result.exit_code == 0

    def test_run_with_no_script(self, workspace):
        harness = Harness()
        result = harness.run(workspace, None)
        assert result.passed is False
        assert result.exit_code == -1
        assert "No verify.sh" in result.stderr


class TestRenderVerifySh:
    def test_basic(self):
        surface = VerificationSurface(runner="pytest", targets=["tests/"], options="-v")
        result = render_verify_sh(surface)
        assert "#!/usr/bin/env bash" in result
        assert "set -euo pipefail" in result
        assert "pytest tests/ -v" in result

    def test_no_options(self):
        surface = VerificationSurface(runner="pytest", targets=["tests/"])
        result = render_verify_sh(surface)
        assert "pytest tests/" in result

    def test_multiple_targets(self):
        surface = VerificationSurface(runner="go test", targets=["./...", "-count=1"])
        result = render_verify_sh(surface)
        assert "go test ./... -count=1" in result
