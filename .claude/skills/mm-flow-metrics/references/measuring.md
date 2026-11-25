# Measuring: the flow_event log (no agent clock)

How the steward knows how long things have taken **without keeping a clock**. Every duration — aging, cycle time, lead time, time-in-state-minus-blocked, CFD — is derived at *query time* from an append-only log of immutable timestamps. The steward maintains no running counter; that is the deliberate anti-brittleness goal (a counter can drift, a timestamp cannot).

The measure layer is **built (Phase 1)** as three `uv` PEP-723 standalone scripts bundled in this skill's `scripts/` dir: `board_read.py` (read-only board + timeline fetch), `flow_store.py` (the append-only store + ingest), and `views.sql` (the derived interval views `flow_store.py` loads). They were built TDD-first. The schema and SQL came verbatim from `research/metrics-data-layer.md`. The webhook feed and the `.claude/steward.yml` config wiring are *not* built — they stay marked below.

## Where it lives

A steward-owned **DuckDB** database at `~/.local/share/backlog-steward/flow.duckdb` (`flow_store.DEFAULT_DB`). This is **external** steward state, held apart from two things it must never commingle with:

- **not** the repo tree (runtime state never lives in-repo; only `.claude/steward.yml` config does);
- **not** make-magic's product card lake (`MAKE_MAGIC_DATA_DIR` / `~/.local/share/make-magic`).

`flow_store.connect()` creates the parent dir, the `flow_event` table, and every view on open, so the store is self-bootstrapping. The session-map cache (`session_bucket` + `session_edge`, owned by the `mm-flow-sessions` skill) lives in the **same** DuckDB file and coexists with `flow_event` — confirmed live, all tables in one DB, each writer owning only its own tables. The two skills share this DuckDB **by path, not by import**.

## The one table (settled schema)

```sql
CREATE TABLE IF NOT EXISTS flow_event (
  card_id BIGINT, item_node_id TEXT, event_ts TIMESTAMP,
  kind TEXT,          -- 'status'|'label'|'created'|'done'
  from_state TEXT, to_state TEXT,
  label TEXT, label_action TEXT,  -- 'added'|'removed'
  source TEXT,        -- 'webhook'|'timeline_replay'|'poll'
  raw_id TEXT PRIMARY KEY  -- delivery guid / event node id -> idempotent upsert
);
```

**Idempotent by `raw_id`.** `upsert_events()` inserts `ON CONFLICT (raw_id) DO NOTHING`, so replay, poll, and (later) webhook all *append timestamped facts* and converge safely on the same event — no double-counting, drift-proof. The table **is** the reusable dataset; every mart is a view over it.

## Two feeds (because GitHub exposes the two transition types unevenly)

Both feeds are driven by `board_read.py` reads and turned into `FlowEvent`s by `flow_store.py`. The agent (or the sweep) runs the reads; the store is pure over their output.

| Feed | What it captures | Built function | `source` | `raw_id` |
|---|---|---|---|---|
| **Label replay** | `blocked`/`needs-info` pause intervals | `events_from_labels()` over `board_read.fetch_timeline()` | `timeline_replay` | the timeline event node id (replayable, drift-proof) |
| **Status poll** | current funnel column per issue | `events_from_status_poll()` over `board_read.read_board()` | `poll` | `poll:<item_node_id>:<updatedAt>` |

- **Label replay** parses only `LabeledEvent`/`UnlabeledEvent` nodes whose label is in `PAUSE_LABELS = ("blocked", "needs-info")`; added → `label_action='added'`, removed → `'removed'`. Keying on the event node id makes replay idempotent regardless of how often the timeline is re-read.
- **Status poll** emits one `kind='status'` event per issue carrying the current `to_state`; because the `raw_id` folds in `updatedAt`, re-polling an *unchanged* issue is a no-op while a real status change (new `updatedAt`) appends a fresh fact. It loses sub-poll-interval A→B→C hops (acceptable at standup cadence). Rows with no Status or no stamp are skipped — nothing unreliable is appended.

