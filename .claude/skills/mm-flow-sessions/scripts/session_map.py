#!/usr/bin/env -S uv run --python 3.12 --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pydantic",
#     "duckdb",
#     "typer",
# ]
# [tool.uv]
# exclude-newer = "2026-11-25T00:00:00Z"
# ///
"""Backlog Steward — read-only session <-> issue reconciliation (Phase 1.4).

A pure, offline-testable classifier plus a thin DuckDB persistence layer. It
sorts omnigent working sessions into three sticky buckets keyed by the immutable
``conversation_id`` and records many-to-many (session, issue) edges.

    mapped              -> linked to one or more issues (#N)
    expressly-unmapped  -> legitimate in-repo non-issue work (ad-hoc skill runs,
                           exploration, design sessions)
    not-ours            -> workspace outside the make-magic worktrees

The classifier is deliberately pure: it takes already-gathered cheap-rows (from a
single ``sys_session_list`` pass), synthetic/real passive git signals, and the
prior persisted state, and returns the new buckets/edges plus a *bounded*
``examine`` queue of read-only ``get_info`` requests. It NEVER emits an action
that interrogates a session (``sys_session_message`` / ``get_history`` /
repeated per-list ``get_info``) — untracked sessions stay query-free. This is
the #102 cost contract, encoded.

Why the split: ``sys_session_list`` / ``sys_session_get_info`` are omnigent MCP
tools available to the *agent*, not to a plain script. So the agent (or the
steward at runtime) makes ONE cheap list pass itself, feeds the rows here, and
lets this module classify + persist. See ``outbox.md``.

Usage:
    # classify rows + git signals gathered by the agent, persist, print counts
    ./session_map.py classify --rows rows.json --git git.json
    ./session_map.py counts

Input JSON shapes:
    rows.json : [ {"conversation_id": "...", "title": "...", "status": "...",
                   "runner_online": false, "created_by_session_id": null,
                   "workspace": null, "archived": false}, ... ]
    git.json  : [ {"workspace": "/path", "issue_numbers": [45],
                   "kind": "commit|branch|pr"}, ... ]

Maintenance:
    uv add --script session_map.py 'package-name'
    uvx ruff format session_map.py
    uvx ruff check session_map.py

References:
    - PEP 723: https://peps.python.org/pep-0723/
    - uv scripts: https://docs.astral.sh/uv/guides/scripts/
    - design.md Section 7, implementation.md Phase 1.4
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import duckdb
import typer
from pydantic import BaseModel, Field

# --- bucket + source constants ----------------------------------------------

MAPPED = "mapped"
EXPRESSLY_UNMAPPED = "expressly-unmapped"
NOT_OURS = "not-ours"

SOURCE_TITLE = "title"
SOURCE_STEWARD_SEED = "steward-seed"
GIT_SOURCE = {"commit": "git-commit", "branch": "branch", "pr": "pr"}

DEFAULT_REPO_ROOTS: tuple[str, ...] = (
    "/Users/trippwickersham/Code/magic-tools",
    "/Users/trippwickersham/Code/magic-tools-worktrees",
)

DEFAULT_DB_PATH = str(Path.home() / ".local/share/backlog-steward/flow.duckdb")

ISSUE_RE = re.compile(r"#(\d+)")


# --- data models -------------------------------------------------------------


class SessionRow(BaseModel):
    """A single cheap row from ``sys_session_list`` (plus optional workspace).

    ``workspace`` is NOT in the cheap list row; it is only present for the
    handful of sessions the agent chose to ``get_info`` (runner_online, or the
    bounded examine queue). Leaving it ``None`` is the common case.
    """

    conversation_id: str
    title: str | None = None
    status: str | None = None
    runner_online: bool = False
    created_by_session_id: str | None = None
    workspace: str | None = None
    archived: bool = False


class GitSignal(BaseModel):
    """A passive git/gh signal: issue numbers referenced on a worktree.

    Gathered free from commits / branch name / PRs (which ``mm-change``
    requires to carry ``#N`` / ``Closes #N`` / ``Part of #N``). Never obtained
    by querying a session.
    """

    workspace: str
    issue_numbers: frozenset[int]
    kind: str  # 'commit' | 'branch' | 'pr'


class Edge(BaseModel):
    """A many-to-many (session, issue) edge."""

    conversation_id: str
    issue_number: int
    source: str


class BucketAssignment(BaseModel):
    conversation_id: str
    bucket: str
    workspace: str | None = None
    title: str | None = None
    note: str = ""


class ExamineRequest(BaseModel):
    """A bounded, read-only metadata read. Kind is ALWAYS ``get_info``.

    This is the only action the classifier may ask for on an unclassified
    session, and it is cheap metadata, not a session interrogation. There is no
    code path that produces ``message`` / ``get_history`` / ``send``.
    """

    conversation_id: str
    kind: str = "get_info"


class Classification(BaseModel):
    buckets: list[BucketAssignment] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    examine: list[ExamineRequest] = Field(default_factory=list)
    auto_unmapped: list[str] = Field(default_factory=list)
    promotions: list[str] = Field(default_factory=list)


# --- pure helpers ------------------------------------------------------------


def extract_issue_refs(text: str | None) -> frozenset[int]:
    """Parse issue numbers from free text (``#45``, ``Closes #12``, ...)."""
    if not text:
        return frozenset()
    return frozenset(int(n) for n in ISSUE_RE.findall(text))


def is_ours(workspace: str, repo_roots: Sequence[str]) -> bool:
    """True iff the workspace is a make-magic worktree.

    Exact match or a path strictly under the root — never a mere string prefix,
    so a sibling like ``magic-tools-evil`` does not count as ``magic-tools``.
    """
    for raw in repo_roots:
        root = raw.rstrip("/")
        if workspace == root or workspace.startswith(root + "/"):
            return True
    return False


def _git_edges_for(
    conv: str, workspace: str | None, git_signals: Iterable[GitSignal]
) -> list[Edge]:
    if workspace is None:
        return []
    edges: list[Edge] = []
    for sig in git_signals:
        if sig.workspace == workspace:
            source = GIT_SOURCE.get(sig.kind, sig.kind)
            for issue in sig.issue_numbers:
                edges.append(
                    Edge(conversation_id=conv, issue_number=issue, source=source)
                )
    return edges


def _title_edges_for(conv: str, title: str | None) -> list[Edge]:
    return [
        Edge(conversation_id=conv, issue_number=i, source=SOURCE_TITLE)
        for i in sorted(extract_issue_refs(title))
    ]


def _dedupe_edges(edges: Iterable[Edge]) -> list[Edge]:
    seen: set[tuple[str, int, str]] = set()
    out: list[Edge] = []
    for e in edges:
        key = (e.conversation_id, e.issue_number, e.source)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _mapped_bucket(
    conv: str, workspace: str | None, title: str | None, note: str = ""
) -> BucketAssignment:
    return BucketAssignment(
        conversation_id=conv,
        bucket=MAPPED,
        workspace=workspace,
        title=title,
        note=note,
    )


# --- the classifier (pure) ---------------------------------------------------


def classify(
    rows: Sequence[SessionRow],
    git_signals: Sequence[GitSignal],
    prior_buckets: Sequence[BucketAssignment],
    prior_edges: Sequence[Edge],
    *,
    repo_roots: Sequence[str] = DEFAULT_REPO_ROOTS,
    steward_id: str | None = None,
    max_examine: int = 8,
    now: datetime | None = None,
) -> Classification:
    """Sort cheap-rows into the three sticky buckets and emit edges.

    Pure: no I/O, no MCP calls. ``prior_buckets`` / ``prior_edges`` carry the
    persisted state so buckets stay sticky across runs. The only correction of a
    wrong auto-unmap comes from passive git/title signals (``promotions``);
    unmapped and not-ours sessions are never re-examined.
    """
    del now  # accepted for call-site symmetry with persist(); unused when pure
    prior_by_id = {b.conversation_id: b for b in prior_buckets}
    seed_issues_by_id: dict[str, set[int]] = {}
    carried_edges_by_id: dict[str, list[Edge]] = {}
    for e in prior_edges:
        carried_edges_by_id.setdefault(e.conversation_id, []).append(e)
        if e.source == SOURCE_STEWARD_SEED:
            seed_issues_by_id.setdefault(e.conversation_id, set()).add(e.issue_number)

    result = Classification()
    deferred: list[SessionRow] = []

    for r in rows:
        conv = r.conversation_id
        prior = prior_by_id.get(conv)
        workspace = r.workspace or (prior.workspace if prior else None)

        title_edges = _title_edges_for(conv, r.title)
        git_edges = _git_edges_for(conv, workspace, git_signals)
        signal_issues = {e.issue_number for e in title_edges} | {
            e.issue_number for e in git_edges
        }

        # --- sticky prior buckets -------------------------------------------
        if prior is not None:
            if prior.bucket == NOT_OURS:
                result.buckets.append(prior)
                continue
            if prior.bucket == MAPPED:
                result.buckets.append(
                    _mapped_bucket(conv, workspace, r.title, prior.note)
                )
                result.edges.extend(carried_edges_by_id.get(conv, []))
                result.edges.extend(title_edges)
                result.edges.extend(git_edges)
                continue
            if prior.bucket == EXPRESSLY_UNMAPPED:
                # passive self-correction ONLY — never interrogate the session.
                if signal_issues:
                    result.buckets.append(
                        _mapped_bucket(
                            conv,
                            workspace,
                            r.title,
                            note="promoted from expressly-unmapped via passive signal",
                        )
                    )
                    result.edges.extend(title_edges)
                    result.edges.extend(git_edges)
                    result.promotions.append(conv)
                else:
                    result.buckets.append(prior)
                continue

        # --- new session ----------------------------------------------------
        issues = signal_issues | seed_issues_by_id.get(conv, set())
        if issues:
            result.buckets.append(_mapped_bucket(conv, workspace, r.title))
            result.edges.extend(title_edges)
            result.edges.extend(git_edges)
            result.edges.extend(
                Edge(conversation_id=conv, issue_number=i, source=SOURCE_STEWARD_SEED)
                for i in sorted(seed_issues_by_id.get(conv, set()))
            )
        elif workspace is not None and not is_ours(workspace, repo_roots):
            result.buckets.append(
                BucketAssignment(
                    conversation_id=conv,
                    bucket=NOT_OURS,
                    workspace=workspace,
                    title=r.title,
                )
            )
        elif workspace is not None:  # in-repo, no issue signal
            result.buckets.append(
                BucketAssignment(
                    conversation_id=conv,
                    bucket=EXPRESSLY_UNMAPPED,
                    workspace=workspace,
                    title=r.title,
                    note="in-repo non-issue work",
                )
            )
            result.auto_unmapped.append(conv)
        else:
            # workspace unknown + no passive issue signal: needs one cheap,
            # read-only get_info. Deferred (bounded), never bucketed blind.
            deferred.append(r)

    # Bounded examine queue, runner_online first (cost contract).
    for r in sorted(deferred, key=lambda s: not s.runner_online)[:max_examine]:
        result.examine.append(ExamineRequest(conversation_id=r.conversation_id))

    result.edges = _dedupe_edges(result.edges)
    return result


# --- persistence (DuckDB) ----------------------------------------------------


def connect(db_path: str = DEFAULT_DB_PATH) -> duckdb.DuckDBPyConnection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(db_path)


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create our own tables only. Never create/alter ``flow_event``."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS session_bucket (
            conversation_id  TEXT PRIMARY KEY,
            bucket           TEXT NOT NULL,
            workspace        TEXT,
            title            TEXT,
            note             TEXT,
            first_classified TIMESTAMP NOT NULL,
            last_seen        TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS session_edge (
            conversation_id TEXT NOT NULL,
            issue_number    INTEGER NOT NULL,
            source          TEXT NOT NULL,
            first_seen      TIMESTAMP NOT NULL,
            last_seen       TIMESTAMP NOT NULL,
            PRIMARY KEY (conversation_id, issue_number, source)
        )
        """
    )


def persist(
    con: duckdb.DuckDBPyConnection,
    classification: Classification,
    *,
    now: datetime | None = None,
) -> None:
    """Upsert buckets + edges. Idempotent; keeps first_classified/first_seen."""
    ts = now or datetime.now(UTC)
    for b in classification.buckets:
        con.execute(
            """
            INSERT INTO session_bucket
                (conversation_id, bucket, workspace, title, note,
                 first_classified, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (conversation_id) DO UPDATE SET
                bucket    = excluded.bucket,
                workspace = excluded.workspace,
                title     = excluded.title,
                note      = excluded.note,
                last_seen = excluded.last_seen
            """,
            [b.conversation_id, b.bucket, b.workspace, b.title, b.note, ts, ts],
        )
    for e in classification.edges:
        con.execute(
            """
            INSERT INTO session_edge
                (conversation_id, issue_number, source, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (conversation_id, issue_number, source) DO UPDATE SET
                last_seen = excluded.last_seen
            """,
            [e.conversation_id, e.issue_number, e.source, ts, ts],
        )


def load_buckets(con: duckdb.DuckDBPyConnection) -> list[BucketAssignment]:
    rows = con.execute(
        "SELECT conversation_id, bucket, workspace, title, note FROM session_bucket"
    ).fetchall()
    return [
        BucketAssignment(
            conversation_id=r[0],
            bucket=r[1],
            workspace=r[2],
            title=r[3],
            note=r[4] or "",
        )
        for r in rows
    ]


def load_edges(con: duckdb.DuckDBPyConnection) -> list[Edge]:
    rows = con.execute(
        "SELECT conversation_id, issue_number, source FROM session_edge"
    ).fetchall()
    return [Edge(conversation_id=r[0], issue_number=r[1], source=r[2]) for r in rows]


def bucket_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    rows = con.execute(
        "SELECT bucket, count(*) FROM session_bucket GROUP BY bucket"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# --- CLI ---------------------------------------------------------------------

app = typer.Typer(add_completion=False, help=__doc__)


def _read_rows(path: Path) -> list[SessionRow]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SessionRow.model_validate(d) for d in data]


def _read_git(path: Path | None) -> list[GitSignal]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [GitSignal.model_validate(d) for d in data]


@app.command("classify")
def classify_cmd(
    rows: Annotated[
        Path, typer.Option("--rows", help="JSON array of cheap session rows")
    ],
    git: Annotated[
        Path | None, typer.Option("--git", help="JSON array of git signals")
    ] = None,
    db: Annotated[
        str, typer.Option("--db", help="DuckDB store path")
    ] = DEFAULT_DB_PATH,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="classify but do not persist")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="print the Classification JSON")
    ] = False,
) -> None:
    """Classify agent-gathered rows + git signals, persist, print bucket counts."""
    session_rows = _read_rows(rows)
    git_signals = _read_git(git)
    con = connect(db)
    ensure_schema(con)
    result = classify(session_rows, git_signals, load_buckets(con), load_edges(con))
    if not dry_run:
        persist(con, result)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    counts = {MAPPED: 0, EXPRESSLY_UNMAPPED: 0, NOT_OURS: 0}
    counts.update(bucket_counts(con) if not dry_run else _count(result))
    typer.echo(
        f"mapped={counts.get(MAPPED, 0)} "
        f"expressly-unmapped={counts.get(EXPRESSLY_UNMAPPED, 0)} "
        f"not-ours={counts.get(NOT_OURS, 0)} "
        f"deferred/examine={len(result.examine)} "
        f"promotions={len(result.promotions)} "
        f"new-auto-unmapped={len(result.auto_unmapped)}"
    )
    con.close()


def _count(result: Classification) -> dict[str, int]:
    out: dict[str, int] = {}
    for b in result.buckets:
        out[b.bucket] = out.get(b.bucket, 0) + 1
    return out


@app.command()
def counts(
    db: Annotated[
        str, typer.Option("--db", help="DuckDB store path")
    ] = DEFAULT_DB_PATH,
) -> None:
    """Print the persisted bucket counts (zero list/MCP calls)."""
    con = connect(db)
    ensure_schema(con)
    c = bucket_counts(con)
    typer.echo(
        f"mapped={c.get(MAPPED, 0)} "
        f"expressly-unmapped={c.get(EXPRESSLY_UNMAPPED, 0)} "
        f"not-ours={c.get(NOT_OURS, 0)}"
    )
    con.close()


if __name__ == "__main__":
    app()
