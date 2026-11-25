---
name: mm-flow-metrics
description: Use to measure how backlog work flows through make-magic's GitHub Projects v2 board — aging, cycle time, lead time, time-in-state-minus-blocked, throughput, and the cumulative-flow (CFD) chart — all derived at query time from an append-only event log, with NO running clock or counter. This is the steward's measure layer (board_read.py + flow_store.py + views.sql). Use when the steward orchestrator (mm-steward-dev-flow) needs the aging/bottleneck inputs for its governor, or when the user asks "how long has #N been in review", "is this card stalled", "what's the cycle time", "lead time", "throughput this week", "show the CFD", "read the board state", or "how long did that take". It reads the board read-only and never mutates it.
---

# Flow metrics: measure duration without keeping a clock

This skill is the steward's **measure layer** — how the steward knows how long things have taken **without keeping a clock**. Every duration (aging, cycle, lead, time-in-state-minus-blocked, CFD) is derived at *query time* from an append-only log of immutable timestamps. It is consumed by the `mm-steward-dev-flow` orchestrator's governor (the aging/bottleneck inputs) and by the roll-up; this skill does not decide what to do about a jam — it only reports the numbers.

The layer is **built (Phase 1)** as three `uv` PEP-723 standalone scripts in this skill's `scripts/` dir: `board_read.py` (read-only board + timeline fetch), `flow_store.py` (the append-only store + ingest + derived views), and `views.sql` (the interval views `flow_store.py` loads). They were built TDD-first.

## Primary constraint: no agent clock — append only, derive at query time

<primary-constraint>
The store **only appends timestamped facts**; it keeps **no running counter and no clock of its own**. Every duration is computed from the timestamps at query time by the interval SQL in `views.sql`. The "now" used for open intervals is the clock-free macro `steward_now()`, which tests and what-if queries override deterministically.

Appends are **idempotent by `raw_id`** — `upsert_events()` inserts `ON CONFLICT (raw_id) DO NOTHING`, so replay, poll, and (later) webhook all converge on the same event with no double-counting.
</primary-constraint>

<rationale>
**Why:** a counter can drift — a missed decrement, a double-count, a crash mid-update, and the number is silently wrong forever with no way to audit it. A timestamped fact cannot drift: the log is the dataset, every mart is a view over it, and re-deriving from the same timestamps always gives the same answer. This is the deliberate anti-brittleness goal. Keeping a clock in the agent would also mean the agent must be running continuously to stay correct; deriving at query time means a laptop-bound sweep that wakes, reads, and sleeps is exactly as correct as a daemon.

**Do instead:** to record that something happened, append a `flow_event` row with its timestamp and a stable `raw_id`. To answer "how long", query a view — never read or write a stored duration.
</rationale>

## Primary constraint: the store lives externally, never commingled

<store-constraint>
The store is a steward-owned **DuckDB** database at `~/.local/share/backlog-steward/flow.duckdb` (`flow_store.DEFAULT_DB`). It is held apart from two things it must **never** commingle with:

- **not** the repo tree — runtime state never lives in-repo; only `.claude/steward.yml` config does;
- **not** make-magic's product card lake (`MAKE_MAGIC_DATA_DIR` / `~/.local/share/make-magic`).
</store-constraint>

<rationale>
**Why:** runtime state in the repo tree would pollute git history, leak into every clone, and couple the steward's bookkeeping to branch state. Commingling with the product card lake would risk the steward's writes corrupting the data make-magic's deck tooling depends on, and vice-versa. The `mm-flow-sessions` skill's session-map cache coexists in the *same* external DuckDB file — each writer owns only its own tables, so one external store holds all steward runtime state and nothing else. `flow_store.connect()` creates the parent dir, the `flow_event` table, and every view on open, so the store is self-bootstrapping.
</rationale>

## The scripts (built CLIs)

Run from this skill's `scripts/` dir; both CLIs are `uv` PEP-723 scripts (shebang `uv run --script`). They read the board read-only and append to the external store — they never mutate the board.

```sh
./board_read.py summary                          # live board status + label counts (JSON)
./board_read.py rows                             # live normalized board rows (JSON)

./flow_store.py init                             # create the external store, table, and views
./flow_store.py view aging_wip                   # print one derived view (aging_wip, throughput, time_in_state, ...)
./flow_store.py sql "SELECT * FROM throughput"   # run a read-only query
```

The ingest wiring the agent/sweep runs between them (the store is pure over the agent's reads):

- `read_board()` → `events_from_status_poll()` for the **Status feed**;
- `fetch_timeline(numbers)` → `events_from_labels(timeline, node_map(rows))` for the **pause feed**;
- `upsert_events()` to append idempotently;
- `count_status_changed(timeline)` as the stay-on-poll canary.

<reference file="references/measuring.md" section="The one table (settled schema)">
The `flow_event` schema, the two feeds (label replay + Status poll) and why they differ, the Status-regression canary and why the steward stays on polling, the full set of derived interval views, the open config carried in `.claude/steward.yml`, and the test command. Read it when adding a view, debugging an ingest feed, or wiring the config.
</reference>

## Why poll Status rather than replay it

<status-caveat>
Projects v2 Status is a single-select custom field, and its timeline event `ProjectV2ItemStatusChangedEvent` has been **regressed since 2026-09-24** (silently unwritten for most moves). Replay cannot be trusted for Status today, so the Status feed is a **poll**: one `kind='status'` event per issue carrying its current state, keyed on `poll:<item_node_id>:<updatedAt>` so re-polling an unchanged issue is a no-op. The hardened canary `count_status_changed()` counts only real from→to transitions (non-empty `previousStatus`); while that count is 0, transitions are still unwritten and the steward **stays on polling**. When GitHub fixes the regression the count goes non-zero and Status can switch to drift-proof timeline replay with no schema change (> Built in Phase 4).
</status-caveat>

## Running the tests

<test-command>
These are standalone PEP-723 scripts, so the repo gate (`./scripts/check`) does **not** run them — the pipeline suite under `plugins/make-magic/pipeline/` is a separate scope. To run this skill's measure-layer tests, mirror *every* inline dependency (including `pytest` itself) on the `uv run` line, from this skill's `scripts/` dir:

```sh
uv run --with pytest --with pydantic --with typer --with duckdb pytest
```

**16 tests pass here** (`test_board_read.py` 5, `test_flow_store.py` 11). The session-map classifier's 23 tests live in the `mm-flow-sessions` skill (39 across the two skills). Dropping `--with pytest` fails with `Failed to spawn: pytest` — the deps are not auto-discovered from the script headers when you invoke `pytest` as the command.
</test-command>

## Reference index

| When you need to… | Read / invoke |
|---|---|
| The `flow_event` schema, feeds, views, canary, config | `references/measuring.md` |
| Decide what to do about a jam the metrics reveal | the `mm-steward-dev-flow` skill (the governor) |
| The session-map cache sharing this DuckDB file | the `mm-flow-sessions` skill |
