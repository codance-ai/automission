# Verifier Spec

Extracted from vision.md D8, Verifier Stack section. See vision.md for design rationale.

## Architecture: Gate + Critic

Two distinct roles:

```
Gate (determines pass/fail):
  1. verify.sh (user-provided script)      -> exit code = pass/fail, stdout = metrics
  2. LLM gate (only when no verify.sh)     -> pass/fail against acceptance criteria

Critic (always runs, analyzes for next attempt):
  3. LLM critic                            -> structured feedback
     Input: verify.sh stdout/stderr + workspace state + attempt contract
     Output: VerifierResult with scores, per-criterion results, suggestion
     Rule: critic CANNOT override gate's pass/fail decision
```

**Why separate:** verify.sh gives hard pass/fail but lacks actionable detail. LLM critic provides analysis without power to override the objective gate.

## verify.sh Protocol

- Exit code 0 = pass, non-zero = fail
- Stdout: optional JSON with metrics and per-criterion results, written to `eval/latest.json`

```json
{
  "passed": false,
  "score": 0.6,
  "scores": {"completeness": 0.8, "correctness": 0.4, "quality": 0.7},
  "metrics": {"test_pass_rate": "6/10", "val_bpb": 0.993},
  "passed_criteria": [
    {"criterion": "API endpoints exist", "detail": "all 5 CRUD endpoints respond"},
    {"criterion": "DB schema correct", "detail": "migrations applied cleanly"}
  ],
  "failed_criteria": [
    {"criterion": "error handling", "detail": "500 on invalid input, no validation on POST /tasks"},
    {"criterion": "test coverage", "detail": "tests 7-10 fail: auth middleware not applied to DELETE"}
  ],
  "suggestion": "focus on input validation and auth middleware before adding features"
}
```

## Gate Rules

- If verify.sh exists: it is the gate. LLM cannot override.
- If verify.sh fails: gate = fail (final). LLM critic still runs for structured feedback.
- If no verify.sh: LLM serves as both gate and critic.

## LLM Verifier Configuration (D8)

- Different session from worker, different prompt
- Hidden rubric — never feed worker's self-assessment to verifier (blind verification)
- Gate prompt bias: "reject unless clearly passing"
- Critic prompt bias: "be specific and actionable — suggest technical strategies, not just restate errors"
- Model configurable via `[verifier] model` in config.toml or `--model` flag

### Implementation: CLI-based Critic

The critic runs via CLI (`claude -p --json-schema`), not SDK. This unifies authentication with the agent execution path:

```bash
claude -p "Analyze this verification result..." \
  --model sonnet \
  --json-schema '{"type":"object","properties":{"passed_criteria":...},"required":[...]}' \
  --output-format json
```

Safeguards:
- Local schema validation after CLI returns
- 1 automatic retry on schema validation failure
- Fallback to basic critic (gate-only result) if retry also fails

**The verifier prompt is a core asset.** Out-of-box Claude is poor at QA. Budget iterative tuning: read evaluation logs -> find judgment divergence -> update prompt -> repeat.

## VerifierResult Schema

```python
@dataclass
class CriterionResult:
    criterion: str             # what was evaluated
    passed: bool
    detail: str               # specific failure reason or success note

@dataclass
class VerifierResult:
    # Two-level pass/fail: contract scope vs full mission
    contract_passed: bool     # did this attempt fulfill its contract scope?
    mission_passed: bool      # are ALL acceptance groups in the graph satisfied?
    gate_source: Literal["script", "llm"]  # who made the pass/fail decision
    score: float | None       # 0.0-1.0 if available
    scores: dict[str, float]  # multi-dimensional
    metrics: dict[str, Any]   # extracted quantitative metrics
    passed_criteria: list[CriterionResult]
    failed_criteria: list[CriterionResult]
    group_analysis: dict[str, bool]  # per acceptance group: completed or not
    suggestion: str           # actionable focus for next attempt
    reason: str               # human-readable summary
```

## Task Type Mapping

| Task type | Gate | Critic |
|-----------|------|--------|
| Code (tests) | verify.sh (tests pass/fail) | LLM analyzes failures |
| Optimization | verify.sh (metric threshold) | LLM suggests next experiment |
| Open-ended (reports, content) | LLM gate | LLM critic |
