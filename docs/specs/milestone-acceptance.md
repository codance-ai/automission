# Milestone Acceptance Criteria

Each milestone has: unit tests (MockBackend) + a real end-to-end test task.

Test fixtures live in `tests/fixtures/`. Each fixture is a self-contained mission definition (goal + acceptance + verify.sh).

---

## M1: Single Agent Runs One Loop Iteration

### Test Fixture: `tests/fixtures/m1-calculator/`

```
MISSION.md:     "Write add, subtract, multiply, divide functions in src/calc.py with tests"
ACCEPTANCE.md:  "All 4 operations work correctly, including edge cases"
verify.sh:      "cd src && python -m pytest tests/ -v"
src/tests/test_calc.py:  Pre-written tests (4 basic + 2 edge cases)
```

### Acceptance Checklist

- [ ] `automission run --goal "Write calculator functions" --acceptance tests/fixtures/m1-calculator/ACCEPTANCE.md --verify tests/fixtures/m1-calculator/verify.sh` starts without error
- [ ] Workspace is initialized: git repo created, mission.db exists, baseline commit made
- [ ] One attempt is executed via ClaudeCodeBackend (claude -p actually called)
- [ ] verify.sh runs after the attempt, produces exit code
- [ ] LLM critic runs and produces structured VerifierResult with passed/failed criteria
- [ ] Attempt is recorded in ledger: status, duration, cost, changed files, verification result
- [ ] If verify.sh passes on first try: mission marked completed
- [ ] If verify.sh fails: mission stays running (loop stops because M1 is single-iteration only)
- [ ] Skills file (if provided) is included in agent prompt
- [ ] `automission status` shows attempt history from ledger

### Unit Tests (MockBackend)

- [ ] Workspace initialization creates correct directory structure
- [ ] SQLite schema creates all required tables
- [ ] MockBackend returns valid AttemptResult
- [ ] verify.sh runner correctly interprets exit code 0 / non-zero
- [ ] LLM critic produces valid VerifierResult from verify.sh output
- [ ] Ledger correctly records attempt data

---

## M2: Single Agent Loops Until Done

### Test Fixture: `tests/fixtures/m2-calculator-iterative/`

Same as M1 but verify.sh requires harder edge cases (division by zero handling, type validation). First attempt will likely fail; agent should iterate and improve.

### Acceptance Checklist

- [ ] Agent loops automatically: attempt → verify → feedback → next attempt
- [ ] Last VerifierResult (structured feedback) is included in next attempt's prompt
- [ ] Attempt contract is auto-derived: shows focused scope based on what failed
- [ ] Agent shows directed improvement: each attempt addresses specific failed criteria, not random retry
- [ ] Mission completes when verify.sh passes (all criteria satisfied)
- [ ] Circuit breaker: mission stops after max_iterations (e.g., 10) even if not passing
- [ ] Circuit breaker: mission stops after max_cost (e.g., $5)
- [ ] Stall detection: if 3 consecutive attempts with no progress, "change strategy" hint appears in prompt
- [ ] Stall detection: if still stuck, rollback to best commit (tagged for traceability)
- [ ] Resume: kill process mid-mission → restart → resumes from ledger (no lost progress)
- [ ] Dirty state: interrupted attempt → next attempt sees git status/diff in prompt, decides salvage or reset
- [ ] Ledger shows full attempt history with per-attempt verification results

### Unit Tests (MockBackend)

- [ ] Loop terminates on verification pass
- [ ] Loop terminates on circuit breaker (iterations, cost, time)
- [ ] Attempt contract correctly derived from last VerifierResult's failed_criteria
- [ ] Stall detection triggers after N no-progress attempts
- [ ] Rollback preserves current head as git tag
- [ ] Resume from ledger after simulated crash
- [ ] Rolling summary correctly captures mission progress

---

## M3: Two Agents Collaborate

### Test Fixture: `tests/fixtures/m3-two-modules/`

```
MISSION.md:     "Build a Python package with two independent modules: string_utils and math_utils"
ACCEPTANCE.md:  Two groups, no dependencies:
                - string_utils: reverse, capitalize, is_palindrome (with tests)
                - math_utils: factorial, fibonacci, is_prime (with tests)
verify.sh:      "python -m pytest tests/ -v"
```

