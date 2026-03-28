# Planner Spec

> Auto-generate MISSION.md + ACCEPTANCE.md + verify.sh from a brief user goal.

## Overview

When `automission run --goal "..."` is called without `--acceptance`, the Planner expands the goal into a structured mission plan. The user reviews and confirms (or edits) before the mission starts.

Planner specifies **what + constraints**, never **how**. Over-specification cascades errors downstream — agents should decide implementation details autonomously.

**MVP scope**: Planner uses the Anthropic API only (same as Verifier). Multi-backend planner support is out of scope.

## Architecture

Planner uses the same CLI-based approach as Agent and Critic: `claude -p --json-schema` for structured output. This unifies authentication (OAuth or API key) and eliminates the Anthropic SDK dependency.

```
User goal (1-4 sentences)
        │
        ▼
┌──────────────┐
│   Planner    │  claude -p --json-schema '...' --model <planner-model>
│  (CLI call)  │
└──────┬───────┘
       │ PlanDraft (structured JSON)
       ▼
┌──────────────┐
│  Validator   │  cycle detection, dangling deps, empty groups, id/name consistency
└──────┬───────┘
       │ validated PlanDraft
       ▼
┌──────────────┐
│   Renderer   │  deterministic: PlanDraft → MISSION.md, ACCEPTANCE.md, verify.sh
└──────┬───────┘
       │ file contents (strings)
       ▼
┌──────────────┐
│  CLI prompt  │  [Y/n/edit] — user reviews rendered files
└──────┬───────┘
       │ confirmed
       ▼
  create_mission()  ← receives content directly, not file paths
```

## Data Model

```python
@dataclass
class PlanCriterion:
    text: str                     # acceptance criterion description
    verification_hint: str        # how to verify (context for critic, not used in verify.sh)

@dataclass
class PlanGroup:
    id: str                       # snake_case, MUST equal _to_snake_case(name)
    name: str                     # human-readable display name (used as ## heading)
    depends_on: list[str]         # group IDs that must complete first
    criteria: list[PlanCriterion]

@dataclass
class PlanDraft:
    mission_summary: str          # expanded goal description + constraints
    constraints: list[str]        # non-functional constraints
    groups: list[PlanGroup]       # acceptance groups (valid DAG)
    verify_command: str           # single shell command to run tests (e.g., "pytest tests/ -v")
    assumptions: list[str]        # planner assumptions for user review
```

**Key invariant**: `group.id == _to_snake_case(group.name)` — this ensures ACCEPTANCE.md round-trips through `parse_acceptance_md()` correctly, since the parser derives `id` from the `##` heading.

## LLM Interaction

### Tool Schema

Single tool `submit_plan` forces structured output:

```python
_PLAN_TOOL = {
    "name": "submit_plan",
    "description": "Submit the structured mission plan.",
    "input_schema": {
        "type": "object",
        "required": ["mission_summary", "constraints", "groups", "verify_command"],
        "properties": {
            "mission_summary": {
                "type": "string",
                "description": "Expanded goal: what to build + key constraints. NO implementation details."
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Non-functional constraints (performance, security, compatibility)"
            },
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "name", "depends_on", "criteria"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "snake_case ID, must equal snake_case(name)"
                        },
                        "name": {
                            "type": "string",
                            "description": "Human-readable name (becomes ## heading in ACCEPTANCE.md)"
                        },
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "criteria": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["text", "verification_hint"],
                                "properties": {
                                    "text": {"type": "string"},
                                    "verification_hint": {
                                        "type": "string",
                                        "description": "How to verify: black-box check description (HTTP response, CLI output, file existence)"
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "verify_command": {
                "type": "string",
                "description": "Single shell command to run all tests (e.g., 'pytest tests/ -v'). Must be complete and runnable."
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Assumptions made that the user should review"
            }
        }
    }
}
```

### System Prompt

Key instructions for the over-specification guard:

- Specify observable outcomes and hard constraints
- Do NOT invent implementation details: no framework choices, file names, class names, or architecture decisions unless the user explicitly stated them in the goal
- Criteria describe **what** the system does, not **how** it's built
- `verification_hint` should describe black-box checks (HTTP responses, CLI output, file existence) not internal code structure
- 3-7 groups is typical; more than 10 is a red flag for over-specification
- Each group should have 2-5 criteria

