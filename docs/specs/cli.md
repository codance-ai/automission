# CLI Specification

> **This document is the stable CLI contract.** User-facing commands, flags, output formats, and exit codes defined here must not change without a deprecation cycle. Internal implementation details (Docker, backend adapters, etc.) are not part of this contract.

## Installation

```bash
pip install automission
```

## Authentication

API key lookup order (first match wins):

1. `--api-key` flag (per-command override)
2. `ANTHROPIC_API_KEY` environment variable
3. `~/.automission/config.toml` → `[keys] anthropic`

If no key is found, the CLI exits with exit code 3:

```
Error: No API key found. Set one of:
  1. export ANTHROPIC_API_KEY=sk-ant-...
  2. automission config set keys.anthropic sk-ant-...
  3. automission run --api-key sk-ant-... --goal "..."
```

When `--backend` is specified, the CLI looks up the corresponding key:

| Backend | Flag | Env Var | Config Key |
|---------|------|---------|------------|
| claude | `--api-key` | `ANTHROPIC_API_KEY` | `keys.anthropic` |
| codex | `--api-key` | `CODEX_API_KEY` | `keys.codex` |
| gemini | `--api-key` | `GOOGLE_API_KEY` | `keys.google` |

---

## Commands Overview

| Command | Description | MVP Status |
|---------|-------------|------------|
| `automission run` | Create and start a mission | **MVP** |
| `automission status` | Show mission status | **MVP** |
| `automission logs` | Show attempt history | **MVP** |
| `automission stop` | Stop a running mission | **MVP** |
| `automission list` | List all missions | **MVP** |
| `automission resume` | Resume a stopped/crashed mission | **MVP** |
| `automission export` | Export results to a directory | Planned |
| `automission limits` | Adjust runtime constraints | Planned |
| `automission config` | View/edit global configuration | Planned |

---

### `automission run`

Create and start a mission. **Blocks until mission completes by default.**

```bash
# Simplest: Planner generates acceptance checklist + verify.sh
automission run --goal "Build a TODO API with auth"

# Full control: provide everything
automission run \
  --goal "Build a calculator library" \
  --acceptance acceptance.md \
  --verify verify.sh \
  --agents 2

# Goal from file (for complex descriptions)
automission run --goal-file mission-brief.md

# Detach: start and return immediately
automission run --goal "Build a TODO API" --detach
# → Mission abc123 started. Use `automission logs -f` to follow.

# Non-interactive: skip Planner confirmation (for CI/CD)
automission run --goal "Build a TODO API" --yes

# With skills
automission run \
  --goal "Train a small GPT model" \
  --skill builtin:pytorch-training \
  --skill ./skills/my-tips.md
```

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--goal` | Yes* | — | Mission goal as text |
| `--goal-file` | Yes* | — | Mission goal from file path |
| `--acceptance` | No | — | Path to ACCEPTANCE.md. Omit to auto-generate via Planner |
| `--verify` | No | — | Path to verify.sh. Omit for LLM-only verification |
| `--skill` | No | — | Skill source (repeatable): local path, URL, or `builtin:name` |
| `--agents` | No | 2 | Number of agents |
| `--api-key` | No | — | API key (overrides env var and config) |
| `--backend` | No | claude | Agent backend: `claude`, `codex`, `gemini` |
| `--model` | No | sonnet | Model for agent execution (e.g. `sonnet`, `opus`, `claude-sonnet-4-6`) |
| `--planner-model` | No | sonnet | Model for Planner (acceptance checklist generation) |
| `--max-iterations` | No | 20 | Max attempts per agent |
| `--max-cost` | No | 10.0 | Max total cost in USD |
| `--timeout` | No | 3600 | Max wall-clock seconds |
| `--detach` | No | false | Start mission and return immediately |
| `--yes` / `-y` | No | false | Skip Planner confirmation (non-interactive) |
| `--no-planner` | No | false | Skip Planner even if `--acceptance` is omitted |
| `--json` | No | false | Output result as JSON |

\* Exactly one of `--goal` or `--goal-file` is required.

**Behavior:**

- Default: blocks until mission completes (or circuit breaker triggers), then exits with appropriate code
- `--detach`: prints mission ID and exits immediately with code 0. Use `status`/`logs -f`/`stop` to manage
- `Ctrl+C` during attached run: gracefully stops the mission (exit code 2)

**Planner flow (when `--acceptance` is omitted):**

```
$ automission run --goal "Build a TODO API with auth"

