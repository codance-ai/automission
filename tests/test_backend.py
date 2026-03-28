"""Tests for agent backends."""

from unittest.mock import patch, MagicMock
import subprocess

import pytest

from automission.models import AttemptSpec, StableContext
from automission.backend.mock import MockBackend
from automission.backend.claude import ClaudeCodeBackend


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    # Init git repo for changed_files detection
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    (ws / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True)
    return ws


class TestMockBackend:
    def test_prepare_workspace(self, workspace):
        backend = MockBackend(result_status="completed", exit_code=0)
        stable = StableContext(goal="Test goal")
        backend.prepare_workspace(workspace, stable)

        automission_md = workspace / "AUTOMISSION.md"
        assert automission_md.exists()
        content = automission_md.read_text()
        assert "Test goal" in content
        assert "DO NOT EDIT" in content

        claude_md = workspace / "CLAUDE.md"
        assert claude_md.exists()
        assert "AUTOMISSION.md" in claude_md.read_text()

    def test_prepare_workspace_appends_to_existing_claude_md(self, workspace):
        (workspace / "CLAUDE.md").write_text("# Existing rules\nBe nice.\n")
        backend = MockBackend(result_status="completed", exit_code=0)
        stable = StableContext(goal="Test")
        backend.prepare_workspace(workspace, stable)

        content = (workspace / "CLAUDE.md").read_text()
        assert "Existing rules" in content
        assert "AUTOMISSION.md" in content

    def test_run_attempt_returns_configured_result(self, workspace):
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            cost_usd=0.25,
            changed_files=["calc.py"],
        )
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="do something",
        )
        result = backend.run_attempt(spec)

        assert result.status == "completed"
        assert result.exit_code == 0
        assert result.cost_usd == 0.25

    def test_run_attempt_simulates_file_changes(self, workspace):
        backend = MockBackend(
            result_status="completed",
            exit_code=0,
            simulate_files={"src/calc.py": "def add(a, b): return a + b\n"},
        )
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="write code",
        )
        result = backend.run_attempt(spec)

        assert (workspace / "src" / "calc.py").exists()
        assert result.status == "completed"

    def test_stable_context_with_skills(self, workspace):
        backend = MockBackend(result_status="completed", exit_code=0)
        stable = StableContext(
            goal="Test",
            skills=["# Skill 1\nDo good things."],
            rules=["Do not modify verify.sh"],
        )
        backend.prepare_workspace(workspace, stable)

        content = (workspace / "AUTOMISSION.md").read_text()
        assert "Skill 1" in content
        assert "Do not modify verify.sh" in content


class TestClaudeCodeBackend:
    def test_prepare_workspace(self, workspace):
        backend = ClaudeCodeBackend()
        stable = StableContext(goal="Build calculator")
        backend.prepare_workspace(workspace, stable)

        assert (workspace / "AUTOMISSION.md").exists()
        assert (workspace / "CLAUDE.md").exists()
        assert "Build calculator" in (workspace / "AUTOMISSION.md").read_text()

    @patch("subprocess.run")
    def test_run_attempt_calls_claude(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b'{"result": "done", "cost_usd": 0.30, "duration_ms": 5000, "num_turns": 3}',
            stderr=b"",
        )
        backend = ClaudeCodeBackend()
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="write calculator",
            timeout_s=300,
        )
        result = backend.run_attempt(spec)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "docker"
        assert "-p" in cmd
        assert result.status == "completed"

    @patch("subprocess.run")
    def test_run_attempt_timeout(self, mock_run, workspace):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=300)
        backend = ClaudeCodeBackend()
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="write calculator",
            timeout_s=300,
        )
        result = backend.run_attempt(spec)
        assert result.status == "timed_out"

    @patch("subprocess.run")
    def test_run_attempt_crash(self, mock_run, workspace):
        mock_run.side_effect = OSError("claude not found")
        backend = ClaudeCodeBackend()
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="write calculator",
            timeout_s=300,
        )
        result = backend.run_attempt(spec)
        assert result.status == "crashed"

    @patch("subprocess.run")
    def test_run_attempt_passes_model(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b'{"result": "done", "cost_usd": 0.10}',
            stderr=b"",
        )
        backend = ClaudeCodeBackend(model="claude-opus-4-6")
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="write calculator",
            timeout_s=300,
        )
        backend.run_attempt(spec)

        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"

    @patch("subprocess.run")
    def test_run_attempt_no_model_omits_flag(self, mock_run, workspace):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b'{"result": "done", "cost_usd": 0.10}',
            stderr=b"",
        )
        backend = ClaudeCodeBackend()
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=workspace,
            prompt="write calculator",
            timeout_s=300,
        )
        backend.run_attempt(spec)

        cmd = mock_run.call_args[0][0]
        assert "--model" not in cmd


CALC_PY_FULL = """\
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
"""


class TestMockBackendSequence:
    def test_sequence_returns_different_files_per_attempt(self, tmp_path):
        """MockBackend with simulate_sequence returns different files on each call."""
        from automission.backend.mock import MockBackend

        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "def add(a, b): return a + b\n"},
                {"src/calc.py": CALC_PY_FULL},
            ],
        )
        backend.prepare_workspace(tmp_path, StableContext(goal="test"))

        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=tmp_path,
            prompt="attempt 1",
        )
        backend.run_attempt(spec)
        assert (
            tmp_path / "src" / "calc.py"
        ).read_text() == "def add(a, b): return a + b\n"

        spec2 = AttemptSpec(
            attempt_id="a2",
            mission_id="m1",
            workdir=tmp_path,
            prompt="attempt 2",
        )
        backend.run_attempt(spec2)
        assert "subtract" in (tmp_path / "src" / "calc.py").read_text()

    def test_sequence_repeats_last_when_exhausted(self, tmp_path):
        from automission.backend.mock import MockBackend

        backend = MockBackend(
            simulate_sequence=[
                {"src/calc.py": "partial\n"},
            ],
        )
        backend.prepare_workspace(tmp_path, StableContext(goal="test"))

        for i in range(3):
            spec = AttemptSpec(
                attempt_id=f"a{i}",
                mission_id="m1",
                workdir=tmp_path,
                prompt=f"attempt {i}",
            )
            backend.run_attempt(spec)

        assert (tmp_path / "src" / "calc.py").read_text() == "partial\n"

    def test_existing_simulate_files_still_works(self, tmp_path):
        """Backward compatibility: simulate_files still works."""
        from automission.backend.mock import MockBackend

        backend = MockBackend(
            simulate_files={"test.txt": "hello"},
        )
        backend.prepare_workspace(tmp_path, StableContext(goal="test"))
        spec = AttemptSpec(
            attempt_id="a1",
            mission_id="m1",
            workdir=tmp_path,
            prompt="test",
        )
        backend.run_attempt(spec)
        assert (tmp_path / "test.txt").read_text() == "hello"
