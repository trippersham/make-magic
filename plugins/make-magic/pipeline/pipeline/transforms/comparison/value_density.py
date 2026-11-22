"""Value-density — the popularity-free corrective of axis B (pure table-lookup).

Value-density answers "how much engine-relevant VALUE does this card pack per mana",
independent of how *popular* it is — the corrective to ``edhrec_rank``'s popularity
bias (a card can be ubiquitous-but-mediocre, e.g. Commander's Sphere, or
cheap-but-premium-and-underplayed, e.g. Mystic Remora). It is ``Σ(attribute value) ÷
(MV + 1)`` — Chapin's *Rate = Effect / Cost* made concrete (research §5).

This module is **pure table-lookup**: it imports no numpy and does no fitting. The
attribute→value weights are *reference data* (``data/attribute_values.json``), authored
offline by ``tools/fit_attribute_values.py`` (the MV-controlled ridge shrunk toward
Chapin theory priors). The split is deliberate — the modeling judgment is frozen into
data, and the runtime is a deterministic, dependency-light lookup, exactly like the otag
catalogs.

Two design points from the spike (``research/spike/FINDINGS.md``):

* **Role-scoped, not global.** A global Σ over *all* a card's attributes over-rewards
  cheap kitchen-sink cards (Ragavan tops a "draw slot" it has no business in). Pass
  ``role_attrs`` (the attribute family the slot is about) and only those attributes count,
  so the density answers "how strong a *draw engine* is this", not "how splashy is this card".
* **Negative weights never subtract.** The authored table may carry a small negative data
  coefficient for a noisy attribute; the value SUM clamps each contribution at ``max(0, w)``
  so an attribute can only ever add value, never dock it (the per-card score is a value
  floor, not a penalty model — penalties are out of scope, offloaded to role gating).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).resolve().parent / 'data' / 'attribute_values.json'


@lru_cache(maxsize=1)
def load_value_table(path: str | None = None) -> dict[str, Any]:
    """Load the authored attribute→value table (cached). Reads the shipped JSON by default."""
    p = Path(path) if path is not None else _DATA_PATH
    return json.loads(p.read_text())


def value_sum(
    attrs: set[str],
    *,
    role_attrs: set[str] | None = None,
    table: dict[str, Any] | None = None,
) -> float:
    """Σ(attribute value) over a card's attributes, role-scoped and clamped >=0 per term.

    ``role_attrs`` restricts the sum to the slot's attribute family (the role-scoping the
    spike proved mandatory); ``None`` sums all of the card's attributes (a whole-card
    reading). Each attribute contributes ``max(0, weight)`` — never a penalty.
    """
    weights = (table or load_value_table())['attribute_values']
    scope = attrs if role_attrs is None else (attrs & role_attrs)
    return sum(max(0.0, float(weights.get(a, 0.0))) for a in scope)


def value_density(
    attrs: set[str],
    mv: float,
    *,
    role_attrs: set[str] | None = None,
    table: dict[str, Any] | None = None,
) -> float:
    """``Σ(attribute value) ÷ (MV + 1)`` — the headline value-per-mana rate.

    The ``+1`` keeps a 0-MV card finite (a Mox is very dense, not infinitely so) and softens
    the curve (research §5). Role-scope with ``role_attrs`` for slot-filling; omit it for a
    whole-card density.
    """
    v = value_sum(attrs, role_attrs=role_attrs, table=table)
    return v / (mv + 1.0)


def mv_residual(
    attrs: set[str],
    mv: float,
    *,
    table: dict[str, Any] | None = None,
) -> float:
    """Over/under-rate diagnostic: ``V - E[V | MV bucket]`` (research §5, the companion form).

    Positive = the card packs more value than a typical card of its mana value (above rate);
    negative = below rate. Uses the authored per-bucket expected-V; an absent bucket ⇒ 0.
    """
    t = table or load_value_table()
    v = value_sum(attrs, table=t)
    key = str(int(mv)) if mv < 5 else '5+'
    expected = float(t.get('expected_v_by_mv_bucket', {}).get(key, 0.0))
    return v - expected
