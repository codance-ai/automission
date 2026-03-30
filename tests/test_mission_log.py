"""Tests for MissionLogger structured narrative log."""

import threading

from automission.mission_log import MissionLogger, _format_size


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert _format_size(1024) == "1 KB"
        assert _format_size(1536) == "2 KB"  # rounds
        assert _format_size(151_552) == "148 KB"

    def test_megabytes(self):
        assert _format_size(1_048_576) == "1.0 MB"
        assert _format_size(5_242_880) == "5.0 MB"

    def test_gigabytes(self):
        assert _format_size(1_073_741_824) == "1.0 GB"


class TestMissionLoggerHeader:
    def test_header_format(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.header(
                mission_id="mission-a1b2c3",
                backend="claude",
                model="claude-sonnet-4-6",
                docker_image="ghcr.io/codance-ai/automission:0.2.10",
                agents=2,
                max_attempts=20,
                max_cost=10.0,
                timeout=3600,
            )

        text = path.read_text()
        assert "AUTOMISSION" in text
        assert "mission-a1b2c3" in text
        assert "claude (claude-sonnet-4-6)" in text
        assert "ghcr.io/codance-ai/automission:0.2.10" in text
        assert "max_attempts=20" in text
        assert "max_cost=$10.00" in text
        assert "timeout=3600s" in text
        # Check decorative borders
        assert "=" * 80 in text


class TestMissionLoggerPlan:
    def test_plan_with_dependencies(self, tmp_path):
        path = tmp_path / "mission.log"
        groups = [
            {
                "name": "auth",
                "title": "User Authentication",
                "criteria": ["JWT token generation and validation"],
            },
            {
                "name": "api",
                "title": "REST API Endpoints",
                "depends": ["auth"],
                "criteria": ["CRUD endpoints for /tasks"],
            },
        ]
        with MissionLogger(path) as log:
            log.plan(groups, duration_s=8.2)

        text = path.read_text()
        assert "PLAN" in text
        assert "Duration: 8.2s" in text
        assert "Group 1: [auth] User Authentication" in text
        assert "JWT token generation and validation" in text
        assert "Group 2: [api] REST API Endpoints (depends: auth)" in text
        assert "CRUD endpoints for /tasks" in text

    def test_plan_without_dependencies(self, tmp_path):
        path = tmp_path / "mission.log"
        groups = [
            {
                "name": "setup",
                "title": "Project Setup",
                "criteria": ["Initialize repo"],
            },
        ]
        with MissionLogger(path) as log:
            log.plan(groups, duration_s=2.0)

        text = path.read_text()
        assert "Group 1: [setup] Project Setup" in text
        assert "depends" not in text


class TestMissionLoggerAttempt:
    def test_attempt_start(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.attempt_start(
                attempt_number=1,
                agent_id="agent-1",
                scope="all groups (first attempt)",
            )

        text = path.read_text()
        assert "ATTEMPT 1" in text
        assert "Agent: agent-1" in text
        assert "Scope: all groups (first attempt)" in text
        assert "UTC" in text

    def test_attempt_prompt(self, tmp_path):
        path = tmp_path / "mission.log"
        prompt = "## First Attempt\nDo something."
        with MissionLogger(path) as log:
            log.attempt_prompt(prompt=prompt, prompt_len=1234)

        text = path.read_text()
        assert "prompt (1,234 chars)" in text
        assert "## First Attempt" in text
        assert "Do something." in text

    def test_attempt_execution(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.attempt_execution(
                status="completed",
                exit_code=0,
                duration_s=45.2,
                token_input=12340,
                token_output=3210,
                cost_usd=0.047,
                changed_files=["M src/auth.py"],
                commit_hash="a1b2c3d",
                stdout_path="agent_outputs/test.stdout",
                stdout_size=151552,
            )

        text = path.read_text()
        assert "agent execution" in text
        assert "Duration: 45.2s" in text
        assert "12,340 in" in text
        assert "3,210 out" in text
        assert "Cost: $0.047" in text
        assert "completed (exit 0)" in text
        assert "M src/auth.py" in text
        assert "Commit: a1b2c3d" in text
        assert "agent_outputs/test.stdout" in text
        assert "148 KB" in text

    def test_attempt_execution_no_commit(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.attempt_execution(
                status="failed",
                exit_code=1,
                duration_s=10.0,
                token_input=500,
                token_output=100,
                cost_usd=0.002,
                changed_files=[],
                commit_hash=None,
                stdout_path=None,
                stdout_size=None,
            )

        text = path.read_text()
        assert "failed (exit 1)" in text
        assert "Commit" not in text
        assert "Full output" not in text


class TestMissionLoggerVerification:
    def test_verification_fail(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.verification(
                passed=False,
                exit_code=1,
                harness_duration_s=8.3,
                stdout="5 passed, 2 failed",
                stderr="",
                critic_duration_s=3.1,
                critic_cost_usd=0.008,
                summary="Login returns 500",
                root_cause="No error handling",
                next_actions=["Fix login route"],
                group_statuses={"auth": False, "api": True},
            )

        text = path.read_text()
        assert "verification" in text
        assert "FAIL (exit 1)" in text
        assert "Duration: 8.3s" in text
        assert "5 passed, 2 failed" in text
        assert "3.1s" in text
        assert "$0.008" in text
        assert "Login returns 500" in text
        assert "No error handling" in text
        assert "Fix login route" in text
        assert "auth x" in text
        assert "api >" in text

    def test_verification_pass(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.verification(
                passed=True,
                exit_code=0,
                harness_duration_s=5.0,
                stdout="7 passed",
                stderr="",
                critic_duration_s=None,
                critic_cost_usd=None,
                summary=None,
                root_cause=None,
                next_actions=None,
                group_statuses={"auth": True, "api": True},
            )

        text = path.read_text()
        assert "PASS (exit 0)" in text
        assert "auth >" in text
        assert "api >" in text
        assert "Critic" not in text


class TestMissionLoggerTiming:
    def test_timing_all_phases(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.timing(
                prompt_s=0.1,
                agent_s=45.2,
                harness_s=8.3,
                critic_s=3.1,
            )

        text = path.read_text()
        assert "prompt 0.1s" in text
        assert "agent 45.2s" in text
        assert "harness 8.3s" in text
        assert "critic 3.1s" in text
        assert "total 56.7s" in text

    def test_timing_no_critic(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.timing(
                prompt_s=0.5,
                agent_s=30.0,
                harness_s=5.0,
                critic_s=None,
            )

        text = path.read_text()
        assert "total 35.5s" in text
        assert "critic" not in text.lower()


class TestMissionLoggerFooter:
    def test_footer_completed(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.footer(
                outcome="COMPLETED",
                total_attempts=2,
                total_cost=0.110,
                total_duration_s=97.9,
                group_statuses={"auth": True, "api": True},
            )

        text = path.read_text()
        assert "MISSION COMPLETED" in text
        assert "Attempts: 2" in text
        assert "Cost: $0.110" in text
        assert "Duration: 97.9s" in text
        assert "auth >" in text
        assert "api >" in text
        assert "=" * 80 in text

    def test_footer_failed(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.footer(
                outcome="FAILED",
                total_attempts=5,
                total_cost=1.234,
                total_duration_s=300.0,
                group_statuses={"auth": True, "api": False},
            )

        text = path.read_text()
        assert "MISSION FAILED" in text
        assert "auth >" in text
        assert "api x" in text


class TestMissionLoggerOrchestratorClaim:
    def test_orchestrator_claim(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.orchestrator_claim(
                agent_id="agent-1",
                group_id="auth",
                frontier=["auth", "api"],
            )

        text = path.read_text()
        assert "agent-1 claimed [auth]" in text
        assert "frontier: auth, api" in text


class TestMissionLoggerMergeResult:
    def test_merge_success(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.merge_result(
                agent_id="agent-1",
                success=True,
                commit_hash="abc1234",
                verify_passed=True,
                rejected_reason=None,
            )

        text = path.read_text()
        assert "agent-1" in text
        assert "MERGED" in text
        assert "abc1234" in text
        assert "verified" in text.lower()

    def test_merge_rejected(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.merge_result(
                agent_id="agent-2",
                success=False,
                commit_hash="def5678",
                verify_passed=False,
                rejected_reason="Tests failed after merge",
            )

        text = path.read_text()
        assert "agent-2" in text
        assert "REJECTED" in text
        assert "Tests failed after merge" in text


class TestMissionLoggerContextManager:
    def test_context_manager(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log._write("hello\n")
        # File should be closed after context manager exits
        text = path.read_text()
        assert "hello" in text

    def test_append_mode(self, tmp_path):
        path = tmp_path / "mission.log"
        path.write_text("existing content\n")
        with MissionLogger(path) as log:
            log._write("new content\n")
        text = path.read_text()
        assert "existing content" in text
        assert "new content" in text


class TestMissionLoggerThreadSafety:
    def test_concurrent_writes(self, tmp_path):
        path = tmp_path / "mission.log"
        num_threads = 5
        writes_per_thread = 20

        with MissionLogger(path) as log:
            barrier = threading.Barrier(num_threads)

            def writer(thread_id: int) -> None:
                barrier.wait()
                for i in range(writes_per_thread):
                    log._write(f"thread-{thread_id}-line-{i}\n")

            threads = [
                threading.Thread(target=writer, args=(t,)) for t in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        lines = path.read_text().strip().splitlines()
        assert len(lines) == num_threads * writes_per_thread
        # Each line should be complete (no interleaving)
        for line in lines:
            assert line.startswith("thread-")


class TestMissionLoggerFullNarrative:
    """Integration test: write a full mission narrative and verify structure."""

    def test_full_narrative(self, tmp_path):
        path = tmp_path / "mission.log"
        with MissionLogger(path) as log:
            log.header(
                mission_id="mission-a1b2c3",
                backend="claude",
                model="claude-sonnet-4-6",
                docker_image="ghcr.io/codance-ai/automission:0.2.10",
                agents=2,
                max_attempts=20,
                max_cost=10.0,
                timeout=3600,
            )
            log.plan(
                [
                    {
                        "name": "auth",
                        "title": "User Authentication",
                        "criteria": ["JWT token generation"],
                    },
                ],
                duration_s=8.2,
            )
            log.attempt_start(1, "agent-1", "all groups (first attempt)")
            log.attempt_prompt("## First Attempt\nDo stuff.", 1234)
            log.attempt_execution(
                status="completed",
                exit_code=0,
                duration_s=45.2,
                token_input=12340,
                token_output=3210,
                cost_usd=0.047,
                changed_files=["M src/auth.py"],
                commit_hash="a1b2c3d",
                stdout_path="agent_outputs/test.stdout",
                stdout_size=151552,
            )
            log.verification(
                passed=True,
                exit_code=0,
                harness_duration_s=5.0,
                stdout="7 passed",
                stderr="",
                critic_duration_s=None,
                critic_cost_usd=None,
                summary=None,
                root_cause=None,
                next_actions=None,
                group_statuses={"auth": True},
            )
            log.timing(prompt_s=0.1, agent_s=45.2, harness_s=5.0, critic_s=None)
            log.footer(
                outcome="COMPLETED",
                total_attempts=1,
                total_cost=0.047,
                total_duration_s=50.3,
                group_statuses={"auth": True},
            )

        text = path.read_text()
        # Verify overall structure order
        header_pos = text.index("AUTOMISSION")
        plan_pos = text.index("PLAN")
        attempt_pos = text.index("ATTEMPT 1")
        footer_pos = text.index("MISSION COMPLETED")
        assert header_pos < plan_pos < attempt_pos < footer_pos
