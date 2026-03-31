"""Tests for CLI commands."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

import pytest

from automission.cli import cli
from mock_helpers import mock_questionary_select


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fixture_dir():
    return Path(__file__).parent / "fixtures" / "m1-calculator"


@pytest.fixture
def acceptance_file(fixture_dir):
    """Path to a valid ACCEPTANCE.md for tests that don't care about Planner."""
    return str(fixture_dir / "ACCEPTANCE.md")


def _mock_daemon_run(
    mission_id="test-001", status="completed", total_cost=0.0, total_attempts=0
):
    """Context manager that mocks _create_mission_workspace + spawn_executor + _attach_live_view + Ledger read."""
    ws = Path("/tmp/fake-ws")
    mock_ws = patch(
        "automission.cli._create_mission_workspace", return_value=(mission_id, ws)
    )
    mock_spawn = patch("automission.daemon.spawn_executor")
    mock_attach = patch("automission.cli._attach_live_view")

    mock_mission = {
        "id": mission_id,
        "status": status,
        "total_cost": total_cost,
        "total_attempts": total_attempts,
        "goal": "test goal",
        "agents": 1,
    }
    mock_ledger_instance = MagicMock()
    mock_ledger_instance.get_mission.return_value = mock_mission
    mock_ledger_instance.__enter__ = MagicMock(return_value=mock_ledger_instance)
    mock_ledger_instance.__exit__ = MagicMock(return_value=False)
    mock_collect = patch("automission.cli._collect_changed_files", return_value=[])
    mock_ledger_cls = patch("automission.cli.Ledger", return_value=mock_ledger_instance)
    mock_docker = patch("automission.docker.ensure_docker")

    return mock_ws, mock_spawn, mock_attach, mock_collect, mock_ledger_cls, mock_docker


class TestRunCommand:
    def test_run_requires_goal(self, runner):
        result = runner.invoke(cli, ["run"])
        assert result.exit_code != 0

    def test_run_with_minimal_args(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calculator",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                ],
            )
            assert result.exit_code == 0

    def test_run_with_minimal_args_daemon(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calculator",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                ],
            )
            assert result.exit_code == 0

    def test_run_with_all_flags_daemon(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calculator",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                    "--agents",
                    "1",
                    "--max-iterations",
                    "10",
                    "--max-cost",
                    "5.0",
                    "--timeout",
                    "1800",
                    "--backend",
                    "claude",
                ],
            )
            assert result.exit_code == 0

    def test_run_with_all_flags(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calculator",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                    "--agents",
                    "1",
                    "--max-iterations",
                    "10",
                    "--max-cost",
                    "5.0",
                    "--timeout",
                    "1800",
                    "--backend",
                    "claude",
                ],
            )
            assert result.exit_code == 0


class TestRunModelFlag:
    def test_run_passes_model_to_workspace(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calculator",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                    "--model",
                    "claude-opus-4-6",
                ],
            )
            assert result.exit_code == 0
            call_kwargs = mock_ws.call_args[1]
            assert call_kwargs["model"] == "claude-opus-4-6"

    def test_run_default_model(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with (
            mocks[0] as mock_ws,
            mocks[1],
            mocks[2],
            mocks[3],
            mocks[4],
            mocks[5],
            patch(
                "automission.cli.load_config",
                return_value=__import__(
                    "automission.config", fromlist=["AutomissionConfig"]
                ).AutomissionConfig(),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calculator",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                ],
            )
            assert result.exit_code == 0
            call_kwargs = mock_ws.call_args[1]
            assert call_kwargs["model"] == "claude-sonnet-4-6"


class TestRunGoalFile:
    def test_goal_file_reads_content(self, runner, tmp_path, acceptance_file):
        goal_file = tmp_path / "goal.txt"
        goal_file.write_text("Build a calculator")
        mocks = _mock_daemon_run()
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal-file",
                    str(goal_file),
                    "--acceptance",
                    acceptance_file,
                ],
            )
            assert result.exit_code == 0
            call_kwargs = mock_ws.call_args[1]
            assert call_kwargs["goal"] == "Build a calculator"

    def test_goal_and_goal_file_mutually_exclusive(self, runner, tmp_path):
        goal_file = tmp_path / "goal.txt"
        goal_file.write_text("test")
        result = runner.invoke(
            cli,
            [
                "run",
                "--goal",
                "test",
                "--goal-file",
                str(goal_file),
            ],
        )
        assert result.exit_code != 0

    def test_neither_goal_nor_goal_file_fails(self, runner):
        result = runner.invoke(cli, ["run"])
        assert result.exit_code != 0


class TestRunJsonOutput:
    def test_json_flag_outputs_json(self, runner, acceptance_file):
        mocks = _mock_daemon_run(mission_id="test-001", status="completed")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calculator",
                    "--json",
                    "--acceptance",
                    acceptance_file,
                ],
            )
            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output["mission_id"] == "test-001"
            assert output["status"] == "completed"


