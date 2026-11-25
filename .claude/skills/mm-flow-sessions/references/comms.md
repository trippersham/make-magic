# Comms: the omnigent session substrate

How the steward spawns, drives, reads, and reconciles working sessions — and how it keeps its own context clean doing so. Workers **and** the steward's own helpers both ride **omnigent**, which keeps the skill harness- and model-agnostic (not bound to Claude Code's `Task`/`SendMessage`). Source of truth: the installed package at `/Users/trippwickersham/.local/share/uv/tools/omnigent/`; local server at `127.0.0.1:6767`.

The **session↔issue map is built (Phase 1.4)**: `scripts/session_map.py` is the pure classifier plus DuckDB persistence (the three sticky buckets, the many-to-many edges, the cost contract encoded). What is **> Built in Phase 3** is the active comms *around* that map — spawning, driving, and relaying sessions (`sys_session_create` / `sys_session_message` / `sys_session_send`), and the archive action (the archive shim lives in the `mm-finish-session` skill). The `sys_session_*` lifecycle calls below are proven (design §12) but not yet wrapped in scripts; do not invent flags for them.

## Identity: run as `local` (decided)

This deployment is single-user (`OMNIGENT_USER_ID=local`); every session is owned by one identity. That matters because omnigent's comms are owner-scoped:

- `sys_session_message` = **same-owner** peer messaging (no parentage change; you poll for the reply with `get_history`).
- `sys_session_send` = **child-of-caller** only.
- `get_info` / `get_history` / `list` = global reads within the per-user permission model.

<identity-rationale>
**Why `local`, not a separate actor:** a steward running as `local` can message and read *every* session, including the ones a human created and drives. A separate-identity steward would forfeit exactly this — it could only drive sessions it created, not the human's. Unattended-safety comes from **cost/sandbox policies**, which are independent of identity, so there is no safety reason to split actors. (`permission_mode` governs approval prompts only, not comms.)
</identity-rationale>

## Three kinds of agent

1. **The steward session** — long-lived, interactive, the human's one-stop desk. Its user-facing context **never** fills with session chatter.
2. **Steward sub-agents** — ephemeral helpers the steward spawns to do its *own* work (check-ins, override-lane execution, hygiene, message relay) and hand back a one-line summary — the **context firewall**. Spawned via `sys_session_create placement="child"` + `sys_session_send`, on a **cheap, fast model** (their job is routing and summarizing, not deep reasoning).
3. **Working sessions** — top-level omnigent sessions (`placement="same_host"`) where the real dev work happens. Durable, enumerable, titled, survive restarts. **Not** sub-agents of the steward.

## The session lifecycle verbs

| Verb | Call | Cost | Note |
|---|---|---|---|
| **Create** | `sys_session_create(agent_id, placement, title, message, model, reasoning_effort)` | starts a turn | `message` (1–8000 chars) seeds the first turn; needs the `spawn:true` spec |
| **Drive (peer)** | `sys_session_message` | starts a turn | same-owner; poll `get_history` for the reply |
| **Drive (child)** | `sys_session_send` | starts a turn | children of the caller only |
| **Read tail** | `sys_session_get_history` | 1 REST call | the cheap read |
| **Read detail** | `sys_session_get_info` | ~3 REST calls | workspace/status/model/parent — **never loop this** |
| **Rename** | `sys_session_rename` | 1–2 calls | top-level only; built to carry issue numbers in the title |
| **Close** | `sys_session_close` | — | **tombstone only, tree-scoped** — does *not* stop the runner or hide the session |

**Archive ≠ close.** Real cleanup is an owner-only REST `PATCH` — it lives in the `mm-finish-session` skill, which archives a session the map here identifies.

## The cost contract (the #102 fix, non-negotiable)

