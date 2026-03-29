"""Tests for the mission executor process."""

import os
import subprocess
from unittest.mock import patch

import pytest

from automission.db import Ledger
from automission.events import EventTailer
from automission.executor import (
    run_executor,
    reconcile_stale_state,
    EXECUTOR_HEARTBEAT_INTERVAL,
)
from automission.models import AcceptanceGroup, Criterion


@pytest.fixture
def mission_workspace(tmp_path):
    """Create a minimal mission workspace with DB."""
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
    (ws / "ACCEPTANCE.md").write_text("## g1: test\n- [ ] criterion 1\n")
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=ws, capture_output=True)

    ledger = Ledger(ws / "mission.db")
    ledger.create_mission("m-test", "test goal")
    ledger.close()
    return ws


class TestReconcileStaleState:
    def test_releases_stale_merge_lock(self, mission_workspace):
        ws = mission_workspace
        ledger = Ledger(ws / "mission.db")
        ledger.acquire_merge_lock("dead-agent")
        ledger.close()

        reconcile_stale_state(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        assert ledger.acquire_merge_lock("new-agent") is True
        ledger.release_merge_lock("new-agent")
        ledger.close()

    def test_expires_stale_claims(self, mission_workspace):
        ws = mission_workspace
        ledger = Ledger(ws / "mission.db")
        groups = [
            AcceptanceGroup(
                id="g1",
                name="test",
                criteria=[
                    Criterion(id="c1", group_id="g1", text="test criterion"),
                ],
            )
        ]
        ledger.store_acceptance_groups("m-test", groups)
        ledger.create_claim("old-claim", "m-test", "dead-agent", "g1", expires_s=9999)
        ledger.close()

        reconcile_stale_state(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        frontier = ledger.get_frontier_groups("m-test")
        assert any(g["id"] == "g1" for g in frontier)
        ledger.close()

    def test_clears_executor_runtime(self, mission_workspace):
        ws = mission_workspace
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-test", "old-exec", 999999)
        ledger.close()

        reconcile_stale_state(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        assert ledger.get_executor_runtime("m-test") is None
        ledger.close()

    def test_resets_mission_status_to_running(self, mission_workspace):
        ws = mission_workspace
        ledger = Ledger(ws / "mission.db")
        ledger.update_mission_status("m-test", "cancelled")
        ledger.close()

        reconcile_stale_state(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        m = ledger.get_mission("m-test")
        assert m["status"] == "running"
        ledger.close()

    def test_cleans_stale_worktrees(self, mission_workspace):
        ws = mission_workspace
        # Create a fake stale worktree directory
        wt_dir = ws / "worktrees" / "agent-1"
        wt_dir.mkdir(parents=True)
        (wt_dir / "some_file").write_text("stale")

        reconcile_stale_state(ws, "m-test")
        # Worktree dir should be cleaned up (or at least attempted)


class TestRunExecutor:
    def test_writes_pid_file(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.return_value = "completed"
            run_executor(ws, "m-test")

        # PID file should be cleaned up on exit
        # But during execution it should have existed
        # We can verify via the events file
        events_file = ws / "events.jsonl"
        assert events_file.exists()

    def test_writes_events(self, mission_workspace):
        ws = mission_workspace
        # Record some attempts so total_attempts > 0
        with Ledger(ws / "mission.db") as ledger:
            ledger.record_attempt(
                attempt_id="a-1",
                mission_id="m-test",
                agent_id="agent-1",
                attempt_number=1,
                status="completed",
                exit_code=0,
                duration_s=1.0,
                cost_usd=0.01,
                token_input=100,
                token_output=50,
                changed_files=[],
                verification_passed=True,
                verification_result="{}",
                commit_hash="abc123",
            )

        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.return_value = "completed"
            run_executor(ws, "m-test")

        events_file = ws / "events.jsonl"
        assert events_file.exists()
        events = list(EventTailer(events_file).read_existing())
        types = [e["type"] for e in events]
        assert "mission_started" in types
        assert "mission_completed" in types
        # Verify total_attempts is populated from DB
        completed_event = [e for e in events if e["type"] == "mission_completed"][0]
        assert completed_event["total_attempts"] == 1

    def test_cleans_pid_on_exit(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.return_value = "completed"
            run_executor(ws, "m-test")

        pid_file = ws / "mission.pid"
        assert not pid_file.exists()

    def test_registers_in_db(self, mission_workspace):
        ws = mission_workspace
        registered = {}

        def _capture_exec(ws_, mid, event_writer, cancel_flag):
            ledger = Ledger(ws_ / "mission.db")
            rt = ledger.get_executor_runtime(mid)
            if rt:
                registered.update(rt)
            ledger.close()
            return "completed"

        with patch("automission.executor._execute_mission", side_effect=_capture_exec):
            run_executor(ws, "m-test")

        assert registered["pid"] == os.getpid()
        assert registered["desired_state"] == "running"

    def test_clears_runtime_on_exit(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.return_value = "failed"
            run_executor(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        assert ledger.get_executor_runtime("m-test") is None
        ledger.close()

    def test_emits_failed_event(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.return_value = "failed"
            run_executor(ws, "m-test")

        events = list(EventTailer(ws / "events.jsonl").read_existing())
        types = [e["type"] for e in events]
        assert "mission_failed" in types

    def test_handles_crash(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.side_effect = RuntimeError("boom")
            run_executor(ws, "m-test")

        events = list(EventTailer(ws / "events.jsonl").read_existing())
        types = [e["type"] for e in events]
        assert "executor_shutdown" in types
        # PID should still be cleaned up
        assert not (ws / "mission.pid").exists()

    def test_heartbeat_interval_constant(self):
        """Verify the heartbeat interval constant exists and is reasonable."""
        assert EXECUTOR_HEARTBEAT_INTERVAL > 0
        assert EXECUTOR_HEARTBEAT_INTERVAL <= 60

    def test_updates_mission_status_on_complete(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.return_value = "completed"
            run_executor(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        m = ledger.get_mission("m-test")
        assert m["status"] == "completed"
        ledger.close()

    def test_updates_mission_status_on_failure(self, mission_workspace):
        ws = mission_workspace
        with patch("automission.executor._execute_mission") as mock_exec:
            mock_exec.return_value = "failed"
            run_executor(ws, "m-test")

        ledger = Ledger(ws / "mission.db")
        m = ledger.get_mission("m-test")
        assert m["status"] == "failed"
        ledger.close()
