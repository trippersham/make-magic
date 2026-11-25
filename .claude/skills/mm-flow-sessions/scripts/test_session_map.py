#!/usr/bin/env -S uv run --python 3.12 --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pytest",
#     "pydantic",
#     "duckdb",
#     "typer",
# ]
# [tool.uv]
# exclude-newer = "2026-11-25T00:00:00Z"
# ///
"""Offline, deterministic tests for the session-map classifier + persistence.

These tests are the TDD unit for ``session_map.py``. They never call omnigent
MCP tools; they feed synthetic cheap-rows + synthetic git signals through the
pure classifier and assert the 3-bucket model, many-to-many edges, stickiness,
passive promotion, the never-query invariant, and a DuckDB round-trip that
coexists with the other agent's ``flow_event`` table.

Usage:
    ./test_session_map.py
    uv run test_session_map.py

Maintenance:
    uv add --script test_session_map.py 'package-name'
    uvx ruff format test_session_map.py
    uvx ruff check test_session_map.py

References:
    - PEP 723: https://peps.python.org/pep-0723/
    - uv scripts: https://docs.astral.sh/uv/guides/scripts/
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session_map as sm

NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
REPO_ROOTS = (
    "/Users/trippwickersham/Code/magic-tools",
    "/Users/trippwickersham/Code/magic-tools-worktrees",
)


def row(conv: str, **kw: object) -> sm.SessionRow:
    return sm.SessionRow(conversation_id=conv, **kw)  # type: ignore[arg-type]


def classify(rows, git_signals=(), prior_buckets=(), prior_edges=(), **kw):
    kw.setdefault("repo_roots", REPO_ROOTS)
    kw.setdefault("steward_id", "steward-conv")
    return sm.classify(
        list(rows),
        list(git_signals),
        list(prior_buckets),
        list(prior_edges),
        **kw,
    )


def bucket_of(result: sm.Classification, conv: str) -> str | None:
    for b in result.buckets:
        if b.conversation_id == conv:
            return b.bucket
    return None


# --- extract_issue_refs ------------------------------------------------------


def test_extract_bare_hash():
    assert sm.extract_issue_refs("work on #45") == frozenset({45})


def test_extract_closes_and_part_of():
    text = "Closes #12 and Part of #7"
    assert sm.extract_issue_refs(text) == frozenset({12, 7})


def test_extract_multiple_and_dedupe():
    assert sm.extract_issue_refs("#3 #3 #10") == frozenset({3, 10})


def test_extract_none_and_empty():
    assert sm.extract_issue_refs(None) == frozenset()
    assert sm.extract_issue_refs("no issues here") == frozenset()


# --- 3-bucket assignment -----------------------------------------------------


def test_mapped_from_title_issue():
    r = row("c1", title="feat/thing (#45)", workspace=f"{REPO_ROOTS[1]}/wt-a")
    result = classify([r], now=NOW)
    assert bucket_of(result, "c1") == sm.MAPPED
    assert any(e.issue_number == 45 for e in result.edges)


def test_not_ours_from_outside_workspace():
    r = row("c2", title="random project", workspace="/Users/trippwickersham/other/repo")
    result = classify([r], now=NOW)
    assert bucket_of(result, "c2") == sm.NOT_OURS


def test_expressly_unmapped_from_in_repo_no_issue():
    r = row("c3", title="exploring proxies", workspace=f"{REPO_ROOTS[0]}")
    result = classify([r], now=NOW)
    assert bucket_of(result, "c3") == sm.EXPRESSLY_UNMAPPED
    assert "c3" in result.auto_unmapped


def test_repo_root_prefix_is_not_confused_with_worktrees():
    # magic-tools is a string-prefix of magic-tools-worktrees; both are "ours"
    # but a sibling like magic-tools-evil must NOT count as ours.
    r = row("c4", title="t", workspace="/Users/trippwickersham/Code/magic-tools-evil/x")
    result = classify([r], now=NOW)
    assert bucket_of(result, "c4") == sm.NOT_OURS


def test_unknown_workspace_no_issue_is_deferred_not_bucketed():
    r = row("c5", title="mystery", workspace=None, runner_online=False)
    result = classify([r], now=NOW)
    assert bucket_of(result, "c5") is None
    assert any(x.conversation_id == "c5" for x in result.examine)


# --- many-to-many edges ------------------------------------------------------


def test_one_session_two_issues():
    r = row("c6", title="combo #12 #13", workspace=f"{REPO_ROOTS[0]}")
    result = classify([r], now=NOW)
    issues = {e.issue_number for e in result.edges if e.conversation_id == "c6"}
    assert issues == {12, 13}


def test_two_sessions_one_issue():
    rows = [
        row("c7", title="part a #20", workspace=f"{REPO_ROOTS[0]}"),
        row("c8", title="part b #20", workspace=f"{REPO_ROOTS[0]}"),
    ]
    result = classify(rows, now=NOW)
    convs = {e.conversation_id for e in result.edges if e.issue_number == 20}
    assert convs == {"c7", "c8"}


# --- stickiness --------------------------------------------------------------


def test_rerun_does_not_reflag_expressly_unmapped():
    r = row("c9", title="design session", workspace=f"{REPO_ROOTS[0]}")
    first = classify([r], now=NOW)
    assert "c9" in first.auto_unmapped
    second = classify([r], prior_buckets=first.buckets, now=NOW)
    assert bucket_of(second, "c9") == sm.EXPRESSLY_UNMAPPED
    assert "c9" not in second.auto_unmapped


def test_sticky_unmapped_is_never_examined_again():
    r = row("c10", title="adhoc", workspace=f"{REPO_ROOTS[0]}")
    first = classify([r], now=NOW)
    second = classify([r], prior_buckets=first.buckets, now=NOW)
    assert not any(x.conversation_id == "c10" for x in second.examine)


def test_not_ours_stays_not_ours():
    r = row("c11", title="other", workspace="/tmp/elsewhere")
    first = classify([r], now=NOW)
    assert bucket_of(first, "c11") == sm.NOT_OURS
    second = classify([r], prior_buckets=first.buckets, now=NOW)
    assert bucket_of(second, "c11") == sm.NOT_OURS


# --- passive self-correction -------------------------------------------------


def test_promote_unmapped_via_git_commit_signal():
    ws = f"{REPO_ROOTS[1]}/wt-b"
    r = row("c12", title="ad-hoc build", workspace=ws)
    first = classify([r], now=NOW)
    assert bucket_of(first, "c12") == sm.EXPRESSLY_UNMAPPED

    signal = sm.GitSignal(workspace=ws, issue_numbers={88}, kind="commit")
    second = classify([r], git_signals=[signal], prior_buckets=first.buckets, now=NOW)
    assert bucket_of(second, "c12") == sm.MAPPED
    assert "c12" in second.promotions
    assert any(
        e.conversation_id == "c12" and e.issue_number == 88 for e in second.edges
    )


def test_promotion_uses_prior_workspace_when_row_workspace_absent():
    ws = f"{REPO_ROOTS[1]}/wt-c"
    r1 = row("c13", title="adhoc", workspace=ws)
    first = classify([r1], now=NOW)
    # later list pass row has no workspace (offline, cheap row); prior holds it
    r2 = row("c13", title="adhoc", workspace=None)
    signal = sm.GitSignal(workspace=ws, issue_numbers={5}, kind="branch")
    second = classify([r2], git_signals=[signal], prior_buckets=first.buckets, now=NOW)
    assert bucket_of(second, "c13") == sm.MAPPED


# --- the never-query invariant ----------------------------------------------


def test_classifier_never_emits_a_session_query_action():
    rows = [
        row("q1", title="mystery", workspace=None),
        row("q2", title="#9", workspace=f"{REPO_ROOTS[0]}"),
        row("q3", title="adhoc", workspace=f"{REPO_ROOTS[0]}"),
    ]
    result = classify(rows, now=NOW)
    # every examine request is a read-only get_info; nothing interrogates
    assert all(x.kind == "get_info" for x in result.examine)
    forbidden = {"message", "get_history", "send"}
    assert all(x.kind not in forbidden for x in result.examine)


def test_examine_budget_prioritises_online_sessions():
    rows = [
        row("off1", workspace=None, runner_online=False),
        row("on1", workspace=None, runner_online=True),
        row("off2", workspace=None, runner_online=False),
    ]
    result = classify(rows, max_examine=1, now=NOW)
    assert [x.conversation_id for x in result.examine] == ["on1"]


# --- steward-owned workers ---------------------------------------------------


def test_steward_seeded_edge_maps_without_title_or_git():
    # the steward created c14 seeded with issue 30 (a prior steward-seed edge)
    r = row("c14", title="worker", workspace=None, created_by_session_id="steward-conv")
    seed = sm.Edge(conversation_id="c14", issue_number=30, source="steward-seed")
    result = classify([r], prior_edges=[seed], now=NOW)
    assert bucket_of(result, "c14") == sm.MAPPED
    assert any(e.issue_number == 30 for e in result.edges if e.conversation_id == "c14")


# --- persistence round-trip --------------------------------------------------


def test_persist_and_reload_round_trip(tmp_path: Path):
    db = tmp_path / "flow.duckdb"
    rows = [
        row("p1", title="a #1 #2", workspace=f"{REPO_ROOTS[0]}"),
        row("p2", title="adhoc", workspace=f"{REPO_ROOTS[0]}"),
        row("p3", title="x", workspace="/tmp/outside"),
    ]
    result = classify(rows, now=NOW)
    con = sm.connect(str(db))
    sm.ensure_schema(con)
    sm.persist(con, result, now=NOW)

    counts = sm.bucket_counts(con)
    assert counts[sm.MAPPED] == 1
    assert counts[sm.EXPRESSLY_UNMAPPED] == 1
    assert counts[sm.NOT_OURS] == 1

    edges = sm.load_edges(con)
    p1_issues = {e.issue_number for e in edges if e.conversation_id == "p1"}
    assert p1_issues == {1, 2}
    con.close()


def test_persist_is_idempotent(tmp_path: Path):
    db = tmp_path / "flow.duckdb"
    r = row("i1", title="a #1", workspace=f"{REPO_ROOTS[0]}")
    result = classify([r], now=NOW)
    con = sm.connect(str(db))
    sm.ensure_schema(con)
    sm.persist(con, result, now=NOW)
    sm.persist(con, result, now=NOW)
    assert sm.bucket_counts(con)[sm.MAPPED] == 1
    assert len([e for e in sm.load_edges(con) if e.conversation_id == "i1"]) == 1
    con.close()


def test_persist_coexists_with_flow_event_table(tmp_path: Path):
    db = tmp_path / "flow.duckdb"
    con = duckdb.connect(str(db))
    # the other agent's table pre-exists
    con.execute("CREATE TABLE flow_event (raw_id TEXT PRIMARY KEY, kind TEXT)")
    con.execute("INSERT INTO flow_event VALUES ('r1', 'status')")
    con.close()

    r = row("x1", title="a #1", workspace=f"{REPO_ROOTS[0]}")
    result = classify([r], now=NOW)
    con = sm.connect(str(db))
    sm.ensure_schema(con)
    sm.persist(con, result, now=NOW)
    # our schema did not disturb flow_event
    assert con.execute("SELECT count(*) FROM flow_event").fetchone()[0] == 1
    assert sm.bucket_counts(con)[sm.MAPPED] == 1
    con.close()


def test_persist_promotion_updates_bucket_in_place(tmp_path: Path):
    db = tmp_path / "flow.duckdb"
    ws = f"{REPO_ROOTS[0]}"
    r = row("m1", title="adhoc", workspace=ws)
    first = classify([r], now=NOW)
    con = sm.connect(str(db))
    sm.ensure_schema(con)
    sm.persist(con, first, now=NOW)
    assert sm.bucket_counts(con)[sm.EXPRESSLY_UNMAPPED] == 1

    signal = sm.GitSignal(workspace=ws, issue_numbers={99}, kind="pr")
    prior = sm.load_buckets(con)
    second = classify([r], git_signals=[signal], prior_buckets=prior, now=NOW)
    sm.persist(con, second, now=NOW)
    counts = sm.bucket_counts(con)
    assert counts.get(sm.EXPRESSLY_UNMAPPED, 0) == 0
    assert counts[sm.MAPPED] == 1
    con.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
