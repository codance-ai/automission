"""Integration test for the daemon lifecycle."""

import os
import subprocess

import pytest

from automission.daemon import is_executor_alive
from automission.db import Ledger
from automission.events import EventTailer, EventWriter
from automission.executor import reconcile_stale_state


@pytest.fixture
def full_mission_workspace(tmp_path):
    """Create a workspace that mimics a real mission setup."""
    ws = tmp_path / "mission"
    ws.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=ws, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=ws, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=ws, capture_output=True)
    (ws / "MISSION.md").write_text("# Test Mission\nBuild hello.py\n")
    (ws / "ACCEPTANCE.md").write_text("## g1: hello\n- [ ] hello.py exists\n")
    (ws / "verify.sh").write_text("#!/bin/bash\ntest -f hello.py\n")
    (ws / "verify.sh").chmod(0o755)
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=ws, capture_output=True)

    from automission.acceptance import parse_acceptance_md

    ledger = Ledger(ws / "mission.db")
    ledger.create_mission("m-integ", "Build hello.py", agents=1)
    groups = parse_acceptance_md((ws / "ACCEPTANCE.md").read_text())
    ledger.store_acceptance_groups("m-integ", groups)
    ledger.close()
    return ws


class TestReconcileIntegration:
    def test_full_reconciliation(self, full_mission_workspace):
        """Reconcile should clean up ALL stale state from a crash."""
        ws = full_mission_workspace
        ledger = Ledger(ws / "mission.db")

        # Simulate crash: stuck lock + active claim + stale runtime + wrong status
        ledger.acquire_merge_lock("ghost-agent")
        groups = ledger.get_acceptance_groups("m-integ")
        if groups:
            ledger.create_claim(
                "ghost-claim", "m-integ", "ghost-agent", groups[0].id, expires_s=9999
            )
        ledger.register_executor("m-integ", "old-exec", 999999999)
        ledger.update_mission_status("m-integ", "cancelled")
        ledger.close()

        reconcile_stale_state(ws, "m-integ")

        ledger = Ledger(ws / "mission.db")
        # Merge lock should be free
        assert ledger.acquire_merge_lock("new-agent") is True
        ledger.release_merge_lock("new-agent")
        # Claims should be expired — group should be in frontier
        if groups:
            frontier = ledger.get_frontier_groups("m-integ")
            assert any(g["id"] == groups[0].id for g in frontier)
        # Old runtime should be cleared
        assert ledger.get_executor_runtime("m-integ") is None
        # Status should be reset to running
        m = ledger.get_mission("m-integ")
        assert m["status"] == "running"
        ledger.close()

    def test_reconcile_with_stale_worktrees(self, full_mission_workspace):
        """Reconcile should attempt to clean up stale worktree directories."""
        ws = full_mission_workspace
        wt_dir = ws / "worktrees" / "agent-1"
        wt_dir.mkdir(parents=True)
        (wt_dir / "stale_file").write_text("leftover")

        reconcile_stale_state(ws, "m-integ")
        # The worktree dir should be attempted for cleanup
        # (git worktree remove may not fully work on a non-git-worktree dir,
        # but at minimum the reconcile function shouldn't crash)


class TestEventLifecycle:
    def test_writer_reader_roundtrip(self, tmp_path):
        """Full event lifecycle: write multiple events, read them back."""
        events_file = tmp_path / "events.jsonl"
        with EventWriter(events_file) as w:
            w.emit("mission_started", {"mission_id": "m-1", "agents": 2})
            w.emit("attempt_start", {"agent_id": "agent-1", "attempt": 1})
            w.emit("attempt_end", {"status": "completed", "cost_usd": 0.15})
            w.emit("verification", {"passed": True, "score": 0.9})
            w.emit("group_completed", {"group_id": "g1"})
            w.emit("mission_completed", {"total_cost": 0.5, "total_attempts": 3})

        events = list(EventTailer(events_file).read_existing())
        assert len(events) == 6
        assert events[0]["type"] == "mission_started"
        assert events[0]["mission_id"] == "m-1"
        assert events[5]["type"] == "mission_completed"
        assert events[5]["total_cost"] == 0.5
        # All events should have timestamps
        for e in events:
            assert "ts" in e

    def test_follow_stops_at_terminal(self, tmp_path):
        """Follow should stop at terminal events."""
        events_file = tmp_path / "events.jsonl"
        with EventWriter(events_file) as w:
            w.emit("attempt_start", {"attempt": 1})
            w.emit("mission_failed", {"outcome": "resource_limit"})
            w.emit("extra_event", {})  # Should not be seen

        events = list(EventTailer(events_file).follow(poll_interval=0.01))
        types = [e["type"] for e in events]
        assert "mission_failed" in types
        assert "extra_event" not in types


class TestDaemonLiveness:
    def test_alive_detection_with_runtime(self, full_mission_workspace):
        """is_executor_alive should return True when PID exists and runtime registered."""
        ws = full_mission_workspace
        (ws / "mission.pid").write_text(str(os.getpid()))
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-integ", "exec-1", os.getpid())
        ledger.close()

        assert is_executor_alive(ws, "m-integ") is True

    def test_dead_detection_no_pid(self, full_mission_workspace):
        """is_executor_alive should return False when no PID file."""
        ws = full_mission_workspace
        assert is_executor_alive(ws, "m-integ") is False

    def test_dead_detection_stale_pid(self, full_mission_workspace):
        """is_executor_alive should return False for non-existent PID."""
        ws = full_mission_workspace
        (ws / "mission.pid").write_text("999999999")
        ledger = Ledger(ws / "mission.db")
        ledger.register_executor("m-integ", "exec-1", 999999999)
        ledger.close()

        assert is_executor_alive(ws, "m-integ") is False


class TestDBExecutorRuntimeIntegration:
    def test_executor_lifecycle_in_db(self, full_mission_workspace):
        """Test the full executor DB lifecycle: register -> heartbeat -> stop -> clear."""
        ws = full_mission_workspace
        ledger = Ledger(ws / "mission.db")

        # Register
        ledger.register_executor("m-integ", "exec-1", 12345)
        rt = ledger.get_executor_runtime("m-integ")
        assert rt["desired_state"] == "running"

        # Heartbeat
        ledger.update_executor_heartbeat("m-integ", "exec-1")
        rt = ledger.get_executor_runtime("m-integ")
        assert rt["heartbeat_at"] is not None

        # Stop request
        ledger.set_executor_desired_state("m-integ", "stopping")
        rt = ledger.get_executor_runtime("m-integ")
        assert rt["desired_state"] == "stopping"

        # Clear
        ledger.clear_executor_runtime("m-integ")
        assert ledger.get_executor_runtime("m-integ") is None

        ledger.close()