<status-replay-caveat>
**Why poll Status instead of replaying it:** Projects v2 Status is a single-select custom field, and its timeline event `ProjectV2ItemStatusChangedEvent` has been **regressed since 2026-09-24** (silently unwritten for most moves — community #209484). Replay cannot be trusted for Status today. The only *exact* live source is the `projects_v2_item` webhook, but that needs an always-on receiver — **> Built in Phase 4** (the always-on Action). Polling fits v1's laptop-bound sweep.
</status-replay-caveat>

<regression-canary>
**The hardened Status-regression canary.** `flow_store.count_status_changed()` counts only `ProjectV2ItemStatusChangedEvent` nodes with a **non-empty `previousStatus`** — a real from→to move. This matters because raw presence is a false positive: the live board already returns these nodes, but every one observed on 2026-11-25 was an **initial board placement** (`previousStatus == ""`, `status == "Inbox"`) fired when the 70 issues were first added — not a transition. The honest transition count today is **0**, so transitions are still unwritten and the steward **stays on Status polling**. `board_read.py`'s timeline query already selects `previousStatus status` so the canary has its input. When the count goes non-zero (GitHub fixes the regression), Status can switch from poll to drift-proof timeline replay **with no change to the log schema** — `raw_id` becomes the event node id (> Built in Phase 4.3). Proven by `test_status_regression_canary_counts_only_real_transitions`.
</regression-canary>

## Derived views (interval SQL in `views.sql`, computed at query time)

`connect()` loads `views.sql` on every open, so these are always present. All are SQL over `flow_event`, never stored counters. Two helper macros underpin them:

- **`steward_now()`** — the clock-free reference "now" for open intervals and aging. It defaults to `CAST(now() AS TIMESTAMP)`, but tests and what-if queries override it with `CREATE OR REPLACE MACRO steward_now() AS TIMESTAMP '...'` so every duration is deterministic and the agent holds no clock of its own.
- **`blocked_overlap_secs(item, lo, hi)`** — seconds the merged pause intervals for one item overlap an arbitrary `[lo, hi)` window; the subtraction primitive the net-time views call.

The intervals and marts:

- **`status_interval`** — each `status` event opens an interval of that state running until the next status event for the same `item_node_id` (`LEAD`), or `steward_now()` if still open (`is_current`).
- **`blocked_interval`** — each `blocked`/`needs-info` `added` paired with the next event for that `(item, label)`, or `steward_now()` if the pause is still open.
- **`blocked_merged`** — a gaps-and-islands merge of pause intervals per item, so overlapping `blocked` + `needs-info` pauses subtract **once** (a union), not twice. Proven by `test_overlapping_blocked_and_needs_info_union_not_double_counted` (8d gross − 3d union = 5d net).
- **`time_in_state`** — per interval: `gross_secs` and `net_secs = gross − blocked_overlap_secs(...)`. This is the headline **time-in-state-minus-blocked**.
- **`aging_wip`** — the active seconds accrued in the current (still-open) non-Done state (the stall input for the governor in `mm-steward-dev-flow`).
- **`cycle_time`** — first entry into the active-work state → first Done, minus pauses.
- **`lead_time`** — first `created` event → first Done (calendar, no pause subtraction).
- **`throughput`** — count of items reaching Done per ISO week.
- **`cfd`** — day-clipped count of items in each state, for a cumulative-flow chart.

<open-config>
Carried from design §13, still **config in `.claude/steward.yml`, not code** (> Built in Phase 0 — the file does not exist yet; the places below are the v1 hardcoded defaults waiting to be wired):

- **First active-work state** for `cycle_time` — hardcoded `'In Progress'` in `views.sql` (change in ONE place: the `active_start` CTE). In Progress vs. Ready is the config decision.
- **Board IDs** (project id, Status field id, option/label ids, repo slug) — `board_read.load_board_ids()` reads them from `subagents/board-bootstrap/board-ids.json` under `~/.claude/plans/...` for now; they belong in `steward.yml`.
- **History-start watermark** vs. best-effort backfill: the poll gives correct *current* aging but no pre-capture history, and `lead_time` needs a `created` feed that is not yet ingested (`events_from_labels` emits nothing for `created`).
- **Item identity** across re-adds/transfers: the schema keeps both `card_id` (issue number) and `item_node_id`; the views key on `item_node_id`.
</open-config>

## Reading the board (read-only, never mutates)

`board_read.py` is the read side. `read_board()` paginates the Projects v2 board and `normalize_board()` flattens each item into a row `{issue_number, item_node_id, status, labels[], assignees[], linked_pr, linked_branch, updated_at}`; `node_map()` gives `issue_number → item_node_id` for joining timelines to board items; `summarize()` gives status + label counts. Every structured-variable query goes through **`gh api graphql --input -`** (piping `{"query": ..., "variables": {...}}` on stdin) — plain `-F` cannot carry a nested `variables` object such as a cursor or option id. The network call (`_graphql`) is kept separate from the pure mappers so shape-mapping is fixture-tested offline.

## CLIs (built)

Run from the skill's `scripts/` dir; both are `uv` PEP-723 scripts (shebang `uv run --script`).

```sh
./board_read.py summary                 # live board status + label counts (JSON)
./board_read.py rows                     # live normalized board rows (JSON)

./flow_store.py init                     # create the external store, table, and views
./flow_store.py view aging_wip           # print one derived view (aging_wip, throughput, time_in_state, ...)
./flow_store.py sql "SELECT * FROM throughput"   # run a read-only query
```

The ingest wiring the agent/sweep runs between them: `read_board()` → `events_from_status_poll()` for the Status feed; `fetch_timeline(numbers)` → `events_from_labels(timeline, node_map(rows))` for the pause feed; `upsert_events()` to append idempotently; `count_status_changed(timeline)` as the stay-on-poll canary.

<test-command>
**Running the tests is a real friction worth recording.** These are standalone PEP-723 scripts, so the repo gate (`./scripts/check`'s ruff/pyright/pytest) does **not** run them — the pipeline suite under `plugins/make-magic/pipeline/` is a separate scope. To run this skill's measure-layer tests, mirror *every* inline dependency (including `pytest` itself) on the `uv run` line, from the `scripts/` dir:

```sh
uv run --with pytest --with pydantic --with typer --with duckdb pytest
```

**16 tests pass in this skill** (`test_board_read.py` 5, `test_flow_store.py` 11). The session-map classifier's 23 tests (`test_session_map.py`) moved with their script to the `mm-flow-sessions` skill — 39 across the two skills. Dropping `--with pytest` fails with `Failed to spawn: pytest` — the deps are not auto-discovered from the script headers when you invoke `pytest` as the command.
</test-command>

## Validation reference (not a dependency)

`github-community-projects/issue-metrics` computes Time-in-Label from the same `LabeledEvent`/`UnlabeledEvent` feed. It **proves** the blocked-pause primitive is computable from label events with no clock — use it to validate the subtraction math. It is a *reference*, not the layer: it measures labels (not v2 Status), does no pause-subtraction, and has no durable store.
