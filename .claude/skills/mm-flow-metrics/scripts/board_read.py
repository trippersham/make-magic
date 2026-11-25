#!/usr/bin/env -S uv run --python 3.12 --with pydantic --with typer --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pydantic",
#     "typer",
# ]
# [tool.uv]
# exclude-newer = "2026-11-25T00:00:00Z"
# ///
"""
Backlog Steward — READ-ONLY board state via `gh api graphql`.

Reads the Projects v2 board and issue timelines; NEVER mutates the board.
Returns normalized rows:
    {issue_number, item_node_id, status, labels[], assignees[],
     linked_pr, linked_branch, updated_at}

Structured-variable queries go through `gh api graphql --input -` (piping a
`{"query": ..., "variables": {...}}` JSON object on stdin). Plain `-F` cannot
pass nested objects such as cursors or option ids — a confirmed
board-bootstrap finding. The network call (`_graphql`) is kept separate from
the pure `normalize_board` so shape-mapping is fixture-tested offline.

Board IDs (project id, status field id, option/label ids) currently live in
`subagents/board-bootstrap/board-ids.json`. They BELONG in `.claude/steward.yml`
(Phase 0.2); `load_board_ids()` reads the json for now and the wiring is flagged
as follow-up in the outbox.

Usage:
    ./board_read.py summary                   # live board status/label summary
    ./board_read.py rows                       # live normalized rows as JSON

Maintenance:
    uvx ruff check board_read.py
    uvx ruff format board_read.py

References:
    - PEP 723: https://peps.python.org/pep-0723/
    - research/metrics-data-layer.md
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import typer

# Default location of the live board ids (until wired into .claude/steward.yml).
# These live in the persistent plans dir under $HOME/.claude, NOT in the repo.
BOARD_IDS_JSON = (
    Path.home()
    / ".claude"
    / "plans"
    / "trippersham"
    / "make-magic"
    / "2026-11-24-backlog-steward"
    / "subagents"
    / "board-bootstrap"
    / "board-ids.json"
)

REPO = "trippersham/make-magic"
STATUS_FIELD = "Status"

_BOARD_QUERY = """
query($project: ID!, $cursor: String) {
  node(id: $project) {
    ... on ProjectV2 {
      items(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          updatedAt
          fieldValueByName(name: "__STATUS_FIELD__") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            ... on Issue {
              number
              updatedAt
              labels(first: 20) { nodes { name } }
              assignees(first: 10) { nodes { login } }
              linkedBranches(first: 5) { nodes { ref { name } } }
              closedByPullRequestsReferences(first: 5) { nodes { number headRefName } }
            }
          }
        }
      }
    }
  }
}
""".replace("__STATUS_FIELD__", STATUS_FIELD)

# Timeline replay for label pauses + the status-regression canary, in one pass.
_TIMELINE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      timelineItems(
        first: 100
        after: $cursor
        itemTypes: [LABELED_EVENT, UNLABELED_EVENT, PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]
      ) {
        pageInfo { hasNextPage endCursor }
        nodes {
          __typename
          ... on LabeledEvent   { id createdAt label { name } }
          ... on UnlabeledEvent { id createdAt label { name } }
          ... on ProjectV2ItemStatusChangedEvent { createdAt previousStatus status }
        }
      }
    }
  }
}
"""


# ----------------------------------------------------------------- pure mappers
def _nodes(obj: Any, *path: str) -> list[dict[str, Any]]:
    """Safely walk `a.b.nodes`-style GraphQL, returning [] on any null."""
    cur = obj
    for p in path:
        if cur is None:
            return []
        cur = cur.get(p)
    if cur is None:
        return []
    return cur.get("nodes") or []


def normalize_board(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a ProjectV2 items payload into board rows (issues only)."""
    items = (((response.get("data") or {}).get("node") or {}).get("items") or {}).get(
        "nodes"
    ) or []
    rows: list[dict[str, Any]] = []
    for it in items:
        content = it.get("content")
        if not content or "number" not in content:
            continue  # draft item / non-issue content: skip
        fv = it.get("fieldValueByName")
        status = fv.get("name") if fv else None
        labels = [n["name"] for n in _nodes(content, "labels")]
        assignees = [n["login"] for n in _nodes(content, "assignees")]
        prs = _nodes(content, "closedByPullRequestsReferences")
        linked_pr = prs[0]["number"] if prs else None
        branches = _nodes(content, "linkedBranches")
        if branches:
            linked_branch = branches[0]["ref"]["name"]
        elif prs:
            linked_branch = prs[0].get("headRefName")
        else:
            linked_branch = None
        rows.append(
            {
                "issue_number": content["number"],
                "item_node_id": it["id"],
                "status": status,
                "labels": labels,
                "assignees": assignees,
                "linked_pr": linked_pr,
                "linked_branch": linked_branch,
                "updated_at": content.get("updatedAt") or it.get("updatedAt"),
            }
        )
    return rows


def node_map(rows: list[dict[str, Any]]) -> dict[int, str]:
    """issue_number -> item_node_id (for joining label timelines to board items)."""
    return {r["issue_number"]: r["item_node_id"] for r in rows}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Status + label counts for a quick human-readable board summary."""
    by_status = Counter(r["status"] or "(unset)" for r in rows)
    by_label: Counter[str] = Counter()
    for r in rows:
        by_label.update(r["labels"])
    return {
        "total": len(rows),
        "by_status": dict(by_status),
        "by_label": dict(by_label),
    }


# --------------------------------------------------------------------- network
def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Run a read-only GraphQL query via `gh api graphql --input -`."""
    payload = json.dumps({"query": query, "variables": variables})
    proc = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def load_board_ids(path: Path = BOARD_IDS_JSON) -> dict[str, Any]:
    """Read live board node-ids (project/status-field/options/labels)."""
    return json.loads(Path(path).read_text())


def read_board(project_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch the full (paginated) live board and normalize it. READ-ONLY."""
    if project_id is None:
        project_id = load_board_ids()["project"]["id"]
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        resp = _graphql(_BOARD_QUERY, {"project": project_id, "cursor": cursor})
        rows.extend(normalize_board(resp))
        page = (((resp.get("data") or {}).get("node") or {}).get("items") or {}).get(
            "pageInfo"
        ) or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return rows


def fetch_timeline(
    numbers: list[int], repo: str = REPO
) -> dict[str, list[dict[str, Any]]]:
    """Fetch label + status-changed timeline nodes per issue. READ-ONLY."""
    owner, name = repo.split("/", 1)
    out: dict[str, list[dict[str, Any]]] = {}
    for num in numbers:
        nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            resp = _graphql(
                _TIMELINE_QUERY,
                {"owner": owner, "repo": name, "number": num, "cursor": cursor},
            )
            tl = (
                ((resp.get("data") or {}).get("repository") or {}).get("issue") or {}
            ).get("timelineItems") or {}
            nodes.extend(tl.get("nodes") or [])
            page = tl.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
        out[str(num)] = nodes
    return out


# ------------------------------------------------------------------------- CLI
app = typer.Typer(add_completion=False, help="Read-only Backlog Steward board reads.")


@app.command()
def summary() -> None:
    """Print a live board status/label summary."""
    rows = read_board()
    summ = summarize(rows)
    typer.echo(json.dumps(summ, indent=2, sort_keys=True))


@app.command()
def rows() -> None:
    """Print the live normalized board rows as JSON."""
    typer.echo(json.dumps(read_board(), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