class TestExitCodes:
    def test_success_exits_0(self, runner, acceptance_file):
        mocks = _mock_daemon_run(status="completed")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli, ["run", "--goal", "test", "--acceptance", acceptance_file]
            )
            assert result.exit_code == 0

    def test_failure_exits_1(self, runner, acceptance_file):
        mocks = _mock_daemon_run(status="failed")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli, ["run", "--goal", "test", "--acceptance", acceptance_file]
            )
            assert result.exit_code == 1

    def test_resource_limit_exits_5(self, runner, acceptance_file):
        mocks = _mock_daemon_run(status="resource_limit")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli, ["run", "--goal", "test", "--acceptance", acceptance_file]
            )
            assert result.exit_code == 5

    def test_cancelled_exits_2(self, runner, acceptance_file):
        mocks = _mock_daemon_run(status="cancelled")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli, ["run", "--goal", "test", "--acceptance", acceptance_file]
            )
            assert result.exit_code == 2


class TestStatusCommand:
    def test_status_no_missions(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["status"])
            assert result.exit_code == 0
            assert "no mission" in result.output.lower()

    def test_status_shows_critic_summary(self, runner, tmp_path):
        """Attempt history should include critic summary at end of each line."""
        from automission.db import Ledger
        from automission.models import (
            CriticResult,
            HarnessResult,
            VerificationResult,
        )

        ws = tmp_path / "test-mission"
        ws.mkdir()
        vr = VerificationResult(
            harness=HarnessResult(passed=True, exit_code=0, stdout="", stderr=""),
            critic=CriticResult(
                summary="all tests pass, auth fully implemented",
                root_cause="",
                next_actions=[],
                blockers=[],
                group_analysis={},
            ),
        )
        with Ledger(ws / "mission.db") as ledger:
            ledger.create_mission("m1", goal="test", agents=1)
            ledger.record_attempt(
                mission_id="m1",
                attempt_id="a1",
                agent_id="agent-1",
                attempt_number=1,
                status="completed",
                exit_code=0,
                duration_s=42.0,
                cost_usd=0.25,
                token_input=12000,
                token_output=74000,
                changed_files=["auth.py"],
                verification_passed=True,
                verification_result=vr.to_json(),
                commit_hash="abc123",
            )

        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path):
            result = runner.invoke(cli, ["status", "m1"])
            assert result.exit_code == 0
            assert "all tests pass, auth fully implemented" in result.output


class TestReadMissionLog:
    """Tests for _read_mission_log helper."""

    SAMPLE_LOG = (
        "=" * 80
        + "\n  AUTOMISSION — test-001\n  2026-03-31\n"
        + "=" * 80
        + "\n\n"
        + "==== PLAN "
        + "=" * 70
        + "\nDuration: 1s\n\n  Group 1: [auth] Login\n    - test criterion\n\n"
        + "\n==== ATTEMPT 1 "
        + "=" * 65
        + "\nAgent: agent-1 | Scope: auth\n\n"
        + "---- prompt (100 chars) "
        + "-" * 56
        + "\nYou are a coding agent.\n"
        + "-" * 80
        + "\n\n"
        + "---- agent execution "
        + "-" * 59
        + "\nDuration: 10s | Tokens: 1,000 in + 5,000 out | Cost: $0.050\n"
        + "-" * 80
        + "\n\n"
        + "\n==== ATTEMPT 2 "
        + "=" * 65
        + "\nAgent: agent-1 | Scope: auth\n\n"
        + "---- prompt (120 chars) "
        + "-" * 56
        + "\nFix the failing test.\n"
        + "-" * 80
        + "\n\n"
        + "---- agent execution "
        + "-" * 59
        + "\nDuration: 15s | Tokens: 2,000 in + 8,000 out | Cost: $0.080\n"
        + "-" * 80
        + "\n\n"
        + "\n==== ATTEMPT 3 "
        + "=" * 65
        + "\nAgent: agent-1 | Scope: auth\n\n"
        + "---- agent execution "
        + "-" * 59
        + "\nDuration: 12s | Tokens: 1,500 in + 6,000 out | Cost: $0.060\n"
        + "-" * 80
        + "\n\n"
        + "\n"
        + "=" * 80
        + "\n  MISSION COMPLETED\n  Attempts: 3 | Cost: $0.190 | Duration: 37s\n"
        + "=" * 80
        + "\n"
    )

    def test_nonexistent_file_returns_none(self, tmp_path):
        from automission.cli import _read_mission_log

        result = _read_mission_log(tmp_path / "nonexistent.log")
        assert result is None

    def test_full_output_strips_prompts_by_default(self, tmp_path):
        from automission.cli import _read_mission_log

        log_file = tmp_path / "mission.log"
        log_file.write_text(self.SAMPLE_LOG, encoding="utf-8")

        result = _read_mission_log(log_file)
        assert result is not None
        assert "ATTEMPT 1" in result
        assert "ATTEMPT 2" in result
        assert "ATTEMPT 3" in result
        assert "agent execution" in result
        # Prompts should be stripped
        assert "You are a coding agent." not in result
        assert "Fix the failing test." not in result

    def test_verbose_includes_prompts(self, tmp_path):
        from automission.cli import _read_mission_log

        log_file = tmp_path / "mission.log"
        log_file.write_text(self.SAMPLE_LOG, encoding="utf-8")

        result = _read_mission_log(log_file, verbose=True)
        assert "You are a coding agent." in result
        assert "Fix the failing test." in result

    def test_last_n_shows_only_last_attempts(self, tmp_path):
        from automission.cli import _read_mission_log

        log_file = tmp_path / "mission.log"
        log_file.write_text(self.SAMPLE_LOG, encoding="utf-8")

        result = _read_mission_log(log_file, last=2)
        assert result is not None
        # Should include header + plan
        assert "AUTOMISSION" in result
        assert "PLAN" in result
        # Should NOT include attempt 1
        assert "ATTEMPT 1" not in result
        # Should include attempts 2 and 3
        assert "ATTEMPT 2" in result
        assert "ATTEMPT 3" in result
        # Should include footer
        assert "MISSION COMPLETED" in result

    def test_last_n_larger_than_total_shows_all(self, tmp_path):
        from automission.cli import _read_mission_log

        log_file = tmp_path / "mission.log"
        log_file.write_text(self.SAMPLE_LOG, encoding="utf-8")

        result = _read_mission_log(log_file, last=10)
        assert "ATTEMPT 1" in result
        assert "ATTEMPT 2" in result
        assert "ATTEMPT 3" in result

    def test_last_and_verbose_combined(self, tmp_path):
        from automission.cli import _read_mission_log

        log_file = tmp_path / "mission.log"
        log_file.write_text(self.SAMPLE_LOG, encoding="utf-8")

        result = _read_mission_log(log_file, last=1, verbose=True)
        # Only attempt 3 (which has no prompt section)
        assert "ATTEMPT 1" not in result
        assert "ATTEMPT 3" in result


