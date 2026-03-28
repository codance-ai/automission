# automission — Vision Document

Date: 2026-03-23 (last updated: 2026-03-25)

## One-liner

Give a goal, agent team autonomously collaborates to deliver results. Decentralized execution (no orchestrator agent), no human-in-the-loop during execution.

## Background

This project was born from rethinking [multibot](https://github.com/codance-ai/multibot)'s evolution direction. Multibot is a multi-bot group chat platform, but group chat is just one basic application — the real value is multi-agent autonomous collaboration toward goals.

Rather than retrofit multibot (which remains a group chat product), automission is a new project built from scratch around the **mission** primitive.

## Bot Evolution Levels

| Level | Mode | Example |
|-------|------|---------|
| 0 | Passive response — user says one thing, bot replies one thing | Current chatbots |
| 1 | Multi-step execution — bot uses tools to complete a request | Function calling / tool use |
| 2 | Single agent autonomous loop — agent iterates until goal is met | Autoresearch |
| 3 | Multi-agent autonomous collaboration — agent team coordinates through shared state | Anthropic C compiler experiment |

**automission targets Level 3.**

## Core Model

```
Mission = Goal
        + Acceptance Checklist (criteria with dependencies, defined at creation)
        + Verifier Stack (Gate: verify.sh or LLM pass/fail; Critic: LLM structured feedback)
        + Agent Team (dynamic, assembled per mission, destroyed when done)
        + Shared State (agents coordinate through this, not through an orchestrator)
        + Autonomous Loop per Agent (autoresearch-style: think → act → observe → evaluate → repeat)
        + Mission Ledger (SQLite, durable state for resume/observability)
```

## Key Architectural Decision: Orchestration Layer, Not Agent Runtime

**automission does NOT build its own agent runtime.** It reuses existing coding agents (Claude Code, Codex, etc.) as execution engines.

```
automission (orchestration):          Coding agent (execution):
├── Mission management                ├── Shell execution
├── Workspace preparation             ├── File read/write/edit
├── Agent spawning & monitoring       ├── LLM reasoning
├── Autonomous loop control           ├── Error recovery
├── Verification (gate + critic)      └── Context management
├── Circuit breakers
├── Progress tracking
└── Multi-agent coordination
```

## Design Principles

1. **No runtime orchestrator agent** — no central agent assigns work or directs workers during mission execution. Planning happens once at initialization (D9 Planner), verification/critique happens after attempts (D8), but runtime coordination is peer-to-peer through shared state (git + SQLite ledger).

2. **Deterministic control plane** — No orchestrator LLM doesn't mean no infrastructure. Still need: leases, heartbeats, budget enforcement, stop conditions, recovery from dead workers.

3. **Stateless reasoning + stateful workspace** — Each loop iteration starts from fresh LLM context (rebuilt from mission state). Fresh session is chosen for **resumability** and **reproducibility**, not because "context reset is always better than compaction" — that is model-dependent and may change.

4. **Peer-to-peer agents** — Agent relationship is peer-to-peer, not parent-child.

5. **Tool-driven coordination** — Give agents tools (`claim_task()`, `read_active_tasks()`, `report_finding()`), don't script behavior in prompts.

6. **Executable gate first, LLM gate as fallback** — verify.sh is the primary gate for pass/fail. LLM serves as gate only when no verify.sh exists. LLM critic always runs to provide structured feedback. Metrics are verifier output, not a third verifier layer.

7. **Hard circuit breakers** — Max iterations, max cost ($), max time per mission. Backoff/cooldown on API failures.

8. **Durable mission state** — The mission ledger (SQLite) is the source of truth. Missions are resumable after crashes.

9. **Observe environment first (Pre-flight Assessment)** — Each fresh agent must inspect workspace state before acting. In resumed or interrupted missions, orchestration provides `git status`, `git diff`, verify.sh results in the prompt.

10. **Config freeze** — Mission config is locked at start, no live edits during execution.

11. **Load-bearing assumptions decay** — Every guardrail component encodes an assumption about what the model can't do on its own. Tag each mechanism with the capability gap it compensates for, and periodically audit whether it's still needed.

    | Component | Assumes model can't… |
    |-----------|----------------------|
    | Stall detection (two-step recovery) | Escape dead-end strategies on its own |
    | Claims table + lease/heartbeat | Self-coordinate task division by reading git alone |
    | Rolling summary in prompt | Fully reconstruct context from workspace state |
    | Homogeneous agents as default | Benefit from role specialization |
    | verify.sh priority over LLM | Self-evaluate quality reliably |
    | Fresh session per attempt | Maintain quality over long context |

## Design Decisions Summary

Decisions made through three-way consultation between Claude (Opus 4.6), Codex (GPT-5.4), and Gemini. Full details in spec files.

| ID | Decision | Spec |
|----|----------|------|
| D1 | Work isolation: git worktree per agent | [merge-protocol](specs/merge-protocol.md) |
| D2 | Merge timing: immediately after verification passes | [merge-protocol](specs/merge-protocol.md) |
| D3 | Conflict handling: agent resolves + atomic merge protocol | [merge-protocol](specs/merge-protocol.md) |
| D4 | Inter-agent awareness: claims table + pull main | [merge-protocol](specs/merge-protocol.md) |
| D5 | Task source: frontier of the acceptance checklist | [acceptance-graph](specs/acceptance-graph.md) |
| D6 | Prompt construction: fixed + dynamic segments | [agent-loop](specs/agent-loop.md) |
| D7 | Stall detection: two-step escalation | [agent-loop](specs/agent-loop.md) |
| D8 | LLM verifier: Gate + Critic, same model + isolation | [verifier](specs/verifier.md) |
| D9 | Workspace init: Planner + dependency-aware acceptance checklist | [acceptance-graph](specs/acceptance-graph.md) |
| D10 | Dirty state: pre-flight assessment, agent decides | [agent-loop](specs/agent-loop.md) |
| D11 | Attempt contract: lightweight feedforward | [agent-loop](specs/agent-loop.md) |

## Tech Stack

- **Language:** Python
- **Agent sandbox:** Docker (one container per agent, same image)
- **Agent backend:** `claude -p` (MVP), others later → [agent-backend spec](specs/agent-backend.md)
- **Mission persistence:** SQLite (WAL mode)
- **Version control:** Git (worktrees for isolation)
- **Process model:** Independent `while true` loops, no supervisor

## Agent Definition

Default homogeneous (same prompt + full toolset), optionally pin specialists. Pinning is a power-user optimization, not required.

## MVP

**Goal: 2 agents autonomously collaborate on a user-defined task via coding agent loops.**

**What MVP builds:**
1. Mission definition + workspace initialization (directory + git repo + SQLite ledger + acceptance checklist)
2. AgentBackend contract + ClaudeCodeBackend, CodexBackend, GeminiBackend
3. `while true` runner over durable mission state (ledger-based, resumable)
4. Verifier stack: verify.sh gate + LLM critic; LLM gate fallback when no verify.sh
5. Cross-iteration context: deterministic state + rolling summary + last verifier result + attempt contract
6. Circuit breakers (max iterations, max cost, max time)
7. Skills loading via native instruction files (CLAUDE.md / AGENTS.md / GEMINI.md)
8. Progress logging to SQLite ledger (per-group completion status)
9. Git worktree isolation (one worktree per agent, Docker container per agent)
10. Claims table + lease/heartbeat (claim per acceptance group, file overlap rule)
11. Atomic merge flow (merge lock → staging ref → regression verify → fast-forward main)
12. Stall detection and two-step recovery
13. Dependency-aware acceptance checklist with frontier computation
14. Attempt contract (auto-derived from last verifier result + frontier)

**What MVP does NOT build:**
- Web dashboard (later, reads from SQLite ledger)
- Additional agent backends beyond Claude/Codex/Gemini
- Pinned roles / specialist agents (later, MVP uses homogeneous agents)

## Implementation Specs

| Spec | Content |
|------|---------|
| [acceptance-graph](specs/acceptance-graph.md) | Data model, frontier computation, group completion, SQLite schema |
| [agent-loop](specs/agent-loop.md) | Loop pseudocode, attempt contract, prompt construction, stall detection, circuit breakers |
| [verifier](specs/verifier.md) | Gate/Critic architecture, verify.sh protocol, VerifierResult schema, LLM verifier config |
| [merge-protocol](specs/merge-protocol.md) | Atomic merge, claims table, file overlap rule, merge lock, SQLite schema |
| [agent-backend](specs/agent-backend.md) | AgentBackend protocol, AttemptSpec/Result, ClaudeCodeBackend implementation notes |

## Key References

### 1. Autoresearch
Single agent autonomous optimization loop by Andrej Karpathy. Key insight: **the loop is the product**.

### 2. Anthropic C Compiler Experiment (2026-02)
16 agents, 100K-line C compiler, no orchestrator. Blog: https://www.anthropic.com/engineering/building-c-compiler

### 3. Anthropic Harness Design for Long-Running Apps (2026-03)
GAN-inspired Generator/Evaluator separation. Blog: https://www.anthropic.com/engineering/harness-design-long-running-apps

### 4. MiroFish
Multi-agent social simulation. Key insight: **emergent coordination from individual autonomy**.

## Risk Checklist

- [ ] Verifier gaming: agents optimize the verifier if LLM fallback is weak
- [ ] Infinite loops: agents writing same broken code repeatedly
- [ ] Side-effect control: external actions need explicit opt-in
- [ ] Cost ceilings: token/time/tool budgets with kill switches
- [ ] Observability: store attempt traces, diffs, artifacts, scores

## External Consultations

### Codex (GPT-5.4) — 2026-03-23
Mission as first-class runtime, peer-to-peer agents, mission-scoped sandbox.

### Gemini — 2026-03-23
Mission Engine as new layer, stateless LLM + stateful sandbox, tool-driven coordination.

### Anthropic Harness Design Article — 2026-03-25
Verifier prompt as core asset, structured feedback, load-bearing assumptions decay, Planner step.

### Three-Way Discussion on Harness Design — 2026-03-25
Unified frontier model, attempt contract, Gate/Critic separation, fresh session rationale, atomic merge protocol.

### Phased Mode Discussion — 2026-03-25
"Parallel" and "phased" are special cases of a dependency-aware acceptance checklist. One runtime, no mode selection.

### Three-way Consultation — 2026-03-24
10 design decisions for multi-agent coordination, verification, and recovery. See Design Decisions Summary above.

## Relationship to Multibot

- **Multibot** continues as the group chat product
- **automission** is a separate project for autonomous mission execution
- Independent codebases, no dependency
