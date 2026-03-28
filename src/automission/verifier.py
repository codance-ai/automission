"""Verifier: Gate (verify.sh) + Critic (LLM)."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from automission.docker import build_docker_cmd
from automission.models import AcceptanceGroup, CriterionResult, VerifierResult
from automission.structured_output import CLIResponseError, StructuredOutputBackend

logger = logging.getLogger(__name__)

# ── Tool schema for structured critic output ──

_CRITIC_TOOL = {
    "name": "submit_verification",
    "description": "Submit structured verification analysis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "passed_criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "detail": {"type": "string"},
                    },
                    "required": ["criterion", "passed", "detail"],
                },
            },
            "failed_criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "detail": {"type": "string"},
                    },
                    "required": ["criterion", "passed", "detail"],
                },
            },
            "group_statuses": {
                "type": "object",
                "description": "Per acceptance group: group_id -> completed (true/false)",
            },
            "suggestion": {
                "type": "string",
                "description": "Actionable suggestion for the next attempt. Be specific and technical.",
            },
            "reason": {
                "type": "string",
                "description": "Human-readable summary of the verification result.",
            },
            "score": {
                "type": "number",
                "description": "Overall score from 0.0 to 1.0.",
            },
        },
        "required": [
            "passed_criteria",
            "failed_criteria",
            "group_statuses",
            "suggestion",
            "reason",
            "score",
        ],
    },
}

_CRITIC_JSON_SCHEMA = _CRITIC_TOOL["input_schema"]


def run_verify_sh(
    workdir: Path,
    script_path: Path,
    docker_image: str = "ghcr.io/codance-ai/automission:latest",
) -> dict[str, Any]:
    """Run verify.sh and return structured gate result."""
    try:
        # verify.sh must be inside workspace to mount correctly
        try:
            rel_script = (
                script_path.relative_to(workdir)
                if script_path.is_absolute()
                else script_path
            )
        except ValueError:
            return {
                "passed": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"verify.sh at {script_path} is outside workspace {workdir}, cannot mount in Docker",
                "json_output": None,
            }
        cmd = build_docker_cmd(docker_image, ["bash", str(rel_script)], workdir=workdir)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, encoding="utf-8"
        )
        stdout = result.stdout
        stderr = result.stderr
        passed = result.returncode == 0

        # Try to parse JSON from stdout
        json_output = None
        try:
            json_output = json.loads(stdout.strip())
        except (json.JSONDecodeError, ValueError):
            pass

        return {
            "passed": passed,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "json_output": json_output,
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "verify.sh timed out after 120s",
            "json_output": None,
        }
    except FileNotFoundError:
        return {
            "passed": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Script not found: {script_path}",
            "json_output": None,
        }


class Verifier:
    """Gate + Critic verifier."""

    def __init__(
        self,
        backend: StructuredOutputBackend | None = None,
        verifier_model: str = "claude-sonnet-4-6",
        docker_image: str = "ghcr.io/codance-ai/automission:latest",
    ):
        self.backend = backend
        self.model = verifier_model
        self.docker_image = docker_image

    def evaluate(
        self,
        workdir: Path,
        verify_sh: Path | None,
        acceptance_groups: list[AcceptanceGroup],
    ) -> VerifierResult:
        # ── Gate ──
        if verify_sh and verify_sh.exists():
            gate = run_verify_sh(
                workdir,
                verify_sh,
                docker_image=self.docker_image,
            )
            gate_source = "script"
            gate_passed = gate["passed"]
        else:
            gate = {
                "passed": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "No verify.sh",
                "json_output": None,
            }
            gate_source = "llm"
            gate_passed = False

        # ── Extract metrics from verify.sh JSON output ──
        score = None
        scores: dict[str, float] = {}
        metrics: dict[str, Any] = {}
        if gate.get("json_output"):
            jo = gate["json_output"]
            score = jo.get("score")
            scores = jo.get("scores", {})
            metrics = jo.get("metrics", {})

        # ── Critic ──
        if self.backend is not None:
            critic = self._run_critic(gate, acceptance_groups)
        else:
            critic = self._basic_critic(gate_passed, acceptance_groups)

        # ── Combine ──
        contract_passed = gate_passed
        group_statuses = critic.get("group_statuses", {})
        all_groups_done = bool(group_statuses) and all(group_statuses.values())
        mission_passed = gate_passed and all_groups_done

        return VerifierResult(
            contract_passed=contract_passed,
            mission_passed=mission_passed,
            gate_source=gate_source,
            score=score if score is not None else critic.get("score"),
            scores=scores,
            metrics=metrics,
            passed_criteria=[
                CriterionResult(**c) for c in critic.get("passed_criteria", [])
            ],
            failed_criteria=[
                CriterionResult(**c) for c in critic.get("failed_criteria", [])
            ],
            group_statuses=group_statuses,
            suggestion=critic.get("suggestion", ""),
            reason=critic.get("reason", ""),
        )

    def _run_critic(
        self, gate: dict[str, Any], groups: list[AcceptanceGroup]
    ) -> dict[str, Any]:
        """Run LLM critic via CLI with json-schema."""
        criteria_text = "\n".join(
            f"- [{g.name}] {c.text}" for g in groups for c in g.criteria
        )
        group_list = ", ".join(g.id for g in groups)

        prompt = f"""You are a code verification critic. Analyze the test output and evaluate each acceptance criterion.

## Gate Result
Passed: {gate["passed"]}
Exit code: {gate["exit_code"]}

## Test Output (stdout)
{gate["stdout"][:3000]}

## Test Errors (stderr)
{gate["stderr"][:2000]}

## Acceptance Criteria
{criteria_text}

## Groups
{group_list}

Evaluate each criterion individually. A group is complete when ALL its required criteria pass.
Be specific about what passed and what failed. Provide an actionable suggestion for the next attempt."""

        try:
            return self.backend.query(
                prompt=prompt,
                model=self.model,
                json_schema=_CRITIC_JSON_SCHEMA,
            )
        except CLIResponseError as e:
            logger.error("Critic CLI call failed: %s", e)
            return self._basic_critic(gate["passed"], groups)

    def _basic_critic(
        self, gate_passed: bool, groups: list[AcceptanceGroup]
    ) -> dict[str, Any]:
        """Fallback critic when no LLM client is available."""
        if gate_passed:
            return {
                "passed_criteria": [
                    {"criterion": c.text, "passed": True, "detail": "Gate passed"}
                    for g in groups
                    for c in g.criteria
                ],
                "failed_criteria": [],
                "group_statuses": {g.id: True for g in groups},
                "suggestion": "",
                "reason": "All gate checks passed.",
                "score": 1.0,
            }
        else:
            return {
                "passed_criteria": [],
                "failed_criteria": [
                    {"criterion": c.text, "passed": False, "detail": "Gate failed"}
                    for g in groups
                    for c in g.criteria
                ],
                "group_statuses": {g.id: False for g in groups},
                "suggestion": "Review the test output and fix failing tests.",
                "reason": "Gate verification failed.",
                "score": 0.0,
            }