### Model Selection

MVP: `--planner-model` CLI flag with default `claude-sonnet-4-6`. Full `config.toml` support is a separate issue.

Passed to `Planner.__init__(model=...)`.

## DAG Validation

After receiving the LLM response, validate before presenting to user:

1. **No cycles**: topological sort must succeed
2. **No dangling deps**: every ID in `depends_on` must exist in `groups`
3. **No self-deps**: group cannot depend on itself
4. **Non-empty groups**: every group has ≥1 criterion
5. **ID format**: all group IDs are valid snake_case
6. **ID/name consistency**: `_to_snake_case(group.name) == group.id` for every group
7. **Round-trip**: rendered ACCEPTANCE.md parses back through `parse_acceptance_md()` producing equivalent groups

If validation fails, attempt one **repair call** (send the error back to the LLM). If the repair also fails, show the error to the user and exit with code 3.

## Rendering

Deterministic functions convert PlanDraft to file contents. The LLM does not author file text directly.

### render_mission_md(draft) → str

```markdown
# Mission

{draft.mission_summary}

## Constraints

- {constraint_1}
- {constraint_2}
```

### render_acceptance_md(draft) → str

```markdown
# Acceptance Criteria

## {group.name}

Depends on: {dep_1}, {dep_2}

- {criterion.text}
- {criterion.text}
```

The `## heading` uses `group.name` (not `group.id`). The parser derives `id` via `_to_snake_case(name)`. This ensures round-trip compatibility with `parse_acceptance_md()`.

### render_verify_sh(draft) → str

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Auto-generated by automission planner
{draft.verify_command}
```

**verify.sh is immutable** — agents are told "Do not modify verify.sh" (existing workspace rule). The Planner must generate a complete, runnable test command (e.g., `pytest tests/ -v`) that will pass once agents implement the features correctly. This is NOT a skeleton.

## CLI Integration

### Input Precedence Rules

| User provides | Behavior |
|---------------|----------|
| `--acceptance` | Skip Planner, use provided ACCEPTANCE.md |
| `--acceptance` + `--verify` | Skip Planner, use both provided files |
| `--verify` (no `--acceptance`) | Run Planner for acceptance only, use user's verify.sh (ignore Planner's `verify_command`) |
| Neither | Run Planner, generate both ACCEPTANCE.md and verify.sh |
| `--no-planner` + `--acceptance` | Skip Planner, use provided file |
| `--no-planner` without `--acceptance` | **Error** (exit code 3): "Either provide --acceptance or remove --no-planner" |

### Planner Flow in `run` Command

```python
# In cli.py run() — before _run_mission()

need_planner = not acceptance and not no_planner
if no_planner and not acceptance:
    raise click.UsageError("Either provide --acceptance or remove --no-planner")

if need_planner:
    planner = Planner(anthropic_client, model=planner_model)
    draft = planner.plan(goal)

    # Display to user
    _display_plan_draft(draft)

    if not yes:
        choice = click.prompt("Accept and start?", type=click.Choice(["Y", "n", "edit"]), default="Y")
        if choice == "n":
            sys.exit(0)
        if choice == "edit":
            # Write to temp dir, open $EDITOR, read back
            draft = _edit_plan_draft(draft)

    # Convert draft to file contents for create_mission
    acceptance_content = render_acceptance_md(draft)
    mission_content = render_mission_md(draft)
    # User's --verify takes precedence over Planner's verify_command
    if not verify:
        verify_content = render_verify_sh(draft)
```

### Display Format

```
Planning mission...

Generated acceptance checklist (4 groups):
  [1] auth_schema (no deps)     — 3 criteria
  [2] db_schema (no deps)       — 2 criteria
  [3] api_endpoints (→ 1, 2)    — 4 criteria
  [4] input_validation (→ 3)    — 3 criteria

Constraints:
  - All endpoints return JSON
  - Authentication uses JWT tokens

Assumptions:
  - Using Python as implementation language
  - SQLite for database

