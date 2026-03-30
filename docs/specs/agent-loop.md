# Agent Loop Spec

Extracted from vision.md autonomous loop, D6, D7, D10, D11. See vision.md for design rationale.

## Overview

Each agent runs an independent `while true` loop in its own git worktree and Docker container. No supervisor process. Coordination through SQLite ledger + git.

## Loop Pseudocode

```python
# Each agent runs this loop independently in its own worktree
while not mission.completed:
    # 1. Sync: rebase from main to get latest shared progress
    rebase_from_main(agent.worktree)

    # 2. Compute current frontier and select target
    frontier_groups = ledger.get_frontier_groups()          # deps satisfied, not yet complete
    last_verification = ledger.get_last_verification()      # structured VerifierResult

    # 3. Generate attempt contract (lightweight feedforward)
    contract = derive_contract(
        frontier_groups=frontier_groups,                     # only target work in the frontier
        last_verification=last_verification,                 # auto-derive from failed criteria + suggestion
        active_claims=ledger.get_active_claims(),            # avoid overlapping with other agents
    )
    ledger.record_contract(agent.id, contract)               # visible to other agents

    # 4. Build dynamic prompt (AttemptContext → -p flag)
    prompt = format_attempt_prompt(AttemptContext(
        last_verification=format_verification(last_verification),
        contract=format_contract(contract),
        rolling_summary=get_rolling_summary(ledger),
        frontier_groups=format_frontier(frontier_groups),
    ))

    # 5. Run one attempt via backend adapter
    result = backend.run_attempt(AttemptSpec(
        prompt=prompt,                                       # dynamic context via -p
        workdir=agent.worktree,                              # AUTOMISSION.md already in workspace (stable)
        timeout_s=mission.timeout_per_attempt,
    ))

    # 6. Auto-commit workspace changes in worktree
    commit_hash = git_commit_if_changed(agent.worktree)

    # 7. Verify: gate (pass/fail) + critic (structured feedback)
    verification = verifier.evaluate(
        agent.worktree, mission.acceptance, contract
    )
    # verification.contract_passed = did this attempt's focused work succeed?
    # verification.mission_passed = are ALL acceptance groups done?

    # 8. If contract passed, atomic merge to main
    if verification.contract_passed:
        with acquire_merge_lock(mission):
            rebase_from_main(agent.worktree)
            staged_ref = stage_merge(agent.worktree)
            regression = verifier.evaluate(staged_ref, mission.acceptance, contract)
            if regression.contract_passed:
                fast_forward_main(staged_ref)
                verification = regression
            else:
                discard_staged_ref(staged_ref)
                verification = regression
                verification.merge_rejected = True

    # 9. Record attempt to ledger (includes advisory group_analysis)
    ledger.record_attempt(result, commit_hash, verification, contract)

    # 10. Check termination
    # Authority for group completion moved to Executor/Orchestrator
    mission.completed = verification.mission_passed
    check_circuit_breakers(mission, ledger)
    check_stall_recovery(agent, ledger)
```

## Attempt Contract (D11)

Auto-derived from last VerifierResult before each attempt:

- **Scope:** What this attempt will work on
- **Done criteria:** How to judge success for this attempt
- **Non-goals:** What NOT to touch

Not a gate — no approval step. A self-commitment for focus and precise evaluation.

## Prompt Construction (D6)

**Fixed segments:** MISSION.md, ACCEPTANCE.md, skills, side-effect policy, "observe workspace first"

**Dynamic segments:**
- Last verifier result (structured: passed/failed criteria + scores + suggestion)
- Best-so-far metrics
- Rolling summary
- Attempt contract

No historical transcripts — only structured summaries.

## Stall Detection (D7)

Two-step escalation:
1. N consecutive attempts with no progress -> add "change strategy" hint to prompt
2. Still stuck -> rollback to best commit (preserve current head as git tag)
3. Still stuck -> stop and notify user

## Dirty State Handling (D10)

On interrupted attempts:
- Mark as `interrupted` in ledger
- Next iteration: orchestration provides `git status`, `git diff`, verify.sh results in prompt
- Agent decides: salvage partial work or `git reset --hard`
- Orchestration only cleans physical artifacts (`.git/index.lock`)

## Circuit Breakers

- Max iterations per mission
- Max cost (USD)
- Max time per mission
- Backoff/cooldown on API failures

## Resume Semantics

Process crash -> restart reads ledger -> interrupted attempt detected -> fresh agent inspects dirty workspace -> continues.
