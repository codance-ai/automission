# Acceptance Checklist Spec

Extracted from vision.md D5, D9. See vision.md for design rationale and consultation history.

## Overview

Mission acceptance criteria form a dependency-aware checklist. Agents work the **frontier** — groups whose dependencies are satisfied but which haven't passed yet. This single model replaces separate "parallel" and "phased" modes.

- Wide frontier (many independent groups) = parallel execution
- Narrow frontier (chained dependencies) = sequential execution
- DAG = mixed

## Data Model

```python
@dataclass
class AcceptanceGroup:
    id: str
    name: str
    depends_on: list[str]      # IDs of groups that must complete first; empty = ready from start
    criteria: list[Criterion]

@dataclass
class Criterion:
    id: str
    group_id: str
    text: str
    required: bool = True
```

## Frontier Computation

```python
def frontier(groups: list[AcceptanceGroup], statuses: dict[str, bool]) -> list[AcceptanceGroup]:
    """Return groups that are ready to work on: all deps satisfied, self not yet complete."""
    return [
        g for g in groups
        if all(statuses.get(dep, False) for dep in g.depends_on)
        and not statuses.get(g.id, False)
    ]
```

## Group Completion

A group is complete when all its `required` criteria pass. Group statuses are stored in the ledger and updated after each verification (via `VerifierResult.group_statuses`).

Mission is complete when the frontier is empty (all groups satisfied).

## Example

"Build a TODO app with auth and real-time sync":

```
auth_schema:      depends_on: []                              <- frontier at start
db_schema:        depends_on: []                              <- frontier at start
api_endpoints:    depends_on: [auth_schema, db_schema]        <- unlocks after both done
auth_middleware:   depends_on: [auth_schema]                   <- unlocks after auth_schema
ui_components:    depends_on: [api_endpoints, auth_middleware] <- unlocks last
```

## Generation

The optional Planner step (D9) generates the acceptance checklist from a brief goal. Rules:
- Specify **what + constraints**, not implementation details
- Over-specification cascades errors downstream
- User reviews and confirms before mission starts

## SQLite Schema

```sql
CREATE TABLE acceptance_groups (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    name TEXT NOT NULL,
    depends_on TEXT NOT NULL DEFAULT '[]',  -- JSON list of group IDs
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMP
);

CREATE TABLE acceptance_criteria (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES acceptance_groups(id),
    text TEXT NOT NULL,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    last_checked_at TIMESTAMP
);
```