Accept and start? [Y/n/edit]
```

### Edit Flow

When user chooses `edit`:
1. Write rendered ACCEPTANCE.md to a temp file
2. Open `$EDITOR` (fallback: `vi`)
3. After editor closes, re-parse through `parse_acceptance_md()` to validate
4. If parse fails, show error and re-open editor
5. Use edited content as the acceptance checklist

Only ACCEPTANCE.md is editable. MISSION.md is the original goal summary (not group-specific). verify.sh is a generic test command (e.g., `pytest tests/ -v`) — not tied to specific groups, so editing acceptance groups does not require regenerating it. If the user needs to customize verify.sh, they can provide `--verify`.

## workspace.py Changes

`create_mission()` currently takes `acceptance_path: Path | None` and `verify_path: Path | None`. Add support for direct content:

```python
def create_mission(
    ...
    acceptance_path: Path | None = None,
    acceptance_content: str | None = None,   # NEW: direct content from Planner
    verify_path: Path | None = None,
    verify_content: str | None = None,       # NEW: direct content from Planner
    mission_content: str | None = None,      # NEW: override default MISSION.md
    ...
) -> Path:
```

Priority: if both path and content are provided, content wins (Planner output takes precedence).

## Planner Class

```python
class Planner:
    def __init__(
        self,
        anthropic_client: Any,
        model: str = "claude-sonnet-4-6",
    ):
        self.client = anthropic_client
        self.model = model

    def plan(self, goal: str) -> PlanDraft:
        """Generate structured plan from goal. Validates DAG before returning."""
        raw = self._call_llm(goal)
        draft = self._parse_response(raw)
        self._validate_dag(draft)
        return draft

    def _call_llm(self, goal: str) -> dict:
        """Single API call with submit_plan tool."""
        ...

    def _parse_response(self, raw: dict) -> PlanDraft:
        """Convert tool_use JSON to PlanDraft dataclass."""
        ...

    def _validate_dag(self, draft: PlanDraft) -> None:
        """Raise PlanValidationError if DAG is invalid."""
        ...

    def _repair(self, goal: str, error: str) -> PlanDraft:
        """One retry with error feedback."""
        ...
```

## Error Handling

- **No API key**: skip Planner, exit with error suggesting `--acceptance` flag
- **API call fails**: log error, exit with code 3 and message suggesting `--acceptance`
- **Validation fails after repair**: show the specific validation error, exit code 3
- **User declines (n)**: exit code 0

## Config

MVP: `--planner-model` CLI flag with default `claude-sonnet-4-6`.

Future (separate issue): read from `[planner] model` in `~/.automission/config.toml`.

## Testing

- `test_planner.py`: unit tests for Planner with mocked Anthropic client
  - Valid plan generation (mock tool_use response → PlanDraft)
  - DAG validation (cycles, dangling deps, self-deps, empty groups)
  - Repair flow (first call invalid, repair succeeds)
  - Over-specification detection (not blocking, just testing prompt)
- `test_planner_render.py`: rendering tests
  - render_acceptance_md round-trips through parse_acceptance_md
  - render_verify_sh is valid bash (shellcheck if available)
  - render_mission_md includes summary and constraints
- `test_cli.py` additions: Planner integration in run command
  - `--yes` skips confirmation
  - `--no-planner` skips Planner entirely
  - `--acceptance` skips Planner
- `test_workspace.py` additions: content-based creation
  - `acceptance_content` parameter works
  - `verify_content` parameter works

## Files Changed

| File | Change |
|------|--------|
| `src/automission/planner.py` | **NEW** — Planner class, tool schema, validation, rendering |
| `src/automission/models.py` | Add PlanDraft, PlanGroup, PlanCriterion dataclasses |
| `src/automission/workspace.py` | Add `acceptance_content`, `verify_content`, `mission_content` params |
| `src/automission/cli.py` | Planner integration in `run`, `--planner-model`/`--no-planner` flags, display + edit flow |
| `tests/test_planner.py` | **NEW** — Planner unit tests |
| `tests/test_workspace.py` | Tests for content-based creation |
| `tests/test_cli.py` | Tests for Planner CLI integration |