class TestLogsCommand:
    def test_logs_no_missions(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["logs"])
            assert result.exit_code == 0
            assert "no mission" in result.output.lower()

    def test_logs_with_nonexistent_id(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["logs", "nonexistent-id"])
            assert result.exit_code == 0
            assert "no mission" in result.output.lower()

    def test_logs_reads_mission_log_file(self, runner, tmp_path):
        """logs command should read mission.log when available."""
        from automission.db import Ledger

        ws = tmp_path / "test-mission"
        ws.mkdir()
        # Create a minimal DB so workspace is found
        with Ledger(ws / "mission.db") as ledger:
            ledger.create_mission("m1", goal="test", agents=1)
        # Create a mission.log
        (ws / "mission.log").write_text(
            "==== PLAN " + "=" * 70 + "\nDuration: 1s\n\n"
            "\n==== ATTEMPT 1 " + "=" * 65 + "\n"
            "Agent: agent-1 | Scope: auth\n\n"
            "---- agent execution " + "-" * 59 + "\n"
            "Duration: 10s | Tokens: 1,000 in + 5,000 out | Cost: $0.050\n"
            + "-" * 80
            + "\n\n",
            encoding="utf-8",
        )

        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path):
            result = runner.invoke(cli, ["logs", "m1"])
            assert result.exit_code == 0
            assert "agent execution" in result.output
            assert "Tokens: 1,000 in + 5,000 out" in result.output

    def test_logs_fallback_to_db_when_no_log_file(self, runner, tmp_path):
        """logs command should fallback to DB when mission.log doesn't exist."""
        from automission.db import Ledger

        ws = tmp_path / "test-mission"
        ws.mkdir()
        with Ledger(ws / "mission.db") as ledger:
            ledger.create_mission("m1", goal="test", agents=1)
            ledger.record_attempt(
                mission_id="m1",
                attempt_id="a1",
                agent_id="agent-1",
                attempt_number=1,
                status="completed",
                exit_code=0,
                duration_s=10.0,
                cost_usd=0.05,
                token_input=1000,
                token_output=5000,
                changed_files=["file.py"],
                verification_passed=True,
                verification_result="{}",
                commit_hash="abc123",
            )

        # No mission.log file — should fallback to DB
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path):
            result = runner.invoke(cli, ["logs", "m1"])
            assert result.exit_code == 0
            assert "agent-1" in result.output
            assert "PASS" in result.output


class TestListCommand:
    def test_list_empty(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["list"])
            assert result.exit_code == 0
            assert "no mission" in result.output.lower()

    def test_list_empty_json(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["list", "--json"])
            assert result.exit_code == 0
            output = json.loads(result.output)
            assert output == []


class TestStopCommand:
    def test_stop_not_found(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["stop", "nonexistent"])
            assert "not found" in result.output.lower()

    def test_stop_no_missions(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["stop"])
            assert "not found" in result.output.lower()


class TestResumeCommand:
    def test_resume_not_found(self, runner, tmp_path):
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "nonexistent"):
            result = runner.invoke(cli, ["resume", "nonexistent"])
            assert "not found" in result.output.lower()


class TestMultiAgentCLI:
    def test_mission_stores_agent_count(self, tmp_path):
        from automission.workspace import create_mission
        from automission.backend.mock import MockBackend
        from automission.db import Ledger

        fixture_dir = Path(__file__).parent / "fixtures" / "m1-calculator"
        backend = MockBackend(simulate_files={})
        ws = create_mission(
            mission_id="multi-cli",
            goal="Test",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=fixture_dir / "workspace",
            agents=2,
        )
        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission("multi-cli")
        assert mission["agents"] == 2
        ledger.close()