<cost-contract>
One `sys_session_list` pass per poll (paginate via cursor); match on `title` + `created_by_session_id` **already in the row**; **never loop `sys_session_get_info` across the list**. Keep the persisted map so routine routing needs **zero** list calls. Call `get_info` only on the one session about to be acted on. Put a `cost.cost_budget` / `user_daily_cost_budget` policy on the steward as a backstop.

**Why:** the original hourly sweep died at session 68/102 by looping `get_info` over ~100 sessions — O(n²) context growth tripped the `$1` cost DENY before any real work. `workspace` and labels are **not** in the cheap list row (only `get_info` has them), which is exactly why looping `get_info` is tempting and exactly why it must never happen across the full list.
</cost-contract>

**Cheap discovery that respects the contract:** page `sys_session_list` → filter to `runner_online == true` (a handful, not ~100) → `get_info` *only those*. `created_by_session_id == steward` identifies the steward's own workers straight from the row; `title` classifies most others without `get_info`. Offline sessions are never `get_info`'d.

<cost-contract-proof>
**Proven on the real set (Phase 1.4).** One list pass over the live session set (**137 sessions**, 2 paginated `sys_session_list` calls, `has_more` → false) fed the classifier; `get_info` was called on **exactly the 8 `runner_online == true` sessions** to read `workspace` (which the cheap row lacks) — **no `get_info` loop across the 137**. That loop is the thing that killed the original sweep at session 68/102. The classifier structurally *cannot* breach the contract: its only action on an unclassified session is a bounded, read-only `get_info` (default cap `max_examine=8`, `runner_online` first), and it has **no code path** that emits `sys_session_message` / `get_history` / `send` (unit-tested: `test_classifier_never_emits_a_session_query_action`). Routine routing reads the persisted map with **zero** list/MCP calls (`./session_map.py counts`).
</cost-contract-proof>

## The session↔issue map (many-to-many)

The relationship is **many-to-many in practice**: one session often touches several issues; one issue may span several sessions; sessions carry dependencies between them. Model it as a set of **(session, issue) edges**, not a 1:1 field.

