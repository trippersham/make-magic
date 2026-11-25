# Finishing: file unfiled work, then archive one session

The active per-session finish action. It runs on one session the `mm-flow-sessions` map identifies — reconcile its work, file what is unfiled as tickets, archive the session, update the board. It runs **bounded by a hard per-run cap and an incremental cursor** (shared with the orchestrator's sweep), so a batch of finishes can never run away.

> Built in Phase 3 — the archive shim and the merged-PR reconciliation are TDD-built scripts bundled with the steward; closure and ticket-filing delegate to existing rae-flow skills. The REST shim mechanics are confirmed (design §7); the script is not yet written.

## The two finish actions (in order)

1. **File unfiled incomplete work as tickets** — anything abandoned in a session or on its branch becomes a ticket at the right funnel Status, reusing `/executing`'s discovery-to-ticket flow and delegating the ticket writing to `/managing-backlog`. This happens **first**, before archiving, so no work is lost when the session is hidden.
2. **Archive the finished working session** — when a card hits Done, its mapped session is a known, closeable target → archive it (shim below). This solves the 102-accumulated-sessions problem from the *issue* side, not by blind enumeration.

Reconciling verifiably-done issues (merged `Closes #N`) produces a **proposed** closure — closing an issue is human-gated, so finish never closes autonomously; it folds the proposal into the roll-up (the `mm-steward-dev-flow` skill owns the roll-up).

<never-auto-close>
**Never close on staleness, never auto-delete.** Stale is a **surfacing label**, not an action. A merged `Closes #N` is *verifiable* evidence a card is done and can be *proposed* for closure; "nobody touched this in 30 days" is not — it may be legitimately parked, blocked on a human, or simply low-priority. Auto-closing on age destroys real work and trains the human to distrust the steward. Branch/worktree deletion is destructive and irreversible, so it is always human-gated.
</never-auto-close>

## The archive shim (closes #103)

Archiving is **separate from close** and has **no CLI or MCP verb** — it is an owner-only REST `PATCH`:

```text
PATCH http://127.0.0.1:6767/v1/sessions/{conversation_id}
body: { "archived": true }
```

- Authenticate by reading the local server's signature at `~/.omnigent/local_server.sig` (loopback).
- `archived=true` hides the session from the default view, prunes read-state, and spawns a deferred runner teardown with an Undo window.
- **`delete_worktree` defaults to `false`** — it is destructive (deletes the on-disk worktree) and runs **only on explicit human ok**, never by default.

<shim-rationale>
**Why a shim at all:** `sys_session_close` is a tree-scoped tombstone — it cannot touch a non-child top-level session (which is what every working session is), and it does not stop the runner or hide anything (see the session verbs in the `mm-flow-sessions` skill). The owner-only `PATCH {archived:true}` is the *only* mechanism that actually stops the runner and clears the session from view, which is exactly what the #103 pile of ~100 sessions needs.
</shim-rationale>

## Do / do not

| Do | Do not |
|---|---|
| File unfiled work as tickets before you archive | Archive while work is still only in the session's context |
| Archive a Done card's mapped session via the loopback `PATCH {archived:true}` | Call `sys_session_close` and assume the session is gone |
| Propose closure from a merged `Closes #N` | Close any issue autonomously, or close on staleness |
| Flag a stale branch/worktree to the human | Delete a branch or pass `delete_worktree:true` without explicit human ok |
| Run under the shared per-run cap + cursor | Enumerate all sessions to find ones to finish |