Planning mission...

Generated MISSION.md:
  Build a REST API for TODO management with user authentication...

Generated acceptance checklist (4 groups):
  [1] auth_schema (no deps)     — User table + auth DB schema
  [2] db_schema (no deps)       — TODO table schema
  [3] api_endpoints (→ 1, 2)    — CRUD endpoints with auth
  [4] input_validation (→ 3)    — Request validation on all endpoints

Generated verify.sh:
  pytest tests/ -v

Accept and start? [Y/n/edit]
> Y

Starting mission abc123 with 2 agents...
```

With `--yes`: skips the `[Y/n/edit]` prompt, starts immediately.

**JSON output (`--json`):**

```json
{
  "mission_id": "abc123",
  "status": "completed",
  "total_cost": 2.45,
  "total_attempts": 7,
  "duration_s": 754,
  "groups": {
    "auth_schema": true,
    "db_schema": true,
    "api_endpoints": true,
    "input_validation": true
  },
  "workspace": "~/.automission/missions/abc123"
}
```

---

### `automission status`

Show mission status.

```bash
automission status            # latest mission
automission status abc123     # specific mission
automission status --json     # JSON output
```

**Output:**

```
Mission: abc123
Status:  running
Agents:  2 (agent-1: active, agent-2: active)
Time:    12m 34s
Cost:    $2.45

Acceptance Checklist:
  ✓ auth_schema      [completed]    3 attempts, $0.60
  ✓ db_schema        [completed]    2 attempts, $0.40
  → api_endpoints    [in progress]  agent-1, attempt #2
  ○ input_validation [blocked]      depends on: api_endpoints

Progress: 2/4 groups completed
```

---

### `automission logs`

Show attempt history.

```bash
automission logs                  # all attempts, latest mission
automission logs abc123           # specific mission
automission logs --last 5         # last N attempts
automission logs --agent agent-1  # specific agent
automission logs -v               # verbose: include verification details
automission logs -f               # live follow (like tail -f)
automission logs -f -v            # live follow + verbose
automission logs --json           # JSON output
```

**Default output:**

```
#  Agent    Group           Duration  Cost   Gate    Contract
1  agent-1  auth_schema     2m 10s    $0.30  FAIL   "Create users table + auth schema"
2  agent-2  db_schema       1m 45s    $0.20  PASS   "Create todos table"
3  agent-1  auth_schema     3m 05s    $0.30  PASS   "Fix password_hash column + add migration"
4  agent-1  api_endpoints   2m 30s    $0.25  FAIL   "Implement POST/GET /todos"
5  agent-1  api_endpoints   2m 50s    $0.30  PASS   "Add auth middleware to endpoints"
```

**Verbose output (`-v`):**

```
Attempt #4 — agent-1
  Group:    api_endpoints
  Contract: "Implement POST/GET /todos"
  Duration: 2m 30s | Cost: $0.25 | Tokens: 12k in / 8k out
  Gate:     FAIL (verify.sh, exit code 1)
  Critic:
    Failed: "POST /todos returns 500 — no auth middleware"
    Failed: "GET /todos returns all todos, not filtered by user"
    Suggestion: "Add JWT auth middleware, filter todos by user_id"
  Files: src/api/todos.py (+45), src/api/auth.py (+0)
```

**Live follow output (`-f`):**

```
[12:03:15] agent-1  auth_schema     ATTEMPT #1  "Create users table + auth schema"
[12:05:22] agent-2  db_schema       ATTEMPT #1  "Create todos table"
[12:05:30] agent-2  db_schema       PASS ✓      $0.20  1m45s
[12:05:31] agent-2  db_schema       MERGED → main
[12:05:45] agent-1  auth_schema     FAIL ✗      "password_hash column missing"
[12:05:46] agent-1  auth_schema     ATTEMPT #2  "Fix password_hash + add migration"

