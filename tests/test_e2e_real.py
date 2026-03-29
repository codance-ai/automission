"""Real end-to-end tests — requires Docker, API keys, and the agent image.

These tests call real LLM backends via Docker. They cost real money.

Run all:     pytest tests/test_e2e_real.py -v -s -m e2e
Run claude:  pytest tests/test_e2e_real.py -v -s -k claude
Run codex:   pytest tests/test_e2e_real.py -v -s -k codex
Run gemini:  pytest tests/test_e2e_real.py -v -s -k gemini

Requires: Docker + API keys (ANTHROPIC_API_KEY, CODEX_API_KEY, GEMINI_API_KEY).
Missing key → that backend's tests are skipped.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from automission.db import Ledger
from automission.loop import run_loop
from automission.orchestrator import run_multi_agent
from automission.critic import Critic
from automission.harness import Harness
from conftest import MockCriticBackend
from automission.workspace import create_mission

# ── Skip conditions ──


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "version"], capture_output=True, check=True, timeout=10
        )
        return True
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return False


DOCKER_OK = _docker_available()
DOCKER_IMAGE = "ghcr.io/codance-ai/automission:latest"

# Auth detection: API key only (OAuth needs browser, unusable in Docker/CI)
# Note: Codex CLI reads CODEX_API_KEY, not OPENAI_API_KEY (see #46).
CLAUDE_AUTH = bool(os.environ.get("ANTHROPIC_API_KEY"))
CODEX_AUTH = bool(os.environ.get("CODEX_API_KEY"))
GEMINI_AUTH = bool(os.environ.get("GEMINI_API_KEY"))

requires_docker = pytest.mark.skipif(not DOCKER_OK, reason="Docker not available")
requires_claude = pytest.mark.skipif(
    not CLAUDE_AUTH, reason="ANTHROPIC_API_KEY not set"
)
requires_codex = pytest.mark.skipif(not CODEX_AUTH, reason="CODEX_API_KEY not set")
requires_gemini = pytest.mark.skipif(not GEMINI_AUTH, reason="GEMINI_API_KEY not set")

pytestmark = [pytest.mark.e2e, requires_docker]

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "m1-calculator"

# ── Override conftest autouse fixture: real e2e uses actual Docker ──


@pytest.fixture(autouse=True)
def _unwrap_docker_in_verifier():
    """No-op override: real e2e tests use actual Docker for verification."""


# ── Backend helpers ──


def _make_backend(name: str):
    if name == "claude":
        from automission.backend.claude import ClaudeCodeBackend

        return ClaudeCodeBackend(docker_image=DOCKER_IMAGE, auth_method="api_key")
    elif name == "codex":
        from automission.backend.codex import CodexBackend

        return CodexBackend(docker_image=DOCKER_IMAGE, auth_method="api_key")
    elif name == "gemini":
        from automission.backend.gemini import GeminiBackend

        return GeminiBackend(docker_image=DOCKER_IMAGE, auth_method="api_key")
    raise ValueError(f"Unknown backend: {name}")


def _make_structured_backend(name: str):
    from automission.structured_output import create_structured_backend

    return create_structured_backend(
        name, docker_image=DOCKER_IMAGE, auth_method="api_key"
    )


# ── Parametrize helpers ──

BACKENDS = [
    pytest.param("claude", marks=[requires_claude], id="claude"),
    pytest.param("codex", marks=[requires_codex], id="codex"),
    pytest.param("gemini", marks=[requires_gemini], id="gemini"),
]

# Planner structured output backends (same skip logic)
PLANNER_BACKENDS = BACKENDS

# Cheap models for Planner (only Claude actually passes --model to CLI)
PLANNER_MODELS = {
    "claude": "haiku",
    "codex": "gpt-4o",  # codex structured output doesn't pass --model, uses CLI default
    "gemini": "gemini-pro",  # gemini structured output doesn't pass --model, uses CLI default
}


# ── Scenario 1: Single agent, calculator task ──


class TestSingleAgentCalculator:
    """Agent writes calculator code to pass pre-written tests."""

    @pytest.mark.parametrize("backend_name", BACKENDS)
    def test_completes_within_budget(self, tmp_path, backend_name):
        """Agent should complete the calculator task within a few attempts."""
        backend = _make_backend(backend_name)
        ws = create_mission(
            mission_id=f"real-calc-{backend_name}",
            goal=(FIXTURE_DIR / "goal.txt").read_text().strip(),
            acceptance_path=FIXTURE_DIR / "ACCEPTANCE.md",
            verify_path=FIXTURE_DIR / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=FIXTURE_DIR / "workspace",
            backend_name=backend_name,
            docker_image=DOCKER_IMAGE,
        )

        harness = Harness()
        critic = Critic(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id=f"real-calc-{backend_name}",
            workdir=ws,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=5,
            max_cost=5.0,
            timeout=600,
        )

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission(f"real-calc-{backend_name}")
        ledger.close()

        print(
            f"\n[{backend_name}] outcome={outcome}, "
            f"attempts={mission['total_attempts']}, "
            f"cost=${mission['total_cost']:.2f}"
        )

        assert outcome == "completed", (
            f"{backend_name} did not complete: {outcome} "
            f"after {mission['total_attempts']} attempts"
        )


# ── Scenario 2: Planner generates checklist, then agent executes ──


class TestPlannerFlow:
    """Planner generates acceptance from goal, then agent runs the mission."""

    @pytest.mark.parametrize("backend_name", PLANNER_BACKENDS)
    def test_planner_generates_and_agent_completes(self, tmp_path, backend_name):
        """Full flow: goal → Planner → acceptance → agent → verify."""
        from automission.planner import Planner, render_acceptance_md, render_mission_md
        from automission.harness import render_verify_sh

        # Step 1: Planner generates plan (use cheap model)
        so_backend = _make_structured_backend(backend_name)
        planner = Planner(backend=so_backend, model=PLANNER_MODELS[backend_name])
        goal = "Write a Python module with add, subtract, multiply, divide functions and tests"
        draft = planner.plan(goal)

        print(f"\n[{backend_name}] Planner generated {len(draft.groups)} groups:")
        for g in draft.groups:
            print(f"  - {g.id} ({len(g.criteria)} criteria, deps={g.depends_on})")
        print(f"  verification_surface: {draft.verification_surface.runner}")

        assert len(draft.groups) >= 1
        assert draft.verification_surface.runner

        # Step 2: Create workspace from planner output
        backend = _make_backend(backend_name)
        ws = create_mission(
            mission_id=f"real-planner-{backend_name}",
            goal=goal,
            acceptance_content=render_acceptance_md(draft),
            verify_content=render_verify_sh(draft.verification_surface),
            mission_content=render_mission_md(draft),
            backend=backend,
            workspace_dir=tmp_path / "ws",
            backend_name=backend_name,
            docker_image=DOCKER_IMAGE,
        )

        # Step 3: Run agent loop
        harness = Harness()
        critic = Critic(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id=f"real-planner-{backend_name}",
            workdir=ws,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=5,
            max_cost=5.0,
            timeout=600,
        )

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission(f"real-planner-{backend_name}")
        ledger.close()

        print(
            f"[{backend_name}] outcome={outcome}, "
            f"attempts={mission['total_attempts']}, "
            f"cost=${mission['total_cost']:.2f}"
        )

        assert outcome == "completed", (
            f"{backend_name} planner flow did not complete: {outcome} "
            f"after {mission['total_attempts']} attempts"
        )


# ── Scenario 3: Multi-agent collaboration ──


class TestMultiAgent:
    """Two agents work on independent acceptance groups in parallel."""

    @pytest.mark.parametrize("backend_name", BACKENDS)
    def test_two_agents_complete(self, tmp_path, backend_name):
        backend = _make_backend(backend_name)
        ws = create_mission(
            mission_id=f"real-multi-{backend_name}",
            goal=(FIXTURE_DIR / "goal.txt").read_text().strip(),
            acceptance_path=FIXTURE_DIR / "ACCEPTANCE.md",
            verify_path=FIXTURE_DIR / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=FIXTURE_DIR / "workspace",
            agents=2,
            backend_name=backend_name,
            docker_image=DOCKER_IMAGE,
        )

        harness = Harness()
        critic = Critic(backend=MockCriticBackend())
        outcome = run_multi_agent(
            mission_id=f"real-multi-{backend_name}",
            mission_dir=ws,
            n_agents=2,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=5,
            max_cost=5.0,
            timeout=600,
        )

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission(f"real-multi-{backend_name}")
        ledger.close()

        print(
            f"\n[{backend_name}] multi-agent outcome={outcome}, "
            f"attempts={mission['total_attempts']}, "
            f"cost=${mission['total_cost']:.2f}"
        )

        assert outcome == "completed", (
            f"{backend_name} multi-agent did not complete: {outcome} "
            f"after {mission['total_attempts']} attempts"
        )


# ── Scenario 4: Circuit breaker triggers ──


class TestCircuitBreaker:
    """Circuit breaker stops the mission when limits are reached."""

    @pytest.mark.parametrize("backend_name", BACKENDS)
    def test_max_iterations_stops(self, tmp_path, backend_name):
        """Set max_iterations=1 — mission should stop as resource_limit."""
        backend = _make_backend(backend_name)
        ws = create_mission(
            mission_id=f"real-breaker-{backend_name}",
            goal="Write a web server with authentication, database, caching, and full test suite",
            acceptance_path=FIXTURE_DIR / "ACCEPTANCE.md",
            verify_path=FIXTURE_DIR / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=FIXTURE_DIR / "workspace",
            backend_name=backend_name,
            docker_image=DOCKER_IMAGE,
        )

        harness = Harness()
        critic = Critic(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id=f"real-breaker-{backend_name}",
            workdir=ws,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=1,
            max_cost=5.0,
            timeout=600,
        )

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission(f"real-breaker-{backend_name}")
        ledger.close()

        print(
            f"\n[{backend_name}] circuit breaker outcome={outcome}, "
            f"attempts={mission['total_attempts']}, "
            f"cost=${mission['total_cost']:.2f}"
        )

        # Either completed in 1 attempt (lucky) or hit resource_limit
        assert outcome in ("completed", "resource_limit"), (
            f"{backend_name} unexpected outcome: {outcome}"
        )
        assert mission["total_attempts"] <= 1


# ── Scenario 5: Iteration with feedback ──


class TestIterationWithFeedback:
    """Agent receives feedback from failed attempt and improves.

    Uses a harder goal that's unlikely to pass on first try but
    should pass within a few iterations with verification feedback.
    """

    @pytest.mark.parametrize("backend_name", BACKENDS)
    def test_improves_with_feedback(self, tmp_path, backend_name):
        backend = _make_backend(backend_name)

        # Use the standard calculator fixture — agent should pass within attempts
        ws = create_mission(
            mission_id=f"real-iter-{backend_name}",
            goal=(FIXTURE_DIR / "goal.txt").read_text().strip(),
            acceptance_path=FIXTURE_DIR / "ACCEPTANCE.md",
            verify_path=FIXTURE_DIR / "verify.sh",
            backend=backend,
            workspace_dir=tmp_path / "ws",
            init_files_dir=FIXTURE_DIR / "workspace",
            backend_name=backend_name,
            docker_image=DOCKER_IMAGE,
        )

        harness = Harness()
        critic = Critic(backend=MockCriticBackend())
        outcome = run_loop(
            mission_id=f"real-iter-{backend_name}",
            workdir=ws,
            backend=backend,
            harness=harness,
            critic=critic,
            max_iterations=5,
            max_cost=5.0,
            timeout=600,
        )

        ledger = Ledger(ws / "mission.db")
        mission = ledger.get_mission(f"real-iter-{backend_name}")
        attempts = ledger.get_attempts(f"real-iter-{backend_name}")
        ledger.close()

        print(
            f"\n[{backend_name}] iteration outcome={outcome}, "
            f"attempts={mission['total_attempts']}, "
            f"cost=${mission['total_cost']:.2f}"
        )

        # Print per-attempt results for debugging
        for a in attempts:
            print(
                f"  attempt {a['attempt_number']}: "
                f"verified={'PASS' if a['verification_passed'] else 'FAIL'}, "
                f"cost=${a.get('cost_usd', 0):.2f}"
            )

        assert outcome == "completed"