class TestNewFlags:
    def test_yes_flag(self, runner, acceptance_file):
        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "test",
                    "--yes",
                    "--acceptance",
                    acceptance_file,
                ],
            )
            assert result.exit_code == 0

    def test_detach_flag(self, runner, acceptance_file):
        mocks = _mock_daemon_run()
        with (
            mocks[0],
            mocks[1] as mock_spawn,
            mocks[2] as mock_attach,
            mocks[3],
            mocks[4],
            mocks[5],
        ):
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "test",
                    "--detach",
                    "--acceptance",
                    acceptance_file,
                ],
            )
            assert result.exit_code == 0
            assert "background" in result.output.lower()
            mock_spawn.assert_called_once()
            mock_attach.assert_not_called()


class TestInitInteractiveFlow:
    """Interactive init flow: choose backends, auth, write config, pull Docker."""

    def test_claude_defaults_no_auth_prompt(self, runner, tmp_path):
        """Choosing claude for both backends skips auth prompts entirely."""
        config_path = tmp_path / "config.toml"
        # Flow: agent backend → model → planner backend → model → verifier "yes"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                ["claude", "claude-sonnet-4-6", "claude", "claude-sonnet-4-6", "yes"]
            ),
        ):
            mock_run.side_effect = FileNotFoundError()  # docker not available
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert config_path.exists()

    def test_codex_agent_oauth_runs_login(self, runner, tmp_path):
        """Selecting codex + oauth triggers 'codex login'."""
        config_path = tmp_path / "config.toml"
        # Flow: backend=codex → model → auth=oauth, backend=claude → model, verifier "yes"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                ["codex", "gpt-5.4", "oauth", "claude", "claude-sonnet-4-6", "yes"]
            ),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # codex login
                FileNotFoundError(),  # docker version
            ]
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "logged in" in result.output.lower()

    def test_codex_agent_api_key_no_login(self, runner, tmp_path):
        """Selecting codex + api_key does not run login."""
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                ["codex", "gpt-5.4", "api_key", "claude", "claude-sonnet-4-6", "yes"]
            ),
        ):
            mock_run.side_effect = FileNotFoundError()  # docker not available
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "logged in" not in result.output.lower()

    def test_gemini_planner_oauth(self, runner, tmp_path):
        """Selecting gemini as planner + oauth triggers gemini OAuth flow."""
        config_path = tmp_path / "config.toml"
        # Flow: backend=claude → model, backend=gemini → model → auth=oauth, verifier "yes"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                [
                    "claude",
                    "claude-sonnet-4-6",
                    "gemini",
                    "gemini-3.1-pro-preview",
                    "oauth",
                    "yes",
                ]
            ),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0),  # gemini oauth
                FileNotFoundError(),  # docker version
            ]
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "gemini" in result.output.lower()

    def test_oauth_cli_not_found(self, runner, tmp_path):
        """When codex CLI is not installed, show helpful message."""
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                ["codex", "gpt-5.4", "oauth", "claude", "claude-sonnet-4-6", "yes"]
            ),
        ):
            mock_run.side_effect = FileNotFoundError()
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_config_reflects_choices(self, runner, tmp_path):
        """Generated config.toml should reflect the user's backend/auth/model choices."""
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                [
                    "codex",
                    "gpt-5.4-mini",
                    "api_key",
                    "gemini",
                    "gemini-3-flash-preview",
                    "api_key",
                    "yes",  # verifier: same as planner
                ]
            ),
        ):
            mock_run.side_effect = FileNotFoundError()
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        import tomllib

        data = tomllib.loads(config_path.read_text())
        assert data["defaults"]["backend"] == "codex"
        assert data["defaults"]["model"] == "gpt-5.4-mini"
        assert data["defaults"]["auth"] == "api_key"
        assert data["planner"]["backend"] == "gemini"
        assert data["planner"]["model"] == "gemini-3-flash-preview"
        assert data["planner"]["auth"] == "api_key"
        # Verifier should match planner when "yes" is selected
        assert data["verifier"]["backend"] == "gemini"
        assert data["verifier"]["model"] == "gemini-3-flash-preview"
        assert data["verifier"]["auth"] == "api_key"

    def test_config_verifier_independent(self, runner, tmp_path):
        """When user says 'no', verifier gets its own backend/model/auth."""
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                [
                    "claude",
                    "claude-sonnet-4-6",  # agent
                    "claude",
                    "claude-sonnet-4-6",  # planner
                    "no",  # verifier: configure separately
                    "gemini",
                    "gemini-3-flash-preview",
                    "api_key",  # verifier config
                ]
            ),
        ):
            mock_run.side_effect = FileNotFoundError()
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        import tomllib

        data = tomllib.loads(config_path.read_text())
        assert data["verifier"]["backend"] == "gemini"
        assert data["verifier"]["model"] == "gemini-3-flash-preview"
        assert data["verifier"]["auth"] == "api_key"

    def test_custom_model_via_other(self, runner, tmp_path):
        """Selecting 'Other (type manually)' allows entering a custom model name."""
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(
                [
                    "claude",
                    "Other (type manually)",
                    "claude",
                    "claude-sonnet-4-6",
                    "yes",
                ]
            ),
            patch("automission.cli.questionary.text") as mock_text,
        ):
            mock_text.return_value.ask.return_value = "my-custom-model"
            mock_run.side_effect = FileNotFoundError()
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        import tomllib

        data = tomllib.loads(config_path.read_text())
        assert data["defaults"]["backend"] == "claude"
        assert data["defaults"]["model"] == "my-custom-model"
        assert data["planner"]["backend"] == "claude"
        assert data["planner"]["model"] == "claude-sonnet-4-6"


