"""Tests for daemon process management."""

import os
import signal
import subprocess
from unittest.mock import patch

import pytest

from automission.daemon import (
    spawn_executor,
    is_executor_alive,
    stop_executor,
    read_pid_file,
    wait_for_executor_exit,
)
from automission.db import Ledger


@pytest.fixture
def mission_workspace(tmp_path):
    ws = tmp_path / "mission-ws"
    ws.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=ws, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=ws, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=ws, capture_output=True)
    (ws / "MISSION.md").write_text("# Test\n")
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=ws, capture_output=True)
    ledger = Ledger(ws / "mission.db")
    ledger.create_mission("m-test", "test goal")
    ledger.close()
    return ws


class TestReadPidFile:
    def test_reads_valid_pid(self, tmp_path):
        pid_file = tmp_path / "mission.pid"
        pid_file.write_text("12345")
        assert read_pid_file(pid_file) == 12345

    def test_returns_none_for_missing_file(self, tmp_path):
        assert read_pid_file(tmp_path / "missing.pid") is None

    def test_returns_none_for_invalid_content(self, tmp_path):
        pid_file = tmp_path / "mission.pid"
        pid_file.write_text("not_a_number")
        assert read_pid_file(pid_file) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        pid_file = tmp_path / "mission.pid"
        pid_file.write_text("")
        assert read_pid_file(pid_file) is None


class TestIsExecutorAlive:
    def test_current_process_is_alive(self, mission_workspace):
        ws = mission_workspace
        (ws / "mission.pid").write_text(str(os.getpid()))
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-test", "exec-1", os.getpid())
        ledger.close()
        assert is_executor_alive(ws, "m-test") is True

    def test_dead_process(self, mission_workspace):
        ws = mission_workspace
        (ws / "mission.pid").write_text("999999999")
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-test", "exec-1", 999999999)
        ledger.close()
        assert is_executor_alive(ws, "m-test") is False

    def test_no_pid_file(self, mission_workspace):
        ws = mission_workspace
        assert is_executor_alive(ws, "m-test") is False

    def test_pid_exists_but_no_runtime(self, mission_workspace):
        ws = mission_workspace
        (ws / "mission.pid").write_text(str(os.getpid()))
        # No runtime registered
        assert is_executor_alive(ws, "m-test") is False


class TestStopExecutor:
    def test_sets_desired_state(self, mission_workspace):
        ws = mission_workspace
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-test", "exec-1", os.getpid())
        ledger.close()
        (ws / "mission.pid").write_text(str(os.getpid()))

        with patch("automission.daemon.os.kill"):
            stop_executor(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        rt = ledger.get_executor_runtime("m-test")
        assert rt["desired_state"] == "stopping"
        ledger.close()

    def test_sends_sigterm(self, mission_workspace):
        ws = mission_workspace
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-test", "exec-1", os.getpid())
        ledger.close()
        (ws / "mission.pid").write_text(str(os.getpid()))

        with patch("automission.daemon.os.kill") as mock_kill:
            # First call for is_executor_alive check, second for actual SIGTERM
            result = stop_executor(ws, "m-test")

        assert result is True
        # os.kill should have been called with SIGTERM
        mock_kill.assert_called()
        # Verify SIGTERM was sent
        sigterm_calls = [
            call for call in mock_kill.call_args_list if call.args[1] == signal.SIGTERM
        ]
        assert len(sigterm_calls) == 1

    def test_returns_false_when_not_running(self, mission_workspace):
        ws = mission_workspace
        result = stop_executor(ws, "m-test")
        assert result is False


class TestWaitForExecutorExit:
    def test_returns_true_when_already_dead(self, mission_workspace):
        ws = mission_workspace
        # No PID file = not alive
        result = wait_for_executor_exit(ws, "m-test", timeout=1)
        assert result is True

    def test_returns_false_on_timeout(self, mission_workspace):
        ws = mission_workspace
        (ws / "mission.pid").write_text(str(os.getpid()))
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-test", "exec-1", os.getpid())
        ledger.close()
        # Process is alive (current process), so it will timeout
        result = wait_for_executor_exit(ws, "m-test", timeout=0.1)
        assert result is False


class TestSpawnExecutor:
    def test_returns_pid(self, mission_workspace):
        ws = mission_workspace
        # Spawn a process that exits immediately; just verify we get a PID back
        with patch("automission.daemon.subprocess.Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.pid = 99999
            pid = spawn_executor(ws, "m-test")
        assert pid == 99999

    def test_uses_log_file_in_workspace(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.daemon.subprocess.Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.pid = 12345
            spawn_executor(ws, "m-test")
        call_kwargs = mock_popen.call_args
        # Verify the process runs the executor module
        args = call_kwargs.args[0]
        assert "-m" in args
        assert "automission.executor" in args
        assert str(ws) in args
        assert "m-test" in args

    def test_uses_custom_log_file(self, mission_workspace):
        ws = mission_workspace
        custom_log = ws / "custom.log"
        with (
            patch("automission.daemon.subprocess.Popen") as mock_popen,
            patch("builtins.open") as mock_open,
        ):
            mock_proc = mock_popen.return_value
            mock_proc.pid = 12345
            spawn_executor(ws, "m-test", log_file=custom_log)
        mock_open.assert_called_once_with(custom_log, "a")

    def test_uses_start_new_session(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.daemon.subprocess.Popen") as mock_popen:
            mock_proc = mock_popen.return_value
            mock_proc.pid = 12345
            spawn_executor(ws, "m-test")
        call_kwargs = mock_popen.call_args
        assert call_kwargs.kwargs.get("start_new_session") is True
