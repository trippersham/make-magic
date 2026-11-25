#!/usr/bin/env -S uv run --python 3.12 --with pytest --with duckdb --with pydantic --with typer --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pytest",
#     "duckdb",
#     "pydantic",
#     "typer",
# ]
# [tool.uv]
# exclude-newer = "2026-11-25T00:00:00Z"
# ///
"""
Offline, deterministic tests for the backlog-steward flow_event store and
its derived interval views.

The highest-value proof here is **time-in-state MINUS blocked**: a status
interval's active duration must subtract the union of overlapping
`blocked`/`needs-info` label intervals. Every test uses synthetic rows and a
pinned `steward_now()` macro so no clock and no network enter the suite.

Usage:
    uv run .../test_flow_store.py            # via the shebang / pytest
    uv run --with pytest --with duckdb --with pydantic --with typer \
        pytest .claude/skills/mm-flow-metrics/scripts/test_flow_store.py

Maintenance:
    uvx ruff check test_flow_store.py
    uvx ruff format test_flow_store.py

References:
    - PEP 723: https://peps.python.org/pep-0723/
    - research/metrics-data-layer.md (schema + interval SQL)
"""

from __future__ import annotations

from datetime import datetime

import pytest

import flow_store as fs

DAY = 86400.0

# A frozen "now" so open-interval / aging math is deterministic offline.
FROZEN_NOW = "2026-01-10 00:00:00"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


@pytest.fixture
def con():
    """In-memory store with views loaded and a pinned steward_now()."""
    c = fs.connect(":memory:")
    c.execute(f"CREATE OR REPLACE MACRO steward_now() AS TIMESTAMP '{FROZEN_NOW}'")
    yield c
    c.close()


def _status(
    item: str, ts: str, to_state: str, raw_id: str, card_id: int = 1
) -> fs.FlowEvent:
    return fs.FlowEvent(
        card_id=card_id,
        item_node_id=item,
        event_ts=_ts(ts),
        kind="status",
        to_state=to_state,
        source="poll",
        raw_id=raw_id,
    )


def _label(item: str, ts: str, label: str, action: str, raw_id: str) -> fs.FlowEvent:
    return fs.FlowEvent(
        card_id=1,
        item_node_id=item,
        event_ts=_ts(ts),
        kind="label",
        label=label,
        label_action=action,
        source="timeline_replay",
        raw_id=raw_id,
    )


def _created(item: str, ts: str, raw_id: str) -> fs.FlowEvent:
    return fs.FlowEvent(
        card_id=1,
        item_node_id=item,
        event_ts=_ts(ts),
        kind="created",
        source="timeline_replay",
        raw_id=raw_id,
    )


def test_idempotent_upsert_same_raw_id_is_one_row(con):
    ev = _status("I1", "2026-01-02 00:00:00", "In Progress", raw_id="poll:I1:1")
    fs.upsert_events(con, [ev])
    fs.upsert_events(con, [ev])  # replay / re-poll
    n = con.execute("SELECT COUNT(*) FROM flow_event").fetchone()[0]
    assert n == 1


def test_time_in_state_subtracts_blocked_overlap(con):
    # In Progress Jan2 -> Done Jan6 = 4 days gross; blocked Jan3->Jan4 = 1 day.
    fs.upsert_events(
        con,
        [
            _created("I1", "2026-01-01 00:00:00", "c1"),
            _status("I1", "2026-01-02 00:00:00", "In Progress", "s1"),
            _label("I1", "2026-01-03 00:00:00", "blocked", "added", "l1"),
            _label("I1", "2026-01-04 00:00:00", "blocked", "removed", "l2"),
            _status("I1", "2026-01-06 00:00:00", "Done", "s2"),
        ],
    )
    row = con.execute(
        "SELECT gross_secs, net_secs FROM time_in_state "
        "WHERE item_node_id='I1' AND state='In Progress'"
    ).fetchone()
    gross, net = row
    assert gross == pytest.approx(4 * DAY)
    assert net == pytest.approx(3 * DAY)


def test_overlapping_blocked_and_needs_info_union_not_double_counted(con):
    # In Progress Jan2 -> Done Jan10; blocked Jan3-Jan5 overlaps needs-info Jan4-Jan6.
    # Union of the pause = Jan3 -> Jan6 = 3 days (NOT 2+2=4).
    fs.upsert_events(
        con,
        [
            _status("I2", "2026-01-02 00:00:00", "In Progress", "s1"),
            _label("I2", "2026-01-03 00:00:00", "blocked", "added", "l1"),
            _label("I2", "2026-01-05 00:00:00", "blocked", "removed", "l2"),
            _label("I2", "2026-01-04 00:00:00", "needs-info", "added", "l3"),
            _label("I2", "2026-01-06 00:00:00", "needs-info", "removed", "l4"),
            _status("I2", "2026-01-10 00:00:00", "Done", "s2"),
        ],
    )
    row = con.execute(
        "SELECT gross_secs, net_secs FROM time_in_state "
        "WHERE item_node_id='I2' AND state='In Progress'"
    ).fetchone()
    gross, net = row
    assert gross == pytest.approx(8 * DAY)
    assert net == pytest.approx(5 * DAY)  # 8 - 3 (union), not 8 - 4