class TestPlannerIntegration:
    """Tests for Planner flow in run command."""

    def test_acceptance_flag_skips_planner(self, runner, fixture_dir):
        """When --acceptance is provided, Planner should not be called."""
        mocks = _mock_daemon_run()
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calc",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                ],
            )
            assert result.exit_code == 0
            mock_ws.assert_called_once()
            # Planner content params should be None
            call_kwargs = mock_ws.call_args[1]
            assert call_kwargs.get("acceptance_content") is None

    @patch("automission.docker.ensure_docker")
    def test_no_planner_without_acceptance_errors(self, _mock_docker, runner):
        """--no-planner without --acceptance should error."""
        result = runner.invoke(
            cli,
            [
                "run",
                "--goal",
                "Build calc",
                "--no-planner",
            ],
        )
        assert result.exit_code != 0
        assert (
            "acceptance" in result.output.lower() or "planner" in result.output.lower()
        )

    @patch("automission.planner.Planner")
    def test_planner_runs_when_no_acceptance(self, mock_planner_cls, runner):
        """When no --acceptance, Planner should be called."""
        from automission.models import (
            PlanCriterion,
            PlanDraft,
            PlanGroup,
            VerificationSurface,
        )

        mock_draft = PlanDraft(
            mission_summary="Build a calc",
            constraints=["fast"],
            groups=[
                PlanGroup(
                    id="basic",
                    name="basic",
                    criteria=[
                        PlanCriterion(text="add works", verification_hint="test")
                    ],
                )
            ],
            verification_surface=VerificationSurface(
                runner="pytest", targets=["tests/"], options="-v"
            ),
            assumptions=["Python"],
        )
        mock_planner = MagicMock()
        mock_planner.plan.return_value = mock_draft
        mock_planner_cls.return_value = mock_planner

        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calc",
                    "--yes",
                ],
            )
            assert result.exit_code == 0
            mock_planner.plan.assert_called_once_with("Build calc")

    @patch("automission.planner.Planner")
    def test_planner_yes_skips_confirmation(self, mock_planner_cls, runner):
        """--yes should skip the [Y/n/edit] prompt."""
        from automission.models import (
            PlanCriterion,
            PlanDraft,
            PlanGroup,
            VerificationSurface,
        )

        mock_draft = PlanDraft(
            mission_summary="Build a calc",
            constraints=[],
            groups=[
                PlanGroup(
                    id="basic",
                    name="basic",
                    criteria=[PlanCriterion(text="works", verification_hint="test")],
                )
            ],
            verification_surface=VerificationSurface(
                runner="pytest", targets=["tests/"]
            ),
            assumptions=[],
        )
        mock_planner = MagicMock()
        mock_planner.plan.return_value = mock_draft
        mock_planner_cls.return_value = mock_planner

        mocks = _mock_daemon_run()
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4], mocks[5]:
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calc",
                    "--yes",
                ],
            )
            assert result.exit_code == 0
            call_kwargs = mock_ws.call_args[1]
            assert call_kwargs.get("acceptance_content") is not None
            assert "basic" in call_kwargs["acceptance_content"]

    @patch("automission.docker.ensure_docker")
    @patch("automission.planner.Planner")
    def test_planner_user_declines(self, mock_planner_cls, _mock_docker, runner):
        """When user types 'n', exit 0 without running mission."""
        from automission.models import (
            PlanCriterion,
            PlanDraft,
            PlanGroup,
            VerificationSurface,
        )

        mock_draft = PlanDraft(
            mission_summary="Build a calc",
            constraints=[],
            groups=[
                PlanGroup(
                    id="basic",
                    name="basic",
                    criteria=[PlanCriterion(text="works", verification_hint="test")],
                )
            ],
            verification_surface=VerificationSurface(
                runner="pytest", targets=["tests/"]
            ),
            assumptions=[],
        )
        mock_planner = MagicMock()
        mock_planner.plan.return_value = mock_draft
        mock_planner_cls.return_value = mock_planner

        result = runner.invoke(
            cli,
            [
                "run",
                "--goal",
                "Build calc",
            ],
            input="n\n",
        )
        assert result.exit_code == 0


