#!/usr/bin/env -S uv run --python 3.12 --with duckdb --with pydantic --with typer --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "duckdb",
#     "pydantic",
#     "typer",
# ]
# [tool.uv]
# exclude-newer = "2026-11-25T00:00:00Z"
# ///
"""
Backlog Steward — append-only `flow_event` store + derived interval views.

NO AGENT CLOCK. This module only *appends timestamped facts* (idempotent by
`raw_id`) and *derives* every duration at query time from the views in
`views.sql`. The store lives EXTERNALLY at
`~/.local/share/backlog-steward/flow.duckdb` — never in the repo tree and
never in make-magic's product lake (`MAKE_MAGIC_DATA_DIR`).

Two ingest feeds (GitHub exposes the two transition types unevenly):
  * label pause  — GraphQL timeline replay of LabeledEvent/UnlabeledEvent for
                   `blocked`/`needs-info` (robust, replayable, idempotent).
  * Status poll  — snapshot each issue's current Status + updatedAt as a
                   `status` event (poll-only for v1; the
                   `ProjectV2ItemStatusChangedEvent` timeline is regressed —
                   see `count_status_changed` canary).

Usage:
    ./flow_store.py init                      # create the external store
    ./flow_store.py view aging_wip            # print a derived view
    ./flow_store.py sql "SELECT * FROM throughput"

Maintenance:
    uv add --script flow_store.py 'duckdb'
    uvx ruff check flow_store.py
    uvx ruff format flow_store.py

References:
    - PEP 723: https://peps.python.org/pep-0723/
    - research/metrics-data-layer.md (schema + interval SQL, used verbatim)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import typer
from pydantic import BaseModel

DEFAULT_DB = Path.home() / ".local" / "share" / "backlog-steward" / "flow.duckdb"
_VIEWS_SQL = Path(__file__).with_name("views.sql")

# Labels that pause the aging / time-in-state clock (design §2).
PAUSE_LABELS = ("blocked", "needs-info")

SCHEMA = """
CREATE TABLE IF NOT EXISTS flow_event (
  card_id BIGINT,
  item_node_id TEXT,
  event_ts TIMESTAMP,
  kind TEXT,          -- 'status'|'label'|'created'|'done'
  from_state TEXT,
  to_state TEXT,
  label TEXT,
  label_action TEXT,  -- 'added'|'removed'
  source TEXT,        -- 'webhook'|'timeline_replay'|'poll'
  raw_id TEXT PRIMARY KEY  -- delivery guid / event node id -> idempotent upsert
);
"""

_COLS = (
    "card_id",
    "item_node_id",
    "event_ts",
    "kind",
    "from_state",
    "to_state",
    "label",
    "label_action",
    "source",
    "raw_id",
)


class FlowEvent(BaseModel):
    """One immutable, timestamped fact. `raw_id` makes upsert idempotent."""

    raw_id: str
    kind: str  # 'status' | 'label' | 'created' | 'done'
    event_ts: datetime
    item_node_id: str
    card_id: int | None = None
    from_state: str | None = None
    to_state: str | None = None
    label: str | None = None
    label_action: str | None = None  # 'added' | 'removed'
    source: str | None = None  # 'webhook' | 'timeline_replay' | 'poll'

    def row(self) -> tuple[Any, ...]:
        return (
            self.card_id,
            self.item_node_id,
            self.event_ts,
            self.kind,
            self.from_state,
            self.to_state,
            self.label,
            self.label_action,
            self.source,
            self.raw_id,
        )


def _parse_ts(s: str) -> datetime:
    """ISO8601 (GitHub '...Z') -> naive UTC datetime for a TIMESTAMP column."""
    dt = datetime.fromisoformat(s)  # py3.11+ parses the trailing 'Z'
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def connect(db_path: str | Path = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    """Open (creating dir + table + views) the external flow store."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(SCHEMA)
    con.execute(_VIEWS_SQL.read_text())
    return con


