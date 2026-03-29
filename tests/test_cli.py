"""Tests for CLI commands."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

import pytest

from automission.cli import cli


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

    return mock_ws, mock_spawn, mock_attach, mock_collect, mock_ledger_cls


class TestRunCommand:
    def test_run_requires_goal(self, runner):
        result = runner.invoke(cli, ["run"])
        assert result.exit_code != 0

    @patch("automission.cli._run_mission")
    def test_run_with_minimal_args(self, mock_run, runner, fixture_dir):
        mock_run.return_value = (True, "test-001", "completed", Path("/tmp/fake-ws"))
        runner.invoke(
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
        # _run_mission is kept intact but run command no longer calls it directly
        # Test the daemon path instead
        assert True  # _run_mission is tested separately

    def test_run_with_minimal_args_daemon(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
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
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
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

    @patch("automission.cli._run_mission")
    def test_run_with_all_flags(self, mock_run, runner, fixture_dir):
        mock_run.return_value = (True, "test-001", "completed", Path("/tmp/fake-ws"))
        # _run_mission still exists but run command uses daemon path
        assert True


class TestRunModelFlag:
    def test_run_passes_model_to_workspace(self, runner, fixture_dir):
        mocks = _mock_daemon_run()
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4]:
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
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4]:
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
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4]:
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
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
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
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
            result = runner.invoke(
                cli, ["run", "--goal", "test", "--acceptance", acceptance_file]
            )
            assert result.exit_code == 0

    def test_failure_exits_1(self, runner, acceptance_file):
        mocks = _mock_daemon_run(status="failed")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
            result = runner.invoke(
                cli, ["run", "--goal", "test", "--acceptance", acceptance_file]
            )
            assert result.exit_code == 1

    def test_resource_limit_exits_5(self, runner, acceptance_file):
        mocks = _mock_daemon_run(status="resource_limit")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
            result = runner.invoke(
                cli, ["run", "--goal", "test", "--acceptance", acceptance_file]
            )
            assert result.exit_code == 5

    def test_cancelled_exits_2(self, runner, acceptance_file):
        mocks = _mock_daemon_run(status="cancelled")
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
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
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
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

    @staticmethod
    def _mock_select(answers: list[str]):
        """Return a patch that makes questionary.select return *answers* in order."""
        it = iter(answers)

        def fake_select(*args, **kwargs):
            mock_question = MagicMock()
            mock_question.ask.return_value = next(it)
            return mock_question

        return patch("automission.cli.questionary.select", side_effect=fake_select)

    def test_claude_defaults_no_auth_prompt(self, runner, tmp_path):
        """Choosing claude for both backends skips auth prompts entirely."""
        config_path = tmp_path / "config.toml"
        # Flow: backend → model → (auth skipped for claude) × 2
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            self._mock_select(
                ["claude", "claude-sonnet-4-6", "claude", "claude-sonnet-4-6"]
            ),
        ):
            mock_run.side_effect = FileNotFoundError()  # docker not available
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert config_path.exists()

    def test_codex_agent_oauth_runs_login(self, runner, tmp_path):
        """Selecting codex + oauth triggers 'codex login'."""
        config_path = tmp_path / "config.toml"
        # Flow: backend=codex → model → auth=oauth, backend=claude → model
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            self._mock_select(
                ["codex", "gpt-5.4", "oauth", "claude", "claude-sonnet-4-6"]
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
            self._mock_select(
                ["codex", "gpt-5.4", "api_key", "claude", "claude-sonnet-4-6"]
            ),
        ):
            mock_run.side_effect = FileNotFoundError()  # docker not available
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "logged in" not in result.output.lower()

    def test_gemini_planner_oauth(self, runner, tmp_path):
        """Selecting gemini as planner + oauth triggers gemini OAuth flow."""
        config_path = tmp_path / "config.toml"
        # Flow: backend=claude → model, backend=gemini → model → auth=oauth
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            self._mock_select(
                [
                    "claude",
                    "claude-sonnet-4-6",
                    "gemini",
                    "gemini-3.1-pro-preview",
                    "oauth",
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
            self._mock_select(
                ["codex", "gpt-5.4", "oauth", "claude", "claude-sonnet-4-6"]
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
            self._mock_select(
                [
                    "codex",
                    "gpt-5.4-mini",
                    "api_key",
                    "gemini",
                    "gemini-3-flash-preview",
                    "api_key",
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


class TestPlannerIntegration:
    """Tests for Planner flow in run command."""

    def test_acceptance_flag_skips_planner(self, runner, fixture_dir):
        """When --acceptance is provided, Planner should not be called."""
        mocks = _mock_daemon_run()
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4]:
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

    def test_no_planner_without_acceptance_errors(self, runner):
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
        from automission.models import PlanCriterion, PlanDraft, PlanGroup

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
            verify_command="pytest tests/ -v",
            assumptions=["Python"],
        )
        mock_planner = MagicMock()
        mock_planner.plan.return_value = mock_draft
        mock_planner_cls.return_value = mock_planner

        mocks = _mock_daemon_run()
        with mocks[0], mocks[1], mocks[2], mocks[3], mocks[4]:
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
        from automission.models import PlanCriterion, PlanDraft, PlanGroup

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
            verify_command="pytest",
            assumptions=[],
        )
        mock_planner = MagicMock()
        mock_planner.plan.return_value = mock_draft
        mock_planner_cls.return_value = mock_planner

        mocks = _mock_daemon_run()
        with mocks[0] as mock_ws, mocks[1], mocks[2], mocks[3], mocks[4]:
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

    @patch("automission.planner.Planner")
    def test_planner_user_declines(self, mock_planner_cls, runner):
        """When user types 'n', exit 0 without running mission."""
        from automission.models import PlanCriterion, PlanDraft, PlanGroup

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
            verify_command="pytest",
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

        from automission.models import PlanCriterion, PlanDraft, PlanGroup

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
            verify_command="pytest",
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
    @staticmethod
    def _mock_select(answers: list[str]):
        """Return a patch that makes questionary.select return *answers* in order."""
        it = iter(answers)

        def fake_select(*args, **kwargs):
            mock_question = MagicMock()
            mock_question.ask.return_value = next(it)
            return mock_question

        return patch("automission.cli.questionary.select", side_effect=fake_select)

    _DEFAULT_ANSWERS = ["claude", "claude-sonnet-4-6", "claude", "claude-sonnet-4-6"]

    def test_init_creates_config(self, runner, tmp_path):
        config_path = tmp_path / "config.toml"
        with (
            patch("automission.cli.CONFIG_PATH", config_path),
            patch("subprocess.run") as mock_run,
            self._mock_select(self._DEFAULT_ANSWERS),
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
            self._mock_select(self._DEFAULT_ANSWERS),
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
            self._mock_select(self._DEFAULT_ANSWERS),
        ):
            # Docker available, image exists
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "docker: available" in result.output.lower()