class TestEnsureDockerBeforePlanner:
    """Verify that ensure_docker is called before the Planner (Closes #32)."""

    def test_ensure_docker_called_before_planner(self, runner):
        """ensure_docker must be called early in `run`, before Planner invocation."""
        call_order: list[str] = []

        def fake_ensure_docker(image):
            call_order.append("ensure_docker")

        def fake_create_backend(*a, **kw):
            call_order.append("create_structured_backend")
            return MagicMock()

        from automission.models import (
            PlanCriterion,
            PlanDraft,
            PlanGroup,
            VerificationSurface,
        )

        mock_draft = PlanDraft(
            mission_summary="test",
            constraints=[],
            groups=[
                PlanGroup(
                    id="g1",
                    name="g1",
                    criteria=[PlanCriterion(text="works", verification_hint="check")],
                )
            ],
            verification_surface=VerificationSurface(
                runner="pytest", targets=["tests/"]
            ),
            assumptions=[],
        )
        mock_planner = MagicMock()
        mock_planner.plan.return_value = mock_draft

        mocks = _mock_daemon_run()
        with (
            patch("automission.docker.ensure_docker", fake_ensure_docker),
            patch(
                "automission.structured_output.create_structured_backend",
                fake_create_backend,
            ),
            patch("automission.planner.Planner", return_value=mock_planner),
            mocks[0],
            mocks[1],
            mocks[2],
            mocks[3],
            mocks[4],
        ):
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calc",
                    "--yes",
                ],
            )
        assert result.exit_code == 0, result.output
        assert "ensure_docker" in call_order
        assert call_order.index("ensure_docker") < call_order.index(
            "create_structured_backend"
        )

    def test_ensure_docker_failure_blocks_planner(self, runner):
        """If Docker is unavailable, run should fail before reaching the Planner."""

        def failing_ensure_docker(image):
            raise RuntimeError(
                "Docker is not available. Make sure Docker is installed and the daemon is running."
            )

        with patch("automission.docker.ensure_docker", failing_ensure_docker):
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calc",
                ],
            )
        assert result.exit_code != 0
        assert "Docker is not available" in result.output

    def test_ensure_docker_called_even_with_acceptance(self, runner, fixture_dir):
        """ensure_docker is called even when --acceptance is provided (skipping Planner)."""
        mock_ensure = MagicMock()
        mocks = _mock_daemon_run()
        with (
            patch("automission.docker.ensure_docker", mock_ensure),
            mocks[0],
            mocks[1],
            mocks[2],
            mocks[3],
            mocks[4],
        ):
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--goal",
                    "Build calc",
                    "--acceptance",
                    str(fixture_dir / "ACCEPTANCE.md"),
                    "--verify",
                    str(fixture_dir / "verify.sh"),
                ],
            )
        assert result.exit_code == 0
        mock_ensure.assert_called_once()


class TestInitCommand:
    _DEFAULT_ANSWERS = [
        "claude",
        "claude-sonnet-4-6",
        "claude",
        "claude-sonnet-4-6",
        "yes",
    ]

    def test_init_creates_config(self, runner, tmp_path):
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(self._DEFAULT_ANSWERS),
        ):
            mock_run.side_effect = FileNotFoundError()  # docker not available
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert config_path.exists()
        assert "created config" in result.output.lower()

    def test_init_skips_existing_config(self, runner, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text("[defaults]\nagents = 2\n")
        with patch("automission.cli.CONFIG_PATH", config_path):
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_init_force_overwrites(self, runner, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text("[defaults]\nagents = 2\n")
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(self._DEFAULT_ANSWERS),
        ):
            mock_run.side_effect = FileNotFoundError()
            result = runner.invoke(cli, ["init", "--force"])
        assert result.exit_code == 0
        assert config_path.exists()

    def test_init_detects_docker(self, runner, tmp_path):
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            mock_questionary_select(self._DEFAULT_ANSWERS),
        ):
            # Docker available, image exists
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "docker: available" in result.output.lower()


