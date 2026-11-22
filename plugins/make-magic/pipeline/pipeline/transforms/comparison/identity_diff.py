"""Layer ① — identity diff: archetype-identity vectors over two decks.

The *identity* of a deck is the engine's bounded archetype projection — which
strategies it reads as, and how strongly. This module turns that projection into
comparable vectors and diffs two of them: cosine (angle / shape agreement), L1
(absolute share movement), the added/dropped identity set, and the ranked
reweight table (per-strategy ``weighted_quantity`` deltas, largest first — the
"ranked gap").

Pure functions over a **semantic dump** (the ``build_record`` output): everything
here reads ``dump['archetype_identity']`` — the ``archetype_identity`` (bounded,
share-normalized identity set) and ``ranked_identity`` (fuller, pre-cut weighted
table) layers — and nothing else. No lake, no I/O, no engine call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Rounding for the scalar similarity read-outs (matches the spike's reporting).
_SIM_DP = 3
#: Rounding for the weighted-quantity reweight table.
_WQ_DP = 1


def _identity_block(dump: dict[str, Any]) -> dict[str, Any]:
    """The ``archetype_identity`` layer of a semantic dump (empty when absent)."""
    return dump.get('archetype_identity') or {}


def identity_share_vector(dump: dict[str, Any]) -> dict[str, float]:
    """``strategy -> share`` from the bounded ``archetype_identity`` set.

    The share-normalized identity: a small, cut set of the strategies the deck
    actually reads as, each weighted by its share of the identity mass. This is
    the vector for *shape* agreement (cosine) and absolute drift (L1).
    """
    return {d['strategy']: float(d['share']) for d in _identity_block(dump).get('archetype_identity') or []}


def identity_ranked_vector(dump: dict[str, Any]) -> dict[str, float]:
    """``strategy -> weighted_quantity`` from the fuller ``ranked_identity`` table.

    The pre-cut ranked table carries every strategy the deck touches (not only
    the bounded identity set), each with its raw ``weighted_quantity``. This is
    the vector for the ranked reweight gap and the deeper cosine.
    """
    out: dict[str, float] = {}
    for row in _identity_block(dump).get('ranked_identity') or []:
        out[row[0]] = float(row[1])
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity of two sparse strategy vectors (0.0 when either is empty)."""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return round(dot / (na * nb), _SIM_DP) if na and nb else 0.0


def l1(a: dict[str, float], b: dict[str, float]) -> float:
    """L1 (Manhattan) distance between two sparse strategy vectors."""
    keys = set(a) | set(b)
    return round(sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys), _SIM_DP)


@dataclass(frozen=True)
class IdentityDiff:
    """The archetype-identity delta between two decks (A → B).

    Attributes:
        a_identity: A's bounded identity strategies, in identity order.
        b_identity: B's bounded identity strategies, in identity order.
        added_in_b: strategies in B's identity but not A's (identity gained).
        dropped_in_b: strategies in A's identity but not B's (identity lost).
        cosine_share: cosine of the two share vectors (shape agreement of the
            bounded identity; 1.0 = same shape, 0.0 = orthogonal).
        cosine_ranked: cosine of the two ranked ``weighted_quantity`` vectors
            (agreement over the fuller, pre-cut table).
        l1_share: L1 distance of the two share vectors (total absolute drift).
        ranked_gap: ``(strategy, a_wq, b_wq, delta)`` over the union of ranked
            strategies, largest ``|delta|`` first — the ranked reweight table.
    """

    a_identity: list[str]
    b_identity: list[str]
    added_in_b: list[str]
    dropped_in_b: list[str]
    cosine_share: float
    cosine_ranked: float
    l1_share: float
    ranked_gap: list[tuple[str, float, float, float]]


def ranked_gap(a_dump: dict[str, Any], b_dump: dict[str, Any]) -> list[tuple[str, float, float, float]]:
    """Per-strategy ``(strategy, a_wq, b_wq, delta)`` over the ranked union, by ``|delta|``.

    The ranked reweight table: for every strategy either deck touches, how the
    raw ``weighted_quantity`` moved from A to B, ordered by the magnitude of the
    move so the biggest shifts surface first.
    """
    a, b = identity_ranked_vector(a_dump), identity_ranked_vector(b_dump)
    rows = [
        (k, round(a.get(k, 0.0), _WQ_DP), round(b.get(k, 0.0), _WQ_DP), round(b.get(k, 0.0) - a.get(k, 0.0), _WQ_DP))
        for k in set(a) | set(b)
    ]
    rows.sort(key=lambda r: (-abs(r[3]), r[0]))
    return rows


def diff_identity(a_dump: dict[str, Any], b_dump: dict[str, Any]) -> IdentityDiff:
    """Diff two semantic dumps' archetype identities (A → B). Pure."""
    a_share, b_share = identity_share_vector(a_dump), identity_share_vector(b_dump)
    a_ranked, b_ranked = identity_ranked_vector(a_dump), identity_ranked_vector(b_dump)
    a_id = [d['strategy'] for d in _identity_block(a_dump).get('archetype_identity') or []]
    b_id = [d['strategy'] for d in _identity_block(b_dump).get('archetype_identity') or []]
    a_set, b_set = set(a_id), set(b_id)
    return IdentityDiff(
        a_identity=a_id,
        b_identity=b_id,
        added_in_b=sorted(b_set - a_set),
        dropped_in_b=sorted(a_set - b_set),
        cosine_share=cosine(a_share, b_share),
        cosine_ranked=cosine(a_ranked, b_ranked),
        l1_share=l1(a_share, b_share),
        ranked_gap=ranked_gap(a_dump, b_dump),
    )
