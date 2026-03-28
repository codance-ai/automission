# Agent Backend Spec

Extracted from vision.md AgentBackend Contract section. See vision.md for design rationale.

## Overview

automission does NOT build its own agent runtime. It reuses existing coding agents (Claude Code, Codex, Gemini CLI) as execution engines via a strict adapter interface.

## AgentBackend Protocol

```python
class AgentBackend(Protocol):
    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None
        """Write stable mission context into AUTOMISSION.md + native instruction file. Called once at mission creation."""

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult
        """Execute one attempt. Fresh session, no prior state."""

# Stable context: written to AUTOMISSION.md once at mission creation, does not change between attempts
@dataclass
class StableContext:
    goal: str                 # mission goal (also in MISSION.md)
    skills: list[str]         # vendored skill file contents
    side_effect_policy: str   # what the agent is/isn't allowed to do
    rules: list[str]          # additional rules (e.g., "do not modify verify.sh")

# Dynamic context: passed via -p flag, changes every attempt
@dataclass
class AttemptContext:
    last_verification: str    # structured VerifierResult as text
    contract: str             # attempt contract (scope, done criteria, non-goals)
    rolling_summary: str      # mission progress summary
    frontier_groups: str      # current frontier description

@dataclass
class AttemptSpec:
    attempt_id: str
    mission_id: str
    workdir: Path
    prompt: str               # dynamic context formatted for -p flag
    timeout_s: int
    env: dict[str, str]

@dataclass
class AttemptResult:
    status: Literal["completed", "failed", "timed_out", "crashed"]
    exit_code: int
    transcript_path: Path    # full raw interaction log
    token_usage: TokenUsage  # input/output counts
    cost_usd: float
    duration_s: float
    changed_files: list[str] # files modified during this attempt
```

## Hard Semantics

- Backend must run in the provided `workdir`
- Must not rely on hidden prior session state (fresh session per attempt)
- Timeout means best-effort kill + `timed_out` status
- `"completed"` only means the session ended, NOT that the mission succeeded
- Do not parse stdout for correctness — treat it as observability only

## Two-Channel Context Delivery

Stable content and dynamic content use different delivery channels:

| Content | Delivery | When |
|---------|----------|------|
| Mission goal, skills, rules | `AUTOMISSION.md` (read-only file) | Written once at mission creation |
| Pointers to MISSION.md, ACCEPTANCE.md, verify.sh | Native instruction file (CLAUDE.md etc.) | Written once at mission creation |
| Last verification, attempt contract, progress | `-p` flag | Every attempt |

### AUTOMISSION.md (stable, read-only)

A dedicated file that automission owns. Agents are instructed NOT to edit it.

```markdown
# AUTOMISSION.md — Mission Instructions (DO NOT EDIT)

## Mission
See MISSION.md for full goal description.
See ACCEPTANCE.md for acceptance criteria and dependencies.
Run `bash verify.sh` to check your work.

## Skills

### PyTorch Training
...skill content...

### Code Quality
...skill content...

## Rules
- Do not modify: AUTOMISSION.md, MISSION.md, ACCEPTANCE.md, verify.sh, mission.db
- Do not execute side effects (git push, API calls) unless explicitly allowed
- Read ACCEPTANCE.md before starting work
- Run verify.sh after making changes
```

### Native Instruction File (one line, points to AUTOMISSION.md)

Each backend writes a minimal pointer in its native instruction file:

| Backend | File | Content |
|---------|------|---------|
| Claude Code | `CLAUDE.md` | `Read AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.` |
| Codex | `AGENTS.md` | `Read AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.` |
| Gemini CLI | `GEMINI.md` | `Read AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.` |

If the user already has a CLAUDE.md/AGENTS.md/GEMINI.md, the pointer line is appended. No managed block, no risk of agent corrupting automission content.

### -p Prompt (dynamic, every attempt)

```
claude -p "
## Current Status
Last attempt: 4/6 groups passed.
Failed criteria:
  - input_validation: POST /tasks returns 500 on empty body
Suggestion: Add pydantic model validation to POST/PUT endpoints

## This Attempt
Scope: Fix input validation on POST /tasks and POST /register
Done criteria: tests 7 and 8 pass
Non-goals: Don't touch auth middleware this round

## Progress
5 attempts completed. Test pass rate improved from 4/10 to 8/10.

Start by running verify.sh to see current state, then focus on the scope above.
"
```

### Why this split

1. AUTOMISSION.md is stable — never rewritten during mission execution
2. Native instruction file is untouched (just one pointer line) — agent can freely edit its own CLAUDE.md
3. Dynamic context goes through `-p` where it belongs
4. Easy to debug: inspect AUTOMISSION.md for stable context, check -p for dynamic context

## Skill Vendoring

Skills are resolved and vendored into the mission workspace at creation time:

```
~/.automission/missions/{id}/
└── skills/
    ├── pytorch-training.md          # vendored copy
    ├── code-quality.md              # vendored copy
    └── manifest.json                # source metadata
```

`manifest.json`:
```json
{
  "skills": [
    {"name": "pytorch-training", "source": "builtin:pytorch-training", "hash": "sha256:abc123..."},
    {"name": "code-quality", "source": "./skills/code-quality.md", "hash": "sha256:def456..."},
    {"name": "testing", "source": "https://example.com/skill.md", "hash": "sha256:789abc..."}
  ]
}
```