class TestExportCommand:
    """Tests for the export command."""

    def _create_fake_workspace(self, tmp_path, mission_id="test-001"):
        """Create a fake mission workspace with internal and user files."""
        from automission.db import Ledger

        ws = tmp_path / "missions" / mission_id
        ws.mkdir(parents=True)

        # User files (should be exported)
        (ws / "main.py").write_text("print('hello')")
        (ws / "src").mkdir()
        (ws / "src" / "app.py").write_text("class App: pass")
        (ws / "README.md").write_text("# Project")

        # Internal files (should NOT be exported)
        (ws / ".git").mkdir()
        (ws / ".git" / "config").write_text("[core]")
        (ws / "MISSION.md").write_text("# Mission")
        (ws / "ACCEPTANCE.md").write_text("# Acceptance")
        (ws / "AUTOMISSION.md").write_text("# Auto")
        (ws / "verify.sh").write_text("#!/bin/bash")
        (ws / "skills").mkdir()
        (ws / "skills" / "skill1.md").write_text("# Skill")
        (ws / "__pycache__").mkdir()
        (ws / "__pycache__" / "main.cpython-314.pyc").write_text("bytecode")

        # Create ledger so _find_mission_workspace can find it
        ledger = Ledger(ws / "mission.db")
        ledger.create_mission(
            mission_id=mission_id,
            goal="test",
            backend="claude",
            model="claude-sonnet-4-6",
            backend_auth="api_key",
            verifier_backend="claude",
            verifier_model="claude-sonnet-4-6",
            verifier_auth="api_key",
        )
        ledger.close()

        return ws

    def test_export_copies_user_files_only(self, runner, tmp_path):
        """Export should copy user files and exclude internal files."""
        self._create_fake_workspace(tmp_path)
        output_dir = tmp_path / "exported"

        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "missions"):
            result = runner.invoke(
                cli, ["export", "test-001", "--output", str(output_dir)]
            )

        assert result.exit_code == 0
        assert "Exported 3 files" in result.output

        # User files present
        assert (output_dir / "main.py").exists()
        assert (output_dir / "src" / "app.py").exists()
        assert (output_dir / "README.md").exists()

        # Internal files absent
        assert not (output_dir / ".git").exists()
        assert not (output_dir / "mission.db").exists()
        assert not (output_dir / "MISSION.md").exists()
        assert not (output_dir / "ACCEPTANCE.md").exists()
        assert not (output_dir / "AUTOMISSION.md").exists()
        assert not (output_dir / "verify.sh").exists()
        assert not (output_dir / "skills").exists()
        assert not (output_dir / "__pycache__").exists()

    def test_export_fails_if_target_exists_without_force(self, runner, tmp_path):
        """Export should refuse to overwrite existing directory."""
        self._create_fake_workspace(tmp_path)
        output_dir = tmp_path / "exported"
        output_dir.mkdir()

        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "missions"):
            result = runner.invoke(
                cli, ["export", "test-001", "--output", str(output_dir)]
            )

        assert result.exit_code != 0
        assert "--force" in result.output

    def test_export_with_force_overwrites(self, runner, tmp_path):
        """Export --force should overwrite existing directory."""
        self._create_fake_workspace(tmp_path)
        output_dir = tmp_path / "exported"
        output_dir.mkdir()
        (output_dir / "old-file.txt").write_text("stale")

        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "missions"):
            result = runner.invoke(
                cli, ["export", "test-001", "--output", str(output_dir), "--force"]
            )

        assert result.exit_code == 0
        assert (output_dir / "main.py").exists()
        assert not (output_dir / "old-file.txt").exists()

    def test_export_mission_not_found(self, runner, tmp_path):
        """Export should fail gracefully for non-existent mission."""
        with patch("automission.cli.DEFAULT_BASE_DIR", tmp_path / "missions"):
            result = runner.invoke(
                cli, ["export", "nonexistent", "--output", str(tmp_path / "out")]
            )

        assert result.exit_code != 0
        assert "not found" in result.output


# ── Rich output helpers ──


class TestFmtChangedFiles:
    """Tests for _fmt_changed_files helper."""

    def test_empty(self):
        from automission.cli import _fmt_changed_files

        assert _fmt_changed_files([]) == ""

    def test_single_file(self):
        from automission.cli import _fmt_changed_files

        result = _fmt_changed_files(["src/main.py"])
        assert "main.py" in result
        assert "(1 file)" in result

    def test_multiple_files(self):
        from automission.cli import _fmt_changed_files

        result = _fmt_changed_files(["src/a.py", "src/b.py", "tests/test_c.py"])
        assert "a.py" in result
        assert "b.py" in result
        assert "test_c.py" in result
        assert "(3 files)" in result

    def test_truncation(self):
        from automission.cli import _fmt_changed_files

        files = [f"src/file{i}.py" for i in range(8)]
        result = _fmt_changed_files(files, max_shown=3)
        assert "+5 more" in result
        assert "(8 files)" in result


class TestIsMetadataFile:
    """Tests for _is_metadata_file helper."""

    def test_metadata_top_level_files(self):
        from automission.cli import _is_metadata_file

        for f in [
            "CLAUDE.md",
            "AGENTS.md",
            "GEMINI.md",
            "AUTOMISSION.md",
            "MISSION.md",
            "ACCEPTANCE.md",
            "verify.sh",
            "events.jsonl",
            "mission.pid",
            "mission.db",
        ]:
            assert _is_metadata_file(f) is True, f

    def test_metadata_nested_paths(self):
        from automission.cli import _is_metadata_file

        assert _is_metadata_file("worktrees/agent-1/file.py") is True
        assert _is_metadata_file("skills/my_skill.md") is True
        assert _is_metadata_file(".git/objects/abc") is True

    def test_user_deliverables(self):
        from automission.cli import _is_metadata_file

        assert _is_metadata_file("src/main.py") is False
        assert _is_metadata_file("tests/test_foo.py") is False
        assert _is_metadata_file(".github/workflows/ci.yml") is False
        assert _is_metadata_file("calculator.py") is False


class TestFmtChangedFilesFiltering:
    """Tests for metadata filtering in _fmt_changed_files."""

    def test_metadata_files_filtered(self):
        from automission.cli import _fmt_changed_files

        result = _fmt_changed_files(["calculator.py", "CLAUDE.md", "AUTOMISSION.md"])
        assert "calculator.py" in result
        assert "CLAUDE.md" not in result
        assert "AUTOMISSION.md" not in result

    def test_all_metadata_returns_empty(self):
        from automission.cli import _fmt_changed_files

        result = _fmt_changed_files(["CLAUDE.md", "AUTOMISSION.md", "verify.sh"])
        assert result == ""