Progress: 2/4 groups | Cost: $0.80 | Time: 5m38s
```

Last line refreshes in place. `Ctrl+C` exits the follow without stopping the mission.

---

### `automission stop`

Stop a running mission.

```bash
automission stop            # stop latest mission (with confirmation)
automission stop abc123     # stop specific mission (no confirmation)
automission stop --yes      # stop latest, skip confirmation
```

**Without explicit mission ID**, prompts for confirmation:

```
Stop mission abc123 (running, 2/4 groups, $2.45)? [Y/n]
> Y
Stopping mission abc123...
```

With `--yes` or explicit ID: no confirmation prompt. Agents finish current attempt, then exit. Mission marked `cancelled`.

---

### `automission list`

List all missions.

```bash
automission list
automission list --json
```

```
ID       Status     Agents  Groups  Cost    Created
abc123   running    2       2/4     $2.45   5 min ago
def456   completed  2       5/5     $4.80   2 hours ago
ghi789   failed     1       1/3     $10.00  yesterday
```

---

### `automission resume`

Resume a stopped or crashed mission.

```bash
automission resume abc123
automission resume abc123 --detach
```

Reads ledger, restarts agent loops from where they left off. Same blocking behavior as `run` (blocks by default, `--detach` to background).

---

### `automission export` *(Planned)*

Copy mission results to a target directory.

```bash
automission export abc123 --output ./my-new-project
```

Copies workspace source code (excluding automission internals) to the target directory.

---

### `automission limits` *(Planned)*

Adjust runtime constraints on a running mission.

```bash
automission limits abc123 --max-cost 20.0
automission limits abc123 --max-iterations 50
automission limits abc123 --timeout 7200
```

Only adjustable fields can be changed. Frozen fields (goal, agents, backend) cannot be modified.

---

### `automission config` *(Planned)*

View and edit global configuration.

```bash
automission config                              # show current config
automission config set keys.anthropic "sk-ant-..."
automission config set defaults.agents 4
automission config set defaults.backend codex
automission config set defaults.max_cost 20.0
```

---

## Configuration

### Global Config: `~/.automission/config.toml`

```toml
[defaults]
agents = 2
backend = "claude"
model = "sonnet"               # agent model (alias or full name)
max_iterations = 20
max_cost = 10.0
timeout = 3600

[keys]
anthropic = "sk-ant-..."       # or set ANTHROPIC_API_KEY env var
codex = "sk-..."               # or set CODEX_API_KEY env var
google = "..."                 # or set GOOGLE_API_KEY env var

[planner]
enabled = true
model = "sonnet"               # planner model

[verifier]
model = "sonnet"               # critic model
```

### Per-Mission Config: `~/.automission/missions/{id}/mission.toml`

Auto-generated at creation. Split into frozen (immutable) and adjustable fields.

```toml
id = "todo-api-001"
created_at = "2026-03-25T10:30:00Z"

# Frozen at creation — cannot change
[mission]
goal = "Build a TODO API with auth"
agents = 2
backend = "claude"

# Adjustable during execution via `automission limits`
[limits]
max_iterations = 20
max_cost = 10.0
timeout = 3600
```

### Priority Order

`CLI flags` > `per-mission config` > `global config` > `built-in defaults`

---

## Workspace

All missions live in `~/.automission/missions/{id}/`. Users never need to work inside this directory directly.

```bash
automission status abc123
# → Workspace: ~/.automission/missions/abc123/
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Mission completed successfully |
| 1 | Mission failed (verification never passed) |
| 2 | Mission cancelled by user (Ctrl+C or `stop`) |
| 3 | Configuration error (missing API key, invalid flags, bad config) |
| 4 | Mission not found (invalid ID for status/logs/stop/resume) |
| 5 | Resource limit exceeded (max_cost or max_iterations reached) |
