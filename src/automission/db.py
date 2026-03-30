"""SQLite ledger for durable mission state."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from automission import DEFAULT_DOCKER_IMAGE
from automission.models import AcceptanceGroup, Criterion

logger = logging.getLogger(__name__)


class Ledger:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(
            str(db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _create_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                backend TEXT NOT NULL DEFAULT 'claude',
                model TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
                backend_auth TEXT NOT NULL DEFAULT 'api_key',
                verifier_backend TEXT NOT NULL DEFAULT 'claude',
                verifier_model TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
                verifier_auth TEXT NOT NULL DEFAULT 'api_key',
                agents INTEGER NOT NULL DEFAULT 1,
                max_iterations INTEGER NOT NULL DEFAULT 20,
                max_cost REAL NOT NULL DEFAULT 10.0,
                timeout INTEGER NOT NULL DEFAULT 3600,
                docker_image TEXT NOT NULL,
                total_cost REAL NOT NULL DEFAULT 0.0,
                total_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS acceptance_groups (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL REFERENCES missions(id),
                name TEXT NOT NULL,
                depends_on TEXT NOT NULL DEFAULT '[]',
                completed BOOLEAN NOT NULL DEFAULT 0,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS acceptance_criteria (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL REFERENCES acceptance_groups(id),
                text TEXT NOT NULL,
                required BOOLEAN NOT NULL DEFAULT 1,
                passed BOOLEAN NOT NULL DEFAULT 0,
                last_checked_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL REFERENCES missions(id),
                agent_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                duration_s REAL,
                cost_usd REAL DEFAULT 0.0,
                token_input INTEGER DEFAULT 0,
                token_output INTEGER DEFAULT 0,
                changed_files TEXT DEFAULT '[]',
                verification_passed BOOLEAN,
                verification_result TEXT,
                commit_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                claim_contract TEXT DEFAULT '',
                heartbeat_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_active_claim
            ON claims(mission_id, group_id) WHERE status = 'active';

            CREATE TABLE IF NOT EXISTS merge_lock (
                id INTEGER PRIMARY KEY DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'free',
                held_by TEXT,
                acquired_at TIMESTAMP
            );

            INSERT OR IGNORE INTO merge_lock (id, status) VALUES (1, 'free');

            CREATE TABLE IF NOT EXISTS executor_runtime (
                mission_id TEXT PRIMARY KEY REFERENCES missions(id),
                executor_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                desired_state TEXT NOT NULL DEFAULT 'running',
                heartbeat_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn.commit()

    # ── Missions ──

    def create_mission(
        self,
        mission_id: str,
        goal: str,
        backend: str = "claude",
        model: str = "claude-sonnet-4-6",
        backend_auth: str = "api_key",
        verifier_backend: str = "claude",
        verifier_model: str = "claude-sonnet-4-6",
        verifier_auth: str = "api_key",
        agents: int = 1,
        max_iterations: int = 20,
        max_cost: float = 10.0,
        timeout: int = 3600,
        docker_image: str = DEFAULT_DOCKER_IMAGE,
    ) -> None:
        self.conn.execute(
            """INSERT INTO missions (id, goal, backend, model, backend_auth,
               verifier_backend, verifier_model, verifier_auth,
               agents, max_iterations, max_cost, timeout, docker_image)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mission_id,
                goal,
                backend,
                model,
                backend_auth,
                verifier_backend,
                verifier_model,
                verifier_auth,
                agents,
                max_iterations,
                max_cost,
                timeout,
                docker_image,
            ),
        )
        self.conn.commit()

    def get_mission(self, mission_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM missions WHERE id = ?", (mission_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_missions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM missions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_mission_status(self, mission_id: str, status: str) -> None:
        if status == "completed":
            self.conn.execute(
                "UPDATE missions SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, mission_id),
            )
        else:
            self.conn.execute(
                "UPDATE missions SET status = ? WHERE id = ?",
                (status, mission_id),
            )
        self.conn.commit()

    # ── Acceptance Groups ──

    def store_acceptance_groups(
        self, mission_id: str, groups: list[AcceptanceGroup]
    ) -> None:
        self.conn.execute("BEGIN")
        for group in groups:
            self.conn.execute(
                """INSERT INTO acceptance_groups (id, mission_id, name, depends_on)
                   VALUES (?, ?, ?, ?)""",
                (group.id, mission_id, group.name, json.dumps(group.depends_on)),
            )
            for criterion in group.criteria:
                self.conn.execute(
                    """INSERT INTO acceptance_criteria (id, group_id, text, required)
                       VALUES (?, ?, ?, ?)""",
                    (
                        criterion.id,
                        criterion.group_id,
                        criterion.text,
                        criterion.required,
                    ),
                )
        self.conn.commit()

    def get_acceptance_groups(self, mission_id: str) -> list[AcceptanceGroup]:
        rows = self.conn.execute(
            "SELECT * FROM acceptance_groups WHERE mission_id = ? ORDER BY rowid",
            (mission_id,),
        ).fetchall()
        groups = []
        for row in rows:
            criteria_rows = self.conn.execute(
                "SELECT * FROM acceptance_criteria WHERE group_id = ? ORDER BY rowid",
                (row["id"],),
            ).fetchall()
            criteria = [
                Criterion(
                    id=cr["id"],
                    group_id=cr["group_id"],
                    text=cr["text"],
                    required=bool(cr["required"]),
                )
                for cr in criteria_rows
            ]
            groups.append(
                AcceptanceGroup(
                    id=row["id"],
                    name=row["name"],
                    depends_on=json.loads(row["depends_on"]),
                    criteria=criteria,
                )
            )
        return groups

    def update_group_status(self, group_id: str, completed: bool) -> None:
        if completed:
            self.conn.execute(
                "UPDATE acceptance_groups SET completed = 1, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (group_id,),
            )
        else:
            self.conn.execute(
                "UPDATE acceptance_groups SET completed = 0, completed_at = NULL WHERE id = ?",
                (group_id,),
            )
        self.conn.commit()

    def update_group_analysis(self, analysis: dict[str, bool]) -> None:
        self.conn.execute("BEGIN")
        for group_id, completed in analysis.items():
            if completed:
                self.conn.execute(
                    "UPDATE acceptance_groups SET completed = 1, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (group_id,),
                )
            else:
                self.conn.execute(
                    "UPDATE acceptance_groups SET completed = 0, completed_at = NULL WHERE id = ?",
                    (group_id,),
                )
        self.conn.commit()

    def is_group_completed(self, group_id: str) -> bool:
        """Check if a specific acceptance group is completed."""
        row = self.conn.execute(
            "SELECT completed FROM acceptance_groups WHERE id = ?", (group_id,)
        ).fetchone()
        return bool(row and row["completed"])

    # ── Claims ──

    def create_claim(
        self,
        claim_id: str,
        mission_id: str,
        agent_id: str,
        group_id: str,
        expires_s: int = 120,
    ) -> bool:
        """Atomically claim a group. Returns True on success, False if already claimed."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            # Expire stale claims first within the transaction
            self.conn.execute(
                "UPDATE claims SET status = 'expired' "
                "WHERE mission_id = ? AND status = 'active' AND expires_at < CURRENT_TIMESTAMP",
                (mission_id,),
            )
            self.conn.execute(
                "INSERT INTO claims (id, mission_id, agent_id, group_id, expires_at) "
                "VALUES (?, ?, ?, ?, datetime('now', '+' || ? || ' seconds'))",
                (claim_id, mission_id, agent_id, group_id, expires_s),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.conn.rollback()
            return False

    def get_active_claim(self, mission_id: str, group_id: str) -> dict | None:
        """Get the active claim for a mission/group pair."""
        row = self.conn.execute(
            "SELECT * FROM claims WHERE mission_id = ? AND group_id = ? AND status = 'active'",
            (mission_id, group_id),
        ).fetchone()
        return dict(row) if row else None

    def release_claim(self, claim_id: str, status: str) -> None:
        """Release a claim by setting its status."""
        self.conn.execute(
            "UPDATE claims SET status = ? WHERE id = ?",
            (status, claim_id),
        )
        self.conn.commit()

    def expire_stale_claims(self, mission_id: str) -> int:
        """Expire all stale claims for a mission. Returns number of rows affected."""
        cursor = self.conn.execute(
            "UPDATE claims SET status = 'expired' "
            "WHERE mission_id = ? AND status = 'active' AND expires_at < CURRENT_TIMESTAMP",
            (mission_id,),
        )
        self.conn.commit()
        return cursor.rowcount

    def renew_heartbeat(self, claim_id: str, expires_s: int = 120) -> None:
        """Renew heartbeat and extend expiry for a claim."""
        self.conn.execute(
            "UPDATE claims SET heartbeat_at = CURRENT_TIMESTAMP, "
            "expires_at = datetime('now', '+' || ? || ' seconds') WHERE id = ?",
            (expires_s, claim_id),
        )
        self.conn.commit()

    # ── Frontier ──

    def get_frontier_groups(self, mission_id: str) -> list[dict]:
        """Return groups that are ready to work on: not completed, deps satisfied, not claimed."""
        rows = self.conn.execute(
            "SELECT id, name, depends_on FROM acceptance_groups "
            "WHERE mission_id = ? AND completed = 0",
            (mission_id,),
        ).fetchall()

        # Get completed group IDs
        completed_rows = self.conn.execute(
            "SELECT id FROM acceptance_groups WHERE mission_id = ? AND completed = 1",
            (mission_id,),
        ).fetchall()
        completed_ids = {r["id"] for r in completed_rows}

        # Get actively claimed group IDs
        claimed_rows = self.conn.execute(
            "SELECT group_id FROM claims WHERE mission_id = ? AND status = 'active'",
            (mission_id,),
        ).fetchall()
        claimed_ids = {r["group_id"] for r in claimed_rows}

        frontier = []
        for row in rows:
            group_id = row["id"]
            depends_on = json.loads(row["depends_on"])
            # Skip if any dependency is not completed
            if not all(dep in completed_ids for dep in depends_on):
                continue
            # Skip if actively claimed
            if group_id in claimed_ids:
                continue
            frontier.append(
                {
                    "id": group_id,
                    "name": row["name"],
                    "depends_on": depends_on,
                }
            )
        return frontier

    # ── Merge Lock ──

    def acquire_merge_lock(self, agent_id: str) -> bool:
        """Atomically acquire the merge lock. Returns True on success."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            cursor = self.conn.execute(
                "UPDATE merge_lock SET status = 'held', held_by = ?, acquired_at = CURRENT_TIMESTAMP "
                "WHERE id = 1 AND status = 'free'",
                (agent_id,),
            )
            success = cursor.rowcount > 0
            self.conn.commit()
            return success
        except sqlite3.OperationalError:
            self.conn.rollback()
            return False
        except Exception:
            logger.exception("Unexpected error acquiring merge lock")
            self.conn.rollback()
            return False

    def release_merge_lock(self, agent_id: str) -> None:
        """Release the merge lock if held by the given agent."""
        self.conn.execute(
            "UPDATE merge_lock SET status = 'free', held_by = NULL, acquired_at = NULL "
            "WHERE id = 1 AND held_by = ?",
            (agent_id,),
        )
        self.conn.commit()

    # ── Executor Runtime ──

    def register_executor(
        self,
        mission_id: str,
        executor_id: str,
        pid: int,
    ) -> None:
        """Register a new executor for a mission. Replaces any existing entry."""
        self.conn.execute(
            "INSERT OR REPLACE INTO executor_runtime "
            "(mission_id, executor_id, pid, desired_state, heartbeat_at, started_at) "
            "VALUES (?, ?, ?, 'running', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (mission_id, executor_id, pid),
        )
        self.conn.commit()

    def get_executor_runtime(self, mission_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM executor_runtime WHERE mission_id = ?",
            (mission_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_executor_heartbeat(self, mission_id: str, executor_id: str) -> None:
        self.conn.execute(
            "UPDATE executor_runtime SET heartbeat_at = CURRENT_TIMESTAMP "
            "WHERE mission_id = ? AND executor_id = ?",
            (mission_id, executor_id),
        )
        self.conn.commit()

    def set_executor_desired_state(self, mission_id: str, state: str) -> None:
        self.conn.execute(
            "UPDATE executor_runtime SET desired_state = ? WHERE mission_id = ?",
            (state, mission_id),
        )
        self.conn.commit()

    def clear_executor_runtime(self, mission_id: str) -> None:
        self.conn.execute(
            "DELETE FROM executor_runtime WHERE mission_id = ?",
            (mission_id,),
        )
        self.conn.commit()

    def force_release_merge_lock(self) -> None:
        """Force-release the merge lock regardless of who holds it."""
        self.conn.execute(
            "UPDATE merge_lock SET status = 'free', held_by = NULL, acquired_at = NULL WHERE id = 1"
        )
        self.conn.commit()

    def expire_all_active_claims(self, mission_id: str) -> int:
        """Expire ALL active claims for a mission (used during reconciliation)."""
        cursor = self.conn.execute(
            "UPDATE claims SET status = 'expired' WHERE mission_id = ? AND status = 'active'",
            (mission_id,),
        )
        self.conn.commit()
        return cursor.rowcount

    # ── Attempts ──

    def record_attempt(
        self,
        attempt_id: str,
        mission_id: str,
        agent_id: str,
        attempt_number: int,
        status: str,
        exit_code: int,
        duration_s: float,
        cost_usd: float,
        token_input: int,
        token_output: int,
        changed_files: list[str],
        verification_passed: bool,
        verification_result: str,
        commit_hash: str,
    ) -> None:
        self.conn.execute("BEGIN")
        self.conn.execute(
            """INSERT INTO attempts
               (attempt_id, mission_id, agent_id, attempt_number, status, exit_code,
                duration_s, cost_usd, token_input, token_output, changed_files,
                verification_passed, verification_result, commit_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt_id,
                mission_id,
                agent_id,
                attempt_number,
                status,
                exit_code,
                duration_s,
                cost_usd,
                token_input,
                token_output,
                json.dumps(changed_files),
                verification_passed,
                verification_result,
                commit_hash,
            ),
        )
        # Update mission aggregates
        self.conn.execute(
            "UPDATE missions SET total_cost = total_cost + ?, total_attempts = total_attempts + 1 WHERE id = ?",
            (cost_usd, mission_id),
        )
        self.conn.commit()

    def get_attempts(self, mission_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM attempts WHERE mission_id = ? ORDER BY attempt_number",
            (mission_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_last_attempt(self, mission_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM attempts WHERE mission_id = ? ORDER BY attempt_number DESC LIMIT 1",
            (mission_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_best_attempt(self, mission_id: str) -> dict | None:
        """Get the most recent attempt where the gate passed.

        Used for rollback: when stall detection triggers, we reset to
        the last known-good commit. Returns None if no attempt passed.
        """
        attempts = self.get_attempts(mission_id)
        for a in reversed(attempts):
            if a.get("verification_passed"):
                return a
        return None

    def get_mission_age_s(self, mission_id: str) -> float | None:
        """Get seconds elapsed since mission creation."""
        row = self.conn.execute(
            "SELECT (julianday('now') - julianday(created_at)) * 86400.0 as age_s "
            "FROM missions WHERE id = ?",
            (mission_id,),
        ).fetchone()
        return row["age_s"] if row else None

    def close(self) -> None:
        self.conn.close()
