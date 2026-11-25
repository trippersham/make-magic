---
name: mm-finish-session
description: Use to finish ONE make-magic working session cleanly — reconcile its work against its issues and PRs, file any unfiled incomplete work as tickets, archive the session, and update the board. Human- or steward-invoked. This is the per-session finish action the steward's hygiene valve routes to (the sweep-level detection lives in mm-steward-dev-flow). Use when the user says "finish this session", "clean up finished sessions", "archive done sessions", "wrap up the session on #N", "close out this session", or "file the leftover work", or when the orchestrator detects a Done card whose mapped session is ready to archive. It never archives unreconciled work and never deletes a worktree without explicit human ok.
---

# Finish session: reconcile, file, archive — one session, cleanly

This skill drains **one** finished or abandoned working session out of the bottom of the funnel, cutting the human's context-switching cost. It is the **active per-session half** of the steward's hygiene valve: the `mm-steward-dev-flow` orchestrator's sweep (`hygiene-sweep.md`) *detects* a Done card's mapped session or an abandoned session and *routes it here*; this skill does the actual reconcile → file → archive → update. It consumes the session↔issue map that the `mm-flow-sessions` skill owns — it does not reimplement the map or the session verbs.

> Built in Phase 3 — the archive shim and the merged-PR reconciliation are TDD-built scripts bundled with the steward; closure and ticket-filing delegate to existing rae-flow skills. The REST shim mechanics are confirmed (design §7); the script is not yet written.

## Primary constraint: file first, verify before you archive, never auto-delete

<primary-constraint>
Finish a session in a **fixed order** and never skip ahead:

1. **Reconcile** the session's work against its linked issues and PRs — what landed, what is merged (`Closes #N`), what is half-done.
2. **File every piece of unfiled incomplete work as a ticket FIRST** — anything abandoned in the session or on its branch becomes a ticket at the right funnel Status (delegate the ticket writing to `/managing-backlog`, reusing `/executing`'s discovery-to-ticket flow). Nothing is dropped.
3. **Archive the session** only once reconciliation shows its work is either landed or captured as a ticket — via the owner-only loopback `PATCH {archived:true}` (see `references/finishing.md`).
4. **Update the board** — propose any closure of verifiably-done issues (closing is human-gated), note the archive in the roll-up.

**`delete_worktree` defaults to `false`** and runs **only on explicit human ok** — never by default, never as part of routine finish.
</primary-constraint>

<rationale>
**Why file before archive:** archiving stops the runner and hides the session; if unfiled work is still only in that session's context, archiving *loses it*. Filing it as a ticket first makes the work durable on the board before the session disappears, so the order is load-bearing, not cosmetic. **Why verify before archive:** "looks done" is not evidence — a merged `Closes #N` is verifiable, an idle session is not. Archiving an unreconciled session can hide work that was never captured. **Why never auto-delete a worktree:** branch/worktree deletion is destructive and irreversible; a wrongly-deleted worktree cannot be recovered, so it is always a human decision. These mirror the orchestrator's primary constraint — closing and deleting are permanently human-gated.

**Do instead:** reconcile → file unfiled work as tickets → archive the session → propose closure + roll-up. Leave `delete_worktree:false`; surface a stale worktree to the human instead of deleting it.
</rationale>

## The archive shim (closes #103)

Archiving is **separate from close** and has **no CLI or MCP verb** — it is an owner-only REST `PATCH` against the local omnigent server, authenticated by the loopback signature. `sys_session_close` is a tree-scoped tombstone that cannot touch a top-level working session and does not stop the runner; the `PATCH {archived:true}` is the only mechanism that actually stops the runner and clears the session from view — exactly what the #103 pile of ~100 sessions needs.

<reference file="references/finishing.md" section="The archive shim (closes #103)">
The exact `PATCH` endpoint and body, the `~/.omnigent/local_server.sig` loopback auth, what `archived=true` does (hides, prunes read-state, deferred runner teardown with an Undo window), the `delete_worktree=false` default, the per-session filing flow, and the per-run cap + cursor it runs under. Read it before archiving any session.
</reference>

## Who the map hands you

The session to finish is identified by the `mm-flow-sessions` map (route on the immutable `conversation_id`), not by enumerating sessions. When a card hits Done, its mapped session is a known, closeable target — this solves the 102-accumulated-sessions problem from the *issue* side, not by blind enumeration. The one cheap `get_info` the cost contract allows (on the single session about to be archived) is the only MCP read this skill needs.

## Do / do not

| Do | Do not |
|---|---|
| File unfiled incomplete work as tickets before archiving | Archive a session while its work is still only in its context |
| Archive a Done card's mapped session via the loopback `PATCH {archived:true}` | Call `sys_session_close` and assume the session is gone |
| Propose closure from a merged `Closes #N` | Close an issue autonomously, or close on staleness |
| Leave `delete_worktree:false`; flag a stale worktree to the human | Delete a branch or pass `delete_worktree:true` without explicit human ok |

## Reference index

| When you need to… | Read / invoke |
|---|---|
| The archive shim, loopback auth, filing flow, per-run cap | `references/finishing.md` |
| The session↔issue map that identifies the session | the `mm-flow-sessions` skill |
| The sweep that detects and routes sessions here | the `mm-steward-dev-flow` skill |
| Write the tickets for unfiled work | `/managing-backlog` (via `/executing`'s discovery-to-ticket flow) |
