---
name: mm-flow-sessions
description: Use to spawn, drive, read, and reconcile the omnigent working sessions the make-magic steward coordinates, and to maintain the many-to-many session↔issue map. This is the steward's session substrate (session_map.py + the omnigent sys_session_* verbs). Use when the steward orchestrator (mm-steward-dev-flow) needs to route a session to an issue, classify sessions into mapped / expressly-unmapped / not-ours buckets, message a working session for a check-in, or run the one cheap session-list pass per sweep. Use when the user says "which session is working on #N", "map the sessions", "reconcile the session map", "list the working sessions", "message that session", or "how many sessions are mapped". It obeys the #102 cost contract and never interrogates an unmapped session.
---

# Flow sessions: the omnigent session substrate

How the steward spawns, drives, reads, and reconciles working sessions — and how it keeps its own context clean doing so. Workers **and** the steward's own helpers both ride **omnigent**, which keeps the steward harness- and model-agnostic (not bound to Claude Code's `Task`/`SendMessage`). Source of truth: the installed package at `/Users/trippwickersham/.local/share/uv/tools/omnigent/`; local server at `127.0.0.1:6767`.

This skill is consumed by the `mm-steward-dev-flow` orchestrator (which routes intake sessions and reads the map for the roll-up) and by `mm-finish-session` (which archives a session the map identifies). The **session↔issue map is built (Phase 1.4)**: `scripts/session_map.py` is the pure classifier plus DuckDB persistence. The active comms *around* the map — spawning, driving, relaying sessions, mirroring edges to the board — is **> Built in Phase 3**; the `sys_session_*` lifecycle calls below are proven (design §12) but not yet wrapped in scripts, so do not invent flags for them.

## Primary constraint: obey the #102 cost contract on every poll

<primary-constraint>
**One `sys_session_list` pass per poll** (paginate via cursor); match on `title` + `created_by_session_id` **already in the cheap row**; **never loop `sys_session_get_info` across the list**. Call `get_info` only on the one session about to be acted on, or on the bounded `runner_online == true` handful. Keep the persisted map so routine routing needs **zero** list calls — `./session_map.py counts` reads it with no MCP calls at all. Put a `cost.cost_budget` / `user_daily_cost_budget` policy on the steward as a backstop.
</primary-constraint>

<rationale>
**Why:** the original hourly sweep died at session 68/102 by looping `get_info` over ~100 sessions — O(n²) context growth tripped the `$1` cost DENY before any real work. `workspace` and labels are **not** in the cheap list row (only `get_info` has them), which is exactly why looping `get_info` is tempting and exactly why it must never happen across the full list.

**Do instead:** page `sys_session_list` once → filter to `runner_online == true` (a handful, not ~100) → `get_info` *only those* to fill `workspace`. `created_by_session_id == steward` identifies the steward's own workers straight from the row; `title` classifies most others without `get_info`. Offline sessions are never `get_info`'d. Proven on the live set (Phase 1.4): one list pass over **137 sessions** (2 paginated calls), `get_info` on **exactly the 8 `runner_online` sessions** — no loop.
</rationale>

## Primary constraint: never interrogate an unmapped session

<unmapped-constraint>
The three-bucket reconciliation is **sticky but self-correcting** — the steward may revise a wrong guess, but **only from passive, out-of-band signals**: git commits / branch names / PRs on the worktree a session used that reference an issue (`#N`, `Closes #N`/`Part of #N`, which `mm-change` requires). If such a signal appears, it promotes that session's edge to **mapped** in the background. The classifier **never** calls `sys_session_message` / `get_history` / repeated `get_info` on an unmapped session to reclassify it.
</unmapped-constraint>