This ensures:
- Resume works even if original URL/path is gone
- Exact skill content is reproducible
- Source metadata preserved for traceability

## ClaudeCodeBackend (Tier 1)

Primary supported backend. Full E2E testing.

```python
class ClaudeCodeBackend(AgentBackend):
    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None:
        # Write AUTOMISSION.md (stable, read-only)
        (workdir / "AUTOMISSION.md").write_text(format_automission_md(stable))

        # Append pointer to CLAUDE.md (or create if not exists)
        claude_md = workdir / "CLAUDE.md"
        pointer = "\nRead AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.\n"
        if claude_md.exists():
            claude_md.write_text(claude_md.read_text() + pointer)
        else:
            claude_md.write_text(pointer)

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        result = subprocess.run(
            ["claude", "-p", spec.prompt, "--workdir", str(spec.workdir)],
            timeout=spec.timeout_s,
            env={**os.environ, **spec.env},
            capture_output=True,
        )
        return parse_attempt_result(result, spec)
```

### Implementation Notes

- Extract `changed_files` via `git diff --name-only` before/after
- Token usage and cost: parse from Claude Code's session summary or estimate from transcript size
- Timeout: process-level (SIGTERM → SIGKILL)

## CodexBackend (Experimental)

```python
class CodexBackend(AgentBackend):
    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None:
        (workdir / "AUTOMISSION.md").write_text(format_automission_md(stable))
        agents_md = workdir / "AGENTS.md"
        pointer = "\nRead AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.\n"
        if agents_md.exists():
            agents_md.write_text(agents_md.read_text() + pointer)
        else:
            agents_md.write_text(pointer)

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        result = subprocess.run(
            ["codex", "-q", spec.prompt, "--workdir", str(spec.workdir)],
            timeout=spec.timeout_s,
            env={**os.environ, **spec.env},
            capture_output=True,
        )
        return parse_attempt_result(result, spec)
```

## GeminiBackend (Experimental)

```python
class GeminiBackend(AgentBackend):
    def prepare_workspace(self, workdir: Path, stable: StableContext) -> None:
        (workdir / "AUTOMISSION.md").write_text(format_automission_md(stable))
        gemini_md = workdir / "GEMINI.md"
        pointer = "\nRead AUTOMISSION.md for mission goals, skills, and rules. DO NOT EDIT AUTOMISSION.md.\n"
        if gemini_md.exists():
            gemini_md.write_text(gemini_md.read_text() + pointer)
        else:
            gemini_md.write_text(pointer)

    def run_attempt(self, spec: AttemptSpec) -> AttemptResult:
        result = subprocess.run(
            ["gemini", "-p", spec.prompt, "--workdir", str(spec.workdir)],
            timeout=spec.timeout_s,
            env={**os.environ, **spec.env},
            capture_output=True,
        )
        return parse_attempt_result(result, spec)
```

## Model Selection

User specifies model via `--model` flag or `config.toml`. The model name is passed directly to the CLI:

```python
# claude
["claude", "-p", spec.prompt, "--model", spec.model, ...]

# codex
["codex", "-q", spec.prompt, "--model", spec.model, ...]

# gemini
["gemini", "-p", spec.prompt, "--model", spec.model, ...]
```

Model names are backend-specific (e.g., `sonnet`, `opus` for Claude; `gpt-5.4` for Codex). automission does not validate model names — the CLI does.

## Unified CLI Architecture

All LLM interactions in automission go through CLI tools, not SDKs:

| Component | CLI | Purpose |
|-----------|-----|---------|
| Agent | `claude -p` / `codex -q` / `gemini -p` | Code execution |
| Planner | `claude -p --json-schema` | Generate acceptance checklist |
| Critic | `claude -p --json-schema` | Analyze verification results |

This eliminates SDK dependencies and unifies authentication — OAuth or API key, the CLI handles both.

### Structured Output for Planner/Critic

Planner and Critic need structured JSON output. Use `--json-schema` (Claude) or equivalent:

```bash
claude -p "Analyze this verification result..." \
  --model sonnet \
  --json-schema '{"type":"object","properties":{...},"required":[...]}' \
  --output-format json
```

Safeguards:
- Local schema validation after CLI returns
- 1 automatic retry on parse failure
- Fallback to basic result if retry also fails

## Docker Integration

Each agent runs in its own Docker container:
- Same base image for all agents, CLI tools pre-installed
- Workspace mounted as volume (`-v workspace:/workspace`)
- Authentication: inherits host environment (API key via `-e KEY_NAME`, or OAuth via `docker exec` login)
- Backend selection determines which CLI is invoked
- `--dangerously-skip-permissions` enabled (container is the sandbox)

## Supported Backends (MVP)

| Backend | CLI | Agent | Planner/Critic | Status |
|---------|-----|-------|----------------|--------|
| `claude` | `claude -p` | ✅ | ✅ (`--json-schema`) | **Tier 1** |
| `codex` | `codex -q` | ✅ | Experimental | Experimental |
| `gemini` | `gemini -p` | ✅ | Not yet | Experimental |

User selects via `--backend` flag or `config.toml`. `--model` selects the specific model.
