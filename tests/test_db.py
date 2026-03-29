"""Tests for SQLite ledger."""

import threading

import pytest

from automission.db import Ledger
from automission.models import (
    AcceptanceGroup,
    Criterion,
)


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "mission.db")


@pytest.fixture
def sample_groups():
    return [
        AcceptanceGroup(
            id="basic",
            name="basic_operations",
            criteria=[
                Criterion(id="basic_c1", group_id="basic", text="add works"),
                Criterion(id="basic_c2", group_id="basic", text="subtract works"),
            ],
        ),
        AcceptanceGroup(
            id="edge",
            name="edge_cases",
            depends_on=["basic"],
            criteria=[
                Criterion(id="edge_c1", group_id="edge", text="div by zero"),
            ],
        ),
    ]


class TestSchemaCreation:
    def test_tables_exist(self, ledger):
        cursor = ledger.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "missions" in tables
        assert "attempts" in tables
        assert "acceptance_groups" in tables
        assert "acceptance_criteria" in tables

    def test_wal_mode(self, ledger):
        cursor = ledger.conn.execute("PRAGMA journal_mode")
        assert cursor.fetchone()[0] == "wal"


class TestMissions:
    def test_create_mission(self, ledger):
        ledger.create_mission(
            mission_id="m1",
            goal="Build calculator",
            backend="claude",
            agents=1,
            max_iterations=20,
            max_cost=10.0,
            timeout=3600,
        )
        mission = ledger.get_mission("m1")
        assert mission["id"] == "m1"
        assert mission["goal"] == "Build calculator"
        assert mission["status"] == "running"
        assert mission["backend"] == "claude"

    def test_create_mission_with_model(self, ledger):
        ledger.create_mission(
            mission_id="m1",
            goal="Build calculator",
            backend="claude",
            model="claude-opus-4-6",
        )
        mission = ledger.get_mission("m1")
        assert mission["model"] == "claude-opus-4-6"

    def test_create_mission_default_model(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        mission = ledger.get_mission("m1")
        assert mission["model"] == "claude-sonnet-4-6"

    def test_update_mission_status(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        ledger.update_mission_status("m1", "completed")
        assert ledger.get_mission("m1")["status"] == "completed"

    def test_list_missions(self, ledger):
        ledger.create_mission(mission_id="m1", goal="first", backend="claude")
        ledger.create_mission(mission_id="m2", goal="second", backend="claude")
        missions = ledger.list_missions()
        assert len(missions) == 2

    def test_get_mission_age_s(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        age = ledger.get_mission_age_s("m1")
        assert age is not None
        assert age >= 0.0
        assert age < 5.0  # just created

    def test_get_mission_age_s_not_found(self, ledger):
        assert ledger.get_mission_age_s("nonexistent") is None


class TestAcceptanceGroups:
    def test_store_and_retrieve(self, ledger, sample_groups):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        ledger.store_acceptance_groups("m1", sample_groups)

        groups = ledger.get_acceptance_groups("m1")
        assert len(groups) == 2
        assert groups[0].id == "basic"
        assert groups[0].depends_on == []
        assert len(groups[0].criteria) == 2
        assert groups[1].depends_on == ["basic"]

    def test_update_group_status(self, ledger, sample_groups):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        ledger.store_acceptance_groups("m1", sample_groups)

        ledger.update_group_status("basic", completed=True)
        row = ledger.conn.execute(
            "SELECT completed FROM acceptance_groups WHERE id = ?", ("basic",)
        ).fetchone()
        assert row[0] == 1


class TestAttempts:
    def test_record_and_retrieve(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        ledger.record_attempt(
            attempt_id="a1",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=1,
            status="completed",
            exit_code=0,
            duration_s=120.5,
            cost_usd=0.30,
            token_input=5000,
            token_output=3000,
            changed_files=["src/calc.py"],
            verification_passed=True,
            verification_result='{"harness": {"passed": true, "exit_code": 0}, "critic": {"summary": "ok", "group_statuses": {}}}',
            commit_hash="abc123",
        )

        attempts = ledger.get_attempts("m1")
        assert len(attempts) == 1
        assert attempts[0]["attempt_id"] == "a1"
        assert attempts[0]["status"] == "completed"
        assert attempts[0]["cost_usd"] == 0.30
        assert attempts[0]["changed_files"] == '["src/calc.py"]'

    def test_get_last_attempt(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        ledger.record_attempt(
            attempt_id="a1",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=1,
            status="completed",
            exit_code=1,
            duration_s=60.0,
            cost_usd=0.20,
            token_input=3000,
            token_output=2000,
            changed_files=[],
            verification_passed=False,
            verification_result='{"harness": {"passed": false, "exit_code": 1}, "critic": {"summary": "", "group_statuses": {}}}',
            commit_hash="def456",
        )
        ledger.record_attempt(
            attempt_id="a2",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=2,
            status="completed",
            exit_code=0,
            duration_s=90.0,
            cost_usd=0.25,
            token_input=4000,
            token_output=2500,
            changed_files=["calc.py"],
            verification_passed=True,
            verification_result='{"harness": {"passed": false, "exit_code": 1}, "critic": {"summary": "", "group_statuses": {}}}',
            commit_hash="ghi789",
        )

        last = ledger.get_last_attempt("m1")
        assert last is not None
        assert last["attempt_id"] == "a2"

    def test_get_last_attempt_empty(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        assert ledger.get_last_attempt("m1") is None

    def test_update_mission_cost(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        ledger.record_attempt(
            attempt_id="a1",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=1,
            status="completed",
            exit_code=0,
            duration_s=60.0,
            cost_usd=0.50,
            token_input=3000,
            token_output=2000,
            changed_files=[],
            verification_passed=True,
            verification_result='{"harness": {"passed": false, "exit_code": 1}, "critic": {"summary": "", "group_statuses": {}}}',
            commit_hash="abc",
        )
        mission = ledger.get_mission("m1")
        assert mission["total_cost"] == 0.50
        assert mission["total_attempts"] == 1

    def test_get_best_attempt_returns_most_recent_passing(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        # Attempt 1: failed
        ledger.record_attempt(
            attempt_id="a1",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=1,
            status="completed",
            exit_code=1,
            duration_s=60.0,
            cost_usd=0.20,
            token_input=3000,
            token_output=2000,
            changed_files=[],
            verification_passed=False,
            verification_result='{"harness": {"passed": false, "exit_code": 1}, "critic": {"summary": "", "group_statuses": {}}}',
            commit_hash="aaa",
        )
        # Attempt 2: passed
        ledger.record_attempt(
            attempt_id="a2",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=2,
            status="completed",
            exit_code=0,
            duration_s=60.0,
            cost_usd=0.20,
            token_input=3000,
            token_output=2000,
            changed_files=[],
            verification_passed=True,
            verification_result='{"harness": {"passed": false, "exit_code": 1}, "critic": {"summary": "", "group_statuses": {}}}',
            commit_hash="bbb",
        )
        # Attempt 3: failed again
        ledger.record_attempt(
            attempt_id="a3",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=3,
            status="completed",
            exit_code=1,
            duration_s=60.0,
            cost_usd=0.20,
            token_input=3000,
            token_output=2000,
            changed_files=[],
            verification_passed=False,
            verification_result='{"harness": {"passed": false, "exit_code": 1}, "critic": {"summary": "", "group_statuses": {}}}',
            commit_hash="ccc",
        )
        best = ledger.get_best_attempt("m1")
        assert best is not None
        assert best["attempt_id"] == "a2"

    def test_get_best_attempt_no_passing(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        ledger.record_attempt(
            attempt_id="a1",
            mission_id="m1",
            agent_id="agent-1",
            attempt_number=1,
            status="completed",
            exit_code=1,
            duration_s=60.0,
            cost_usd=0.20,
            token_input=3000,
            token_output=2000,
            changed_files=[],
            verification_passed=False,
            verification_result='{"harness": {"passed": false, "exit_code": 1}, "critic": {"summary": "", "group_statuses": {}}}',
            commit_hash="aaa",
        )
        assert ledger.get_best_attempt("m1") is None

    def test_get_best_attempt_empty(self, ledger):
        ledger.create_mission(mission_id="m1", goal="test", backend="claude")
        assert ledger.get_best_attempt("m1") is None


# ── Helper to set up a mission with groups ──


def _setup_mission_with_groups(ledger):
    """Create a mission with two groups: 'basic' (no deps) and 'edge' (depends on basic)."""
    ledger.create_mission(mission_id="m1", goal="test", backend="claude")
    groups = [
        AcceptanceGroup(
            id="g1",
            name="basic_operations",
            criteria=[
                Criterion(id="c1", group_id="g1", text="add works"),
            ],
        ),
        AcceptanceGroup(
            id="g2",
            name="edge_cases",
            depends_on=["g1"],
            criteria=[
                Criterion(id="c2", group_id="g2", text="div by zero"),
            ],
        ),
    ]
    ledger.store_acceptance_groups("m1", groups)


class TestClaimsTable:
    def test_create_and_get_claim(self, ledger):
        _setup_mission_with_groups(ledger)
        ok = ledger.create_claim("claim1", "m1", "agent-1", "g1")
        assert ok is True
        claim = ledger.get_active_claim("m1", "g1")
        assert claim is not None
        assert claim["id"] == "claim1"
        assert claim["agent_id"] == "agent-1"
        assert claim["status"] == "active"

    def test_claim_unique_constraint(self, ledger):
        """Two agents cannot claim the same group concurrently."""
        _setup_mission_with_groups(ledger)
        ok1 = ledger.create_claim("claim1", "m1", "agent-1", "g1")
        ok2 = ledger.create_claim("claim2", "m1", "agent-2", "g1")
        assert ok1 is True
        assert ok2 is False

    def test_release_claim(self, ledger):
        """After releasing, another agent can claim the same group."""
        _setup_mission_with_groups(ledger)
        ledger.create_claim("claim1", "m1", "agent-1", "g1")
        ledger.release_claim("claim1", "completed")
        # Verify old claim is no longer active
        assert ledger.get_active_claim("m1", "g1") is None
        # Another agent can now claim
        ok = ledger.create_claim("claim2", "m1", "agent-2", "g1")
        assert ok is True

    def test_expire_stale_claims(self, ledger):
        """Claims past their expiry get expired."""
        _setup_mission_with_groups(ledger)
        # Create a claim with normal expiry, then manually backdate expires_at
        ledger.create_claim("claim1", "m1", "agent-1", "g1", expires_s=120)
        ledger.conn.execute(
            "UPDATE claims SET expires_at = datetime('now', '-10 seconds') WHERE id = 'claim1'"
        )
        ledger.conn.commit()
        # Expire stale claims
        count = ledger.expire_stale_claims("m1")
        assert count == 1
        assert ledger.get_active_claim("m1", "g1") is None

    def test_renew_heartbeat(self, ledger):
        _setup_mission_with_groups(ledger)
        ledger.create_claim("claim1", "m1", "agent-1", "g1", expires_s=10)
        claim_before = ledger.get_active_claim("m1", "g1")
        old_heartbeat = claim_before["heartbeat_at"]
        old_expires = claim_before["expires_at"]
        # Renew with a longer expiry
        ledger.renew_heartbeat("claim1", expires_s=300)
        claim_after = ledger.get_active_claim("m1", "g1")
        # heartbeat_at should be updated (or at least not earlier)
        assert claim_after["heartbeat_at"] >= old_heartbeat
        # expires_at should be extended
        assert claim_after["expires_at"] > old_expires


class TestFrontier:
    def test_frontier_excludes_completed(self, ledger):
        _setup_mission_with_groups(ledger)
        # Mark g1 as completed
        ledger.update_group_status("g1", completed=True)
        frontier = ledger.get_frontier_groups("m1")
        frontier_ids = [g["id"] for g in frontier]
        assert "g1" not in frontier_ids

    def test_frontier_respects_deps(self, ledger):
        """g2 depends on g1. Until g1 is completed, g2 should not appear in frontier."""
        _setup_mission_with_groups(ledger)
        frontier = ledger.get_frontier_groups("m1")
        frontier_ids = [g["id"] for g in frontier]
        # g1 has no deps, so it's in frontier
        assert "g1" in frontier_ids
        # g2 depends on g1 which is not completed
        assert "g2" not in frontier_ids
        # Now complete g1
        ledger.update_group_status("g1", completed=True)
        frontier = ledger.get_frontier_groups("m1")
        frontier_ids = [g["id"] for g in frontier]
        assert "g2" in frontier_ids

    def test_frontier_excludes_actively_claimed(self, ledger):
        """A group with an active claim should not be in the frontier."""
        _setup_mission_with_groups(ledger)
        ledger.create_claim("claim1", "m1", "agent-1", "g1")
        frontier = ledger.get_frontier_groups("m1")
        frontier_ids = [g["id"] for g in frontier]
        assert "g1" not in frontier_ids


class TestMergeLock:
    def test_acquire_and_release(self, ledger):
        ok = ledger.acquire_merge_lock("agent-1")
        assert ok is True
        # Can't acquire again
        ok2 = ledger.acquire_merge_lock("agent-2")
        assert ok2 is False
        # Release
        ledger.release_merge_lock("agent-1")
        # Now agent-2 can acquire
        ok3 = ledger.acquire_merge_lock("agent-2")
        assert ok3 is True

    def test_thread_safety(self, tmp_path):
        """Two threads race for the merge lock; only one should win."""
        db_path = tmp_path / "thread_test.db"
        # Initialize DB schema before threads to avoid WAL pragma race
        init_db = Ledger(db_path)
        init_db.close()
        results = []

        def try_acquire(agent_id):
            db = Ledger(db_path)
            try:
                ok = db.acquire_merge_lock(agent_id)
                results.append((agent_id, ok))
            finally:
                db.close()

        t1 = threading.Thread(target=try_acquire, args=("agent-1",))
        t2 = threading.Thread(target=try_acquire, args=("agent-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        wins = [r for r in results if r[1] is True]
        losses = [r for r in results if r[1] is False]
        assert len(wins) == 1
        assert len(losses) == 1


class TestUpdateGroupStatusesBatch:
    def test_batch_update(self, ledger):
        """update_group_statuses should batch all updates in one transaction."""
        _setup_mission_with_groups(ledger)
        ledger.update_group_statuses({"g1": True, "g2": True})
        row1 = ledger.conn.execute(
            "SELECT completed FROM acceptance_groups WHERE id = ?", ("g1",)
        ).fetchone()
        row2 = ledger.conn.execute(
            "SELECT completed FROM acceptance_groups WHERE id = ?", ("g2",)
        ).fetchone()
        assert row1[0] == 1
        assert row2[0] == 1


class TestExecutorRuntime:
    def test_register_executor(self, ledger):
        ledger.create_mission("m1", "test goal")
        ledger.register_executor("m1", executor_id="exec-1", pid=12345)
        rt = ledger.get_executor_runtime("m1")
        assert rt is not None
        assert rt["executor_id"] == "exec-1"
        assert rt["pid"] == 12345
        assert rt["desired_state"] == "running"

    def test_update_executor_heartbeat(self, ledger):
        ledger.create_mission("m1", "test goal")
        ledger.register_executor("m1", executor_id="exec-1", pid=100)
        ledger.update_executor_heartbeat("m1", "exec-1")
        rt = ledger.get_executor_runtime("m1")
        assert rt["heartbeat_at"] is not None

    def test_set_desired_state(self, ledger):
        ledger.create_mission("m1", "test goal")
        ledger.register_executor("m1", executor_id="exec-1", pid=100)
        ledger.set_executor_desired_state("m1", "stopping")
        rt = ledger.get_executor_runtime("m1")
        assert rt["desired_state"] == "stopping"

    def test_clear_executor_runtime(self, ledger):
        ledger.create_mission("m1", "test goal")
        ledger.register_executor("m1", executor_id="exec-1", pid=100)
        ledger.clear_executor_runtime("m1")
        rt = ledger.get_executor_runtime("m1")
        assert rt is None

    def test_force_release_merge_lock(self, ledger):
        ledger.create_mission("m1", "test goal")
        ledger.acquire_merge_lock("agent-1")
        ledger.force_release_merge_lock()
        assert ledger.acquire_merge_lock("agent-2") is True
        ledger.release_merge_lock("agent-2")

    def test_expire_all_active_claims(self, ledger, sample_groups):
        ledger.create_mission("m1", "test goal")
        ledger.store_acceptance_groups("m1", sample_groups)
        ledger.create_claim("c1", "m1", "agent-1", "basic", expires_s=9999)
        expired = ledger.expire_all_active_claims("m1")
        assert expired == 1
        frontier = ledger.get_frontier_groups("m1")
        assert any(g["id"] == "basic" for g in frontier)

    def test_register_executor_replaces_existing(self, ledger):
        ledger.create_mission("m1", "test goal")
        ledger.register_executor("m1", executor_id="exec-1", pid=100)
        ledger.register_executor("m1", executor_id="exec-2", pid=200)
        rt = ledger.get_executor_runtime("m1")
        assert rt["executor_id"] == "exec-2"
        assert rt["pid"] == 200

    def test_get_executor_runtime_nonexistent(self, ledger):
        ledger.create_mission("m1", "test goal")
        assert ledger.get_executor_runtime("m1") is None