class TestRenderCriteria:
    """Tests for _render_criteria helper."""

    def test_summary_and_next_actions(self, capsys):
        from automission.cli import _render_criteria

        event = {
            "summary": "Tests failing, input handling incomplete.",
            "next_actions": ["Fix input handling", "Add validation"],
        }
        _render_criteria(event)
        out = capsys.readouterr().out
        assert "Tests failing" in out
        assert "Fix input handling" in out
        assert "Add validation" in out

    def test_next_actions_limited_to_3_by_default(self, capsys):
        from automission.cli import _render_criteria

        event = {
            "summary": "Multiple issues.",
            "next_actions": ["Action 1", "Action 2", "Action 3", "Action 4"],
        }
        _render_criteria(event)
        out = capsys.readouterr().out
        assert "Action 1" in out
        assert "Action 3" in out
        assert "Action 4" not in out

    def test_verbose_shows_all_actions_and_root_cause(self, capsys):
        from automission.cli import _render_criteria

        event = {
            "summary": "Division by zero not handled.",
            "next_actions": [
                "Handle division by zero case",
                "Action 2",
                "Action 3",
                "Action 4",
            ],
            "root_cause": "Missing guard in evaluate()",
        }
        _render_criteria(event, verbose=True)
        out = capsys.readouterr().out
        assert "Handle division by zero case" in out
        assert "Action 4" in out
        assert "Missing guard in evaluate()" in out

    def test_no_group_analysis_displayed(self, capsys):
        """group_analysis should not appear in display (advisory data kept internal)."""
        from automission.cli import _render_criteria

        event = {
            "summary": "Tests pass.",
            "group_analysis": {"basic_arithmetic": True, "input_handling": False},
            "next_actions": [],
        }
        _render_criteria(event)
        out = capsys.readouterr().out
        assert "basic_arithmetic" not in out
        assert "input_handling" not in out
        assert "\u2713" not in out  # no checkmark
        assert "\u2717" not in out  # no cross

    def test_empty_event(self, capsys):
        from automission.cli import _render_criteria

        _render_criteria({})
        out = capsys.readouterr().out
        assert out == ""


class TestRenderEventRichOutput:
    """Tests for enriched _render_event output."""

    def test_attempt_start_with_scope(self, capsys):
        from automission.cli import _render_event

        event = {
            "type": "attempt_start",
            "agent_id": "agent-1",
            "attempt": 2,
            "scope": "Fix: input validation",
        }
        _render_event(event)
        out = capsys.readouterr().out
        assert "agent-1" in out
        assert "#2" in out
        assert "focus: Fix: input validation" in out

    def test_attempt_start_without_scope(self, capsys):
        from automission.cli import _render_event

        event = {"type": "attempt_start", "agent_id": "agent-1", "attempt": 1}
        _render_event(event)
        out = capsys.readouterr().out
        assert "#1 ..." in out
        assert "focus" not in out

    def test_attempt_end_with_changed_files(self, capsys):
        from automission.cli import _render_event

        event = {
            "type": "attempt_end",
            "status": "completed",
            "token_input": 50000,
            "token_output": 10000,
            "changed_files": ["calculator.py", "tests/test_calc.py"],
        }
        _render_event(event)
        out = capsys.readouterr().out
        assert "completed" in out
        assert "60.0k tokens" in out
        assert "changed:" in out
        assert "calculator.py" in out

    def test_attempt_end_no_changed_files(self, capsys):
        from automission.cli import _render_event

        event = {
            "type": "attempt_end",
            "status": "completed",
            "token_input": 1000,
            "token_output": 500,
            "changed_files": [],
        }
        _render_event(event)
        out = capsys.readouterr().out
        assert "changed" not in out

    def test_verification_fail_with_narrative(self, capsys):
        from automission.cli import _render_event

        event = {
            "type": "verification",
            "passed": False,
            "summary": "Tests failing, input handling incomplete.",
            "group_analysis": {"basic_arithmetic": True, "input_handling": False},
            "next_actions": ["Fix subprocess call to use relative path"],
        }
        _render_event(event)
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Tests failing" in out
        assert "Fix subprocess call" in out
        # group_analysis should not appear as checkmarks
        assert "\u2713" not in out
        assert "\u2717" not in out

    def test_verification_pass_with_narrative(self, capsys):
        from automission.cli import _render_event

        event = {
            "type": "verification",
            "passed": True,
            "summary": "All tests pass.",
            "group_analysis": {"testing": True},
            "next_actions": [],
        }
        _render_event(event)
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "All tests pass" in out

    def test_verification_no_duplicate_summary(self, capsys):
        """Summary should appear exactly once (was a bug)."""
        from automission.cli import _render_event

        event = {
            "type": "verification",
            "passed": True,
            "summary": "Unique summary text.",
            "group_analysis": {},
            "next_actions": [],
        }
        _render_event(event)
        out = capsys.readouterr().out
        assert out.count("Unique summary text.") == 1