def test_aging_wip_is_open_interval_active_secs(con):
    # Open In Progress since Jan5, frozen now Jan10 => 5 days gross.
    # blocked Jan6->Jan7 => 1 day; active aging = 4 days.
    fs.upsert_events(
        con,
        [
            _status("I3", "2026-01-05 00:00:00", "In Progress", "s1"),
            _label("I3", "2026-01-06 00:00:00", "blocked", "added", "l1"),
            _label("I3", "2026-01-07 00:00:00", "blocked", "removed", "l2"),
        ],
    )
    row = con.execute(
        "SELECT gross_secs, net_secs FROM aging_wip WHERE item_node_id='I3'"
    ).fetchone()
    gross, net = row
    assert gross == pytest.approx(5 * DAY)
    assert net == pytest.approx(4 * DAY)


def test_aging_wip_excludes_done_items(con):
    fs.upsert_events(
        con,
        [
            _status("I4", "2026-01-02 00:00:00", "In Progress", "s1"),
            _status("I4", "2026-01-03 00:00:00", "Done", "s2"),
        ],
    )
    rows = con.execute("SELECT * FROM aging_wip WHERE item_node_id='I4'").fetchall()
    assert rows == []


def test_cycle_time_first_active_to_done_minus_blocked(con):
    fs.upsert_events(
        con,
        [
            _created("I5", "2026-01-01 00:00:00", "c1"),
            _status("I5", "2026-01-02 00:00:00", "In Progress", "s1"),
            _label("I5", "2026-01-03 00:00:00", "blocked", "added", "l1"),
            _label("I5", "2026-01-04 00:00:00", "blocked", "removed", "l2"),
            _status("I5", "2026-01-06 00:00:00", "Done", "s2"),
        ],
    )
    row = con.execute(
        "SELECT gross_secs, net_secs FROM cycle_time WHERE item_node_id='I5'"
    ).fetchone()
    gross, net = row
    assert gross == pytest.approx(4 * DAY)
    assert net == pytest.approx(3 * DAY)


def test_lead_time_done_minus_created(con):
    fs.upsert_events(
        con,
        [
            _created("I6", "2026-01-01 00:00:00", "c1"),
            _status("I6", "2026-01-02 00:00:00", "In Progress", "s1"),
            _status("I6", "2026-01-06 00:00:00", "Done", "s2"),
        ],
    )
    (lead,) = con.execute(
        "SELECT lead_secs FROM lead_time WHERE item_node_id='I6'"
    ).fetchone()
    assert lead == pytest.approx(5 * DAY)


def test_throughput_counts_done_per_week(con):
    fs.upsert_events(
        con,
        [
            _status("A", "2026-01-05 00:00:00", "Done", "s1"),  # week of Jan5 (Mon)
            _status("B", "2026-01-06 00:00:00", "Done", "s2"),
            _status("C", "2026-01-13 00:00:00", "Done", "s3"),  # next week
        ],
    )
    rows = con.execute("SELECT done_count FROM throughput ORDER BY week").fetchall()
    assert [r[0] for r in rows] == [2, 1]


def test_status_regression_canary_counts_only_real_transitions():
    # The canary must count only REAL status transitions (non-empty
    # previousStatus). Initial board-placement fires a
    # ProjectV2ItemStatusChangedEvent with previousStatus="" — a false
    # positive for "regression resolved" (observed live 2026-11-25).
    empty = {"123": []}
    assert fs.count_status_changed(empty) == 0
    timeline = {
        "123": [
            # initial placement: Inbox from nothing -> NOT a transition
            {
                "__typename": "ProjectV2ItemStatusChangedEvent",
                "createdAt": "2026-01-01T00:00:00Z",
                "previousStatus": "",
                "status": "Inbox",
            },
            {"__typename": "LabeledEvent", "createdAt": "2026-01-02T00:00:00Z"},
        ],
        "124": [
            # a real move: Inbox -> In Progress
            {
                "__typename": "ProjectV2ItemStatusChangedEvent",
                "createdAt": "2026-01-03T00:00:00Z",
                "previousStatus": "Inbox",
                "status": "In Progress",
            },
        ],
    }
    assert fs.count_status_changed(timeline) == 1


def test_events_from_status_poll_is_idempotent_on_unchanged_updatedat(con):
    board_rows = [
        {
            "item_node_id": "N1",
            "issue_number": 10,
            "status": "Inbox",
            "updated_at": "2026-01-02T00:00:00Z",
        },
    ]
    evs1 = fs.events_from_status_poll(board_rows)
    evs2 = fs.events_from_status_poll(board_rows)
    fs.upsert_events(con, evs1)
    fs.upsert_events(con, evs2)  # same updatedAt => same raw_id => no new row
    n = con.execute("SELECT COUNT(*) FROM flow_event WHERE kind='status'").fetchone()[0]
    assert n == 1


def test_events_from_labels_parses_blocked_only(con):
    timeline = {
        "10": [
            {
                "__typename": "LabeledEvent",
                "id": "LE1",
                "createdAt": "2026-01-03T00:00:00Z",
                "label": {"name": "blocked"},
            },
            {
                "__typename": "UnlabeledEvent",
                "id": "UE1",
                "createdAt": "2026-01-04T00:00:00Z",
                "label": {"name": "blocked"},
            },
            {
                "__typename": "LabeledEvent",
                "id": "LE2",
                "createdAt": "2026-01-05T00:00:00Z",
                "label": {"name": "enhancement"},  # not a pause label -> ignored
            },
        ]
    }
    evs = fs.events_from_labels(timeline, item_node_by_number={10: "N10"})
    assert len(evs) == 2
    assert {e.label_action for e in evs} == {"added", "removed"}
    assert all(e.label == "blocked" for e in evs)
    assert all(e.item_node_id == "N10" for e in evs)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
