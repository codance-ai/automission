# Merge Protocol & Claims Spec

Extracted from vision.md D1-D4, D5 (claims), Shared State Design. See vision.md for design rationale.

## Work Isolation (D1)

Each agent works in its own git worktree (isolated branch). One worktree per agent, one Docker container per agent.

## Atomic Merge Protocol (D3)

Merge to main must be safe — a failed regression must not poison main for other agents.

```
1. Agent acquires merge lock (SQLite row lock, one agent merges at a time)
2. Rebase onto current main
3. Merge to a temporary staging ref (not main yet)
4. Run regression verification on the staged state
5. If regression passes -> fast-forward main to staged ref -> release lock
6. If regression fails -> discard staged ref -> release lock -> record as "merge_rejected" in ledger
```

This prevents:
- **Poisoned main:** bad merge landed, other agents rebase onto it
- **Rebase race:** two agents competing to merge simultaneously

## Merge Timing (D2)

Merge immediately after attempt passes verification. Don't wait — the sooner changes land in main, the less branch drift.

## Conflict Handling (D3)

Before merge, agent rebases main. If conflicts:
- Agent resolves (has best context)
- If fails 1-2 times, abandon this attempt
- Claiming at AcceptanceGroup level reduces logical conflicts

## Claims Table

```python
@dataclass
class TaskClaim:
    id: str
    mission_id: str
    agent_id: str
    group_id: str             # The Acceptance Group being worked on
    status: str               # "active", "completed", "failed"
    claim_contract: str       # JSON copy of the Attempt Contract (D11)
    heartbeat_at: datetime    # Expired leases are auto-released
    expires_at: datetime
```

### Claim Rules

- **Granularity:** One agent claims exactly one AcceptanceGroup from the frontier at a time
- **Lease/heartbeat:** Expired leases auto-released so crashed agents don't block others
- **Coordination:** Claims table prevents two agents from working on the same group simultaneously

### File Overlap Rule

Claims are **semantic reservations** on acceptance groups, not file locks. Two agents claiming different groups may still touch the same files. This is acceptable:
- Worktree isolation prevents physical conflicts during work
- Atomic merge protocol catches integration issues via regression verification
- Agents should use `claim_contract` to signal intent, reducing avoidable overlap

## SQLite Schema

```sql
CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active, completed, failed, expired
    claim_contract TEXT,                     -- JSON: attempt contract
    heartbeat_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Merge lock: only one row, acquired via UPDATE ... WHERE status = 'free'
CREATE TABLE merge_lock (
    id INTEGER PRIMARY KEY DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'free',    -- free, held
    held_by TEXT,                            -- agent_id
    acquired_at TIMESTAMP
);
```

## Inter-Agent Awareness (D4)

Two layers:
1. **Claims table:** "who is doing what" (prevents duplicate work)
2. **Pulling main:** "what has been done" (code awareness)
