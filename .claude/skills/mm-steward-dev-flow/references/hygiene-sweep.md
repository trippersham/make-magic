# Hygiene sweep: detect & propose, never act on one session

This is the **sweep half** of the hygiene valve — the board-wide detect/propose pass the steward runs on every poll to find finished and abandoned work at the bottom of the funnel. It **surfaces and proposes**; it never closes an issue, never deletes a branch, and never archives a session itself. The **per-session finish action** — reconcile one session's work, file unfiled work as tickets, archive that session, update the board — is the separate `mm-finish-session` skill. The sweep detects; `mm-finish-session` acts on one session.

The sweep is **bounded by a hard per-run cap and an incremental cursor**, so it can never run away.

> Built in Phase 3 — the merged-PR reconciliation is a TDD-built script bundled with the steward; closure and ticket-filing delegate to existing rae-flow skills. The REST shim the finish action uses is confirmed (design §7) but lives in `mm-finish-session`.

## The sweep's three detect/propose actions

1. **Propose closure of verifiably-done work** — reconcile merged PRs ↔ open issues (`Closes #N`) and **propose** closure. Closing an issue is human-gated, so this ships in propose-mode; the sweep never closes.
2. **Detect a Done card's mapped session and hand it off** — when a card hits Done, its mapped session (from the `mm-flow-sessions` map) is a known, closeable target. The sweep **routes it to `mm-finish-session`**, which reconciles and archives it. This solves the 102-accumulated-sessions problem from the *issue* side, not by blind enumeration.
3. **Flag stale branches / sessions** to the human — surfacing only, folded into the next roll-up (`references/rollup.md`). Abandoned session/branch work that needs filing is also routed to `mm-finish-session` (which reuses `/executing`'s discovery-to-ticket flow); the sweep only flags it.

<never-auto-close>
**Never auto-close on staleness, never auto-delete.** Stale is a **surfacing label**, not an action. A merged `Closes #N` is *verifiable* evidence a card is done and can be *proposed* for closure; "nobody touched this in 30 days" is not — it may be legitimately parked, blocked on a human, or simply low-priority. Auto-closing on age destroys real work and trains the human to distrust the steward. Branch/worktree deletion is destructive and irreversible, so it is always human-gated (enforced in `mm-finish-session`, where `delete_worktree` defaults to `false`).
</never-auto-close>

## One shared live-session pass (fold in the stack-hygiene sweep)

An hourly omnigent task — `make-magic stack hygiene sweep (OBSERVE)` — is **active right now** (reads git state, mutates nothing, runs `scripts/stack_hygiene.py sweep`). It already implements the cheap live-session pass this design needs (page list → filter `runner_online` → `get_info` only those → judge liveness by `last_activity_at` — the cost contract the `mm-flow-sessions` skill holds).

- **Decision:** the steward **owns this recurring concern** — run the live-session pass **once** per sweep and feed *both* stack-hygiene *and* its own session-map reconciliation + backlog cleanup, instead of two independent hourly enumerations. *(Confirm the subsume vs. coexist decision before Phase 3.)*
- It currently has **no `max_cost_usd` cap** — add one when folding it in (note: `max_cost_usd` must be set in the omnigent **UI**, not the API).

<one-pass-rationale>
**Why one pass, not two:** every enumeration of the session set costs the cost-contract budget (one list pass + bounded `get_info`). Two independent hourly sweeps double that cost for the same reads and risk two agents racing on the same sessions. One shared pass per sweep feeds every consumer from a single list, keeping the #102 contract intact.
</one-pass-rationale>

## Do / do not

| Do | Do not |
|---|---|
| Propose closure from a merged `Closes #N` | Close any issue autonomously, or close on staleness |
| Route a Done card's session to `mm-finish-session` to archive | Archive or `sys_session_close` a session from the sweep itself |
| Flag stale branches/sessions to the human in the roll-up | Delete a branch or pass `delete_worktree:true` from the sweep |
| Run one shared live-session pass under the per-run cap + cursor | Enumerate all sessions, or run two independent hourly sweeps |