<rationale>
**Why:** interrogating a session to classify it is both a privacy violation and a direct cost-contract breach — it is the exact `get_info`/message loop that killed the original sweep. Reclassification rides the free git/`gh` state the hygiene sweep already reads, so untracked sessions stay untouched and query-free. The classifier is unit-tested to have **no code path** that emits a session-query action (`test_classifier_never_emits_a_session_query_action`); its only action on an unclassified session is a bounded, read-only `get_info` (default cap `max_examine=8`, `runner_online` first).
</rationale>

## Identity: run as `local` (decided)

This deployment is single-user (`OMNIGENT_USER_ID=local`); every session is owned by one identity, because omnigent's comms are owner-scoped:

- `sys_session_message` = **same-owner** peer messaging (no parentage change; you poll for the reply with `get_history`).
- `sys_session_send` = **child-of-caller** only.
- `get_info` / `get_history` / `list` = global reads within the per-user permission model.

<identity-rationale>
**Why `local`, not a separate actor:** a steward running as `local` can message and read *every* session, including the ones a human created and drives. A separate-identity steward could only drive sessions it created, not the human's. Unattended-safety comes from **cost/sandbox policies**, which are independent of identity, so there is no safety reason to split actors. (`permission_mode` governs approval prompts only, not comms.)
</identity-rationale>

## The session_map.py CLI (built)

`sys_session_list` / `sys_session_get_info` are **omnigent MCP tools the agent calls**, not a CLI — a plain script cannot reach them. So the gather and the classify are split: the **agent** does the MCP reads, and `scripts/session_map.py` is a **pure, offline** classifier + store.

```sh
# classify rows + git signals gathered by the agent, persist, print counts
./session_map.py classify --rows rows.json --git git.json
./session_map.py classify --rows rows.json --git git.json --dry-run   # preview, no persist
./session_map.py classify --rows rows.json --git git.json --json      # full Classification
./session_map.py counts                                               # read persisted map, ZERO MCP calls
```

The gather→classify steps the agent runs:

1. **Agent:** one `sys_session_list` pass (paginate via `page.next_cursor`); collect cheap rows (`conversation_id`, `title`, `status`, `runner_online`, `created_by_session_id`).
2. **Agent:** `get_info` only the `runner_online == true` handful to fill `workspace`; write rows to a JSON file (`SessionRow` shape).
3. **Agent (free, already-read git/gh state):** collect passive `GitSignal`s — `#N` / `Closes #N` / `Part of #N` from commits, branch names, PRs on each worktree — to a JSON file. Never query a session for this.
4. **Script:** `./session_map.py classify` loads prior buckets + edges, classifies (sticky), persists, and prints counts.

The classifier persists two **many-to-many** tables in the shared `~/.local/share/backlog-steward/flow.duckdb` — the **same** DuckDB file the `mm-flow-metrics` skill uses for `flow_event`, shared **by path, not by import**; `session_map.py` creates/alters only its own two tables (`session_bucket`, `session_edge`).

<reference file="references/comms.md" section="The session lifecycle verbs">
The full session lifecycle verbs and their costs, the three kinds of agent (standing session / ephemeral helpers / working sessions), the cost-contract proof on the live set, the many-to-many edge model, the three sticky buckets, the reconciliation rationale, and the title-edge confidence caveat. Read it before spawning or driving a session, or when debugging the map.
</reference>

## Running the tests

<test-command>
Standalone PEP-723 scripts, so the repo gate does not run them. From this skill's `scripts/` dir, mirror every inline dep (including `pytest`):

```sh
uv run --with pytest --with pydantic --with typer --with duckdb pytest
```

**23 tests pass here** (`test_session_map.py`). The measure layer's 16 tests live in the `mm-flow-metrics` skill (39 across the two skills).
</test-command>

## Reference index

| When you need to… | Read / invoke |
|---|---|
| Spawn / drive / read a session; the edge map; reconciliation | `references/comms.md` |
| Decide what to do with a routed session | the `mm-steward-dev-flow` skill |
| Archive a finished session | the `mm-finish-session` skill |
| The `flow_event` measure tables sharing this DuckDB file | the `mm-flow-metrics` skill |
