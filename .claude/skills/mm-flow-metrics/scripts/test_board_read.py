#!/usr/bin/env -S uv run --python 3.12 --with pytest --with pydantic --with typer --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pytest",
#     "pydantic",
#     "typer",
# ]
# [tool.uv]
# exclude-newer = "2026-11-25T00:00:00Z"
# ///
"""
Offline tests for board_read: the pure normalization of a Projects v2 GraphQL
response into flat board rows. No network — the GraphQL call is separated from
`normalize_board` so the shape-mapping is fixture-tested here.

Usage:
    uv run --with pytest --with pydantic --with typer \
        pytest .claude/skills/mm-flow-metrics/scripts/test_board_read.py

Maintenance:
    uvx ruff check test_board_read.py
    uvx ruff format test_board_read.py
"""

from __future__ import annotations

import board_read as br

# A Projects v2 `node(ProjectV2).items` payload with the item shapes we expect:
# a fully-populated issue, a status-less issue, and a draft (content == null).
SAMPLE = {
    "data": {
        "node": {
            "items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [
                    {
                        "id": "ITEM_full",
                        "updatedAt": "2026-11-20T10:00:00Z",
                        "fieldValueByName": {"name": "In Progress"},
                        "content": {
                            "number": 42,
                            "updatedAt": "2026-11-21T09:00:00Z",
                            "labels": {
                                "nodes": [{"name": "blocked"}, {"name": "expedite"}]
                            },
                            "assignees": {"nodes": [{"login": "trippersham"}]},
                            "linkedBranches": {
                                "nodes": [{"ref": {"name": "feat/do-thing"}}]
                            },
                            "closedByPullRequestsReferences": {
                                "nodes": [{"number": 7, "headRefName": "feat/do-thing"}]
                            },
                        },
                    },
                    {
                        "id": "ITEM_bare",
                        "updatedAt": "2026-11-19T08:00:00Z",
                        "fieldValueByName": None,  # Status unset
                        "content": {
                            "number": 43,
                            "updatedAt": "2026-11-19T08:00:00Z",
                            "labels": {"nodes": []},
                            "assignees": {"nodes": []},
                            "linkedBranches": {"nodes": []},
                            "closedByPullRequestsReferences": {"nodes": []},
                        },
                    },
                    {
                        "id": "ITEM_draft",
                        "updatedAt": "2026-11-18T08:00:00Z",
                        "fieldValueByName": {"name": "Inbox"},
                        "content": None,  # draft item, not an issue
                    },
                ],
            }
        }
    }
}


def test_normalize_board_maps_full_issue():
    rows = br.normalize_board(SAMPLE)
    full = next(r for r in rows if r["item_node_id"] == "ITEM_full")
    assert full["issue_number"] == 42
    assert full["status"] == "In Progress"
    assert full["labels"] == ["blocked", "expedite"]
    assert full["assignees"] == ["trippersham"]
    assert full["linked_pr"] == 7
    assert full["linked_branch"] == "feat/do-thing"
    assert full["updated_at"] == "2026-11-21T09:00:00Z"


def test_normalize_board_handles_unset_status_and_empty_lists():
    rows = br.normalize_board(SAMPLE)
    bare = next(r for r in rows if r["item_node_id"] == "ITEM_bare")
    assert bare["issue_number"] == 43
    assert bare["status"] is None
    assert bare["labels"] == []
    assert bare["assignees"] == []
    assert bare["linked_pr"] is None
    assert bare["linked_branch"] is None


def test_normalize_board_skips_draft_items_without_issue_content():
    rows = br.normalize_board(SAMPLE)
    assert all(r["item_node_id"] != "ITEM_draft" for r in rows)
    assert len(rows) == 2


def test_node_map_is_issue_number_to_item_node_id():
    rows = br.normalize_board(SAMPLE)
    assert br.node_map(rows) == {42: "ITEM_full", 43: "ITEM_bare"}


def test_summarize_counts_status_and_labels():
    rows = br.normalize_board(SAMPLE)
    summ = br.summarize(rows)
    assert summ["by_status"]["In Progress"] == 1
    assert summ["by_status"]["(unset)"] == 1
    assert summ["by_label"]["blocked"] == 1
    assert summ["by_label"]["expedite"] == 1
    assert summ["total"] == 2


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