### Acceptance Checklist

- [ ] Two Docker containers start, each with its own git worktree
- [ ] Each agent claims a different AcceptanceGroup (string_utils vs math_utils)
- [ ] Claims table correctly prevents both agents from claiming the same group
- [ ] Heartbeat/lease works: if one agent dies, its claim is released after timeout
- [ ] Each agent works in isolation (no git conflicts during work)
- [ ] First agent to pass verification: atomic merge to main succeeds
  - [ ] Merge lock acquired (second agent waits)
  - [ ] Staging ref created, regression verified, fast-forward to main
  - [ ] Merge lock released
- [ ] Second agent rebases onto updated main, merges its work
- [ ] If regression fails on staged ref: merge rejected, main not poisoned
- [ ] Mission completes when both groups pass
- [ ] Total wall-clock time < 2x single-agent time (parallelism works)

### Unit Tests (MockBackend)

- [ ] Two mock agents run concurrently without deadlock
- [ ] Claims table prevents duplicate claims on same group
- [ ] Lease expiry releases stale claims
- [ ] Atomic merge: concurrent merge attempts serialize correctly via lock
- [ ] Regression failure on staging ref → main untouched
- [ ] One agent crash → other continues → crashed agent restarts and resumes

---

## M4: Dependency-Aware Acceptance Checklist

### Test Fixture: `tests/fixtures/m4-dependent-modules/`

```
MISSION.md:     "Build a REST API: database layer first, then API endpoints on top"
ACCEPTANCE.md:  Two groups WITH dependency:
                - db_layer (depends_on: []): SQLite wrapper with CRUD operations + tests
                - api_layer (depends_on: [db_layer]): FastAPI endpoints using db_layer + tests
verify.sh:      "python -m pytest tests/ -v"
```

### Acceptance Checklist

- [ ] Acceptance groups loaded with correct dependency relationships
- [ ] Frontier computation: initially only db_layer is in the frontier
- [ ] Agents can only claim groups in the frontier (api_layer is blocked at start)
- [ ] After db_layer passes: api_layer enters the frontier, agent claims it
- [ ] VerifierResult includes group_analysis: `{"db_layer": true, "api_layer": false}`
- [ ] Two-level pass/fail works: contract_passed (this attempt's scope) vs mission_passed (all groups)
- [ ] Mission completes only when all groups are satisfied (frontier is empty)
- [ ] With 2 agents and linear dependency: one agent is idle while waiting for frontier to open (expected)
- [ ] With 2 agents and wide frontier (2+ groups ready): both agents work in parallel

### Unit Tests (MockBackend)

- [ ] Frontier correctly computed for linear chain (A → B → C)
- [ ] Frontier correctly computed for DAG (A, B independent; C depends on both)
- [ ] Group completion updates unlock dependent groups
- [ ] mission_passed = True only when frontier is empty
- [ ] Agent cannot claim group outside frontier

---

## M5: Planner Auto-Generates Acceptance Checklist

### Test Fixture: None (the point is Planner generates everything)

### Test Input

```
"Build a TODO API with user authentication, CRUD operations, and input validation"
```

### Acceptance Checklist

- [ ] Planner produces valid MISSION.md from one-line goal
- [ ] Planner produces ACCEPTANCE.md with 3-6 AcceptanceGroups with dependencies
- [ ] Planner produces verify.sh skeleton that matches acceptance criteria
- [ ] Dependency ordering makes sense (e.g., auth before auth-protected endpoints)
- [ ] No over-specification: groups define what to verify, not how to implement
- [ ] User confirmation step: generated files shown to user before mission starts
- [ ] User can edit generated files before confirming
- [ ] After confirmation: mission runs end-to-end using generated checklist
- [ ] Planner output is deterministic enough to be useful (not random decomposition each time)

### Unit Tests

- [ ] Planner prompt produces valid JSON matching AcceptanceGroup schema
- [ ] Generated depends_on has no cycles (DAG validation)
- [ ] Generated verify.sh is syntactically valid bash
- [ ] Over-specification detection: flag if Planner output contains implementation details (library names, data structures)