def upsert_events(con: duckdb.DuckDBPyConnection, events: list[FlowEvent]) -> int:
    """Idempotent insert keyed by raw_id. Returns count attempted."""
    if not events:
        return 0
    placeholders = ", ".join(["?"] * len(_COLS))
    sql = (
        f"INSERT INTO flow_event ({', '.join(_COLS)}) VALUES ({placeholders}) "
        "ON CONFLICT (raw_id) DO NOTHING"
    )
    con.executemany(sql, [e.row() for e in events])
    return len(events)


def events_from_status_poll(board_rows: list[dict[str, Any]]) -> list[FlowEvent]:
    """Snapshot current Status per issue as a `status` event.

    raw_id = poll:<item>:<updatedAt> so re-polling an unchanged issue is a
    no-op, while a status change (new updatedAt) appends a new fact.
    """
    out: list[FlowEvent] = []
    for r in board_rows:
        status = r.get("status")
        updated = r.get("updated_at")
        item = r.get("item_node_id")
        if not status or not updated or not item:
            continue  # unset Status / missing stamp: nothing reliable to append
        out.append(
            FlowEvent(
                raw_id=f"poll:{item}:{updated}",
                kind="status",
                event_ts=_parse_ts(updated),
                item_node_id=item,
                card_id=r.get("issue_number"),
                to_state=status,
                source="poll",
            )
        )
    return out


def events_from_labels(
    timeline: dict[str, list[dict[str, Any]]],
    item_node_by_number: dict[int, str] | None = None,
) -> list[FlowEvent]:
    """Parse LabeledEvent/UnlabeledEvent for pause labels into label events.

    `timeline` maps issue-number (str) -> list of timeline nodes. raw_id = the
    event node id, so replay is idempotent and drift-proof.
    """
    item_node_by_number = item_node_by_number or {}
    out: list[FlowEvent] = []
    for num_str, nodes in timeline.items():
        num = int(num_str)
        item = item_node_by_number.get(num, num_str)
        for node in nodes:
            typ = node.get("__typename")
            if typ == "LabeledEvent":
                action = "added"
            elif typ == "UnlabeledEvent":
                action = "removed"
            else:
                continue
            label = (node.get("label") or {}).get("name")
            if label not in PAUSE_LABELS:
                continue
            out.append(
                FlowEvent(
                    raw_id=node["id"],
                    kind="label",
                    event_ts=_parse_ts(node["createdAt"]),
                    item_node_id=item,
                    card_id=num,
                    label=label,
                    label_action=action,
                    source="timeline_replay",
                )
            )
    return out


def count_status_changed(timeline: dict[str, list[dict[str, Any]]]) -> int:
    """Status-regression canary: count REAL status TRANSITIONS in the timeline.

    Only ProjectV2ItemStatusChangedEvent nodes with a non-empty
    `previousStatus` count. Initial board-placement also fires this event but
    with `previousStatus == ""` (observed live 2026-11-25) — counting raw
    presence is a false positive for "regression resolved".

    Zero today => transitions are still unwritten (community #209484, regressed
    since 2026-09-24); stay on Status polling. When this goes non-zero, Status
    can switch from poll to drift-proof timeline replay with no schema change
    (design §4, Phase 4.3).
    """
    return sum(
        1
        for nodes in timeline.values()
        for n in nodes
        if n.get("__typename") == "ProjectV2ItemStatusChangedEvent"
        and (n.get("previousStatus") or "")
    )


# --------------------------------------------------------------------------- CLI
app = typer.Typer(add_completion=False, help="Backlog Steward flow-event store.")


@app.command()
def init(db: str = str(DEFAULT_DB)) -> None:
    """Create the external store, table, and views."""
    con = connect(db)
    con.close()
    typer.echo(f"initialized {db}")


@app.command()
def view(name: str, db: str = str(DEFAULT_DB)) -> None:
    """Print a derived view (e.g. aging_wip, throughput, time_in_state)."""
    con = connect(db)
    try:
        con.sql(f"SELECT * FROM {name}").show()
    finally:
        con.close()


@app.command()
def sql(query: str, db: str = str(DEFAULT_DB)) -> None:
    """Run a read-only query and print the result."""
    con = connect(db)
    try:
        con.sql(query).show()
    finally:
        con.close()


if __name__ == "__main__":
    app()