- **Route on the immutable `conversation_id`** (distinct from the inner harness's own session id — the agent inside may not know its omnigent id; the steward always holds it).
- **The edge map is local-only — no bespoke GitHub board field** (decided: "less is more"). The `conversation_id` is a single-machine omnigent identifier, so mirroring it to a `Sessions` card field or a per-issue comment buys little and adds opaque noise. The durable, portable, human-visible binding is GitHub's **native PR/branch ↔ issue dev-link** (`Closes #N` / `Part of #N`, which `mm-change` requires). The `session_edge` table is a **rebuildable cache** in the steward's external store (`~/.local/share/backlog-steward/flow.duckdb`) — *rebuilt from* those native dev-link signals (plus lower-confidence session titles), not a hand-maintained ledger that can drift, so a cache wipe is recoverable from GitHub + git state.

### The gather→classify split (built)

`sys_session_list` / `sys_session_get_info` are **omnigent MCP tools the agent calls**, not a CLI — a plain script cannot reach them. So the gather and the classify are split: the **agent** does the MCP reads, and `scripts/session_map.py` is a **pure, offline** classifier + store. This keeps the cost contract enforceable in one place (the agent owns every MCP call; the classifier can only ever *request* a bounded `get_info`).

1. **Agent:** one `sys_session_list` pass (paginate via `page.next_cursor`); collect the cheap rows (`conversation_id`, `title`, `status`, `runner_online`, `created_by_session_id`).
2. **Agent:** `get_info` only the `runner_online == true` handful to fill `workspace`; write rows to a JSON file (`SessionRow` shape).
3. **Agent (free, already-read git/gh state):** collect passive `GitSignal`s — `#N` / `Closes #N` / `Part of #N` from commits, branch names, and PRs on each worktree — to a JSON file. Never query a session for this.
4. **Script:** `./session_map.py classify --rows rows.json --git git.json` loads prior buckets + edges from the DuckDB store, classifies (sticky), persists, and prints counts. `--dry-run` previews; `--json` emits the full `Classification`. `./session_map.py counts` reads the persisted map with zero MCP calls.

The classifier persists two **many-to-many** tables in the shared `~/.local/share/backlog-steward/flow.duckdb` (coexisting with the `mm-flow-metrics` skill's `flow_event` + views — the two skills share this DuckDB **by path, not by import**; `session_map.py` creates/alters only its own two tables):

- **`session_bucket`** — `conversation_id` (PK) → `bucket`, `workspace`, `title`, `note`, `first_classified`, `last_seen`. One sticky bucket per session.
- **`session_edge`** — `(conversation_id, issue_number, source)` (composite PK) → `first_seen`, `last_seen`. The many-to-many edges; `source` records provenance (`title`, `git-commit`, `branch`, `pr`, `steward-seed`). Live proof: issue 83 → 3 sessions, and one session → issues {83, 2}.

### Three-bucket reconciliation (sticky, self-correcting)

Each sweep sorts repo-local sessions into three **sticky** buckets keyed by `conversation_id`:

| Bucket | Meaning |
|---|---|
| **mapped** | linked to one or more issues |
| **expressly-unmapped** | legitimate non-issue work — ad-hoc skill runs done in-repo by convention, exploration, a design session |
| **not-ours** | workspace outside the repo |

Only sessions in **none** of the buckets are examined (bounded N per run, incremental cursor), so the map converges without re-scanning the world.

<reconciliation-rationale>
**Why "never interrogate an unmapped session":** express-unmapped is sticky but self-correcting — the steward may quietly revise a wrong guess, but **only from passive, out-of-band signals**: git commits / branch names / PRs on the worktree a session used that reference an issue (`#N`, `Closes #N`/`Part of #N`, which `mm-change` requires). If such a signal appears, it promotes that session's edge to **mapped** in the background. It **never** calls `sys_session_message` / `get_history` / repeated `get_info` on an unmapped session to reclassify it — untracked sessions stay untouched and query-free, and reclassification rides the free git/`gh` state the hygiene sweep already reads. Interrogating sessions to classify them is both a privacy violation and a direct cost-contract breach.
</reconciliation-rationale>

The roll-up still notes "N new ad-hoc sessions auto-unmapped" so the human can correct a miss directly.

<edge-confidence-caveat>
**IMPORTANT — title-derived edges are lower-confidence than git/PR-derived edges.** The classifier mines `#N` from the session `title` (`extract_issue_refs`), and a title like `event-decomposition probe (#83 smell#2)` yields issues `{83, 2}` — `smell#2` matches `#\d+`, so the `#2` edge is a **spurious false positive**. This is acceptable for a *rebuildable cache* (GitHub is the source of truth and will correct it), but Phase-3 intake/comms **routing must treat `source='title'` edges as provisional** and prefer corroboration from `git-commit` / `branch` / `pr` edges (which ride the `#N` / `Closes #N` / `Part of #N` that `mm-change` requires on real commits, branches, and PRs). Known convergence follow-ups, deferred, not yet built:

- a **title-pattern allowlist** so the ~25 identical `make-magic stack hygiene sweep (OBSERVE)` scheduled-task sessions (one shared title, no `#N`, no cheap-row workspace) auto-classify to a non-issue bucket instead of each burning a bounded `get_info` slot;
- an **`examined_at` cursor** so offline sessions already examined in a prior sweep are not re-queued — without it the long offline tail (workspace unknown + no passive signal) converges over many sweeps, not one.
</edge-confidence-caveat>

## Do / do not

| Do | Do not |
|---|---|
| One list pass per poll; keep the persisted map | Loop `get_info` across the session list |
| Run helpers as `placement="child"` on a cheap/fast model | Do deep reasoning in a helper, or let helper chatter into the standing session |
| Reclassify only from passive git/`gh`/PR signals | Message or read an unmapped session to classify it |
| Create and own the session on the override lane | Open or drive the human's session on the normal path (only *message* it for check-ins) |
