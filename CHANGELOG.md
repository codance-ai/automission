# Changelog

## 0.1.1 — 2026-03-28

- Fix: restore missing `click` and `jsonschema` in package dependencies

## 0.1.0 — 2026-03-28

Initial public release.

### Features

- **Goal-driven execution** — describe a goal, Planner generates an acceptance checklist with dependencies, agents execute autonomously
- **Multi-agent collaboration** — parallel agents coordinate via shared git + SQLite ledger, no central orchestrator
- **Multi-backend support** — Claude Code, Codex CLI, and Gemini CLI as interchangeable agent/planner/critic backends
- **Docker-first isolation** — all agent execution runs inside containers for reproducibility
- **Daemon mode** — `--detach` to run in background, `attach` / `stop` / `resume` to control
- **Structured output** — JSON schema validation for planner and verifier outputs across all backends
- **Safety rails** — circuit breakers on cost, time, and iterations; 3-step stall detection with auto-recovery
- **Interactive setup** — `automission init` walks through backend selection, auth, and Docker image verification
