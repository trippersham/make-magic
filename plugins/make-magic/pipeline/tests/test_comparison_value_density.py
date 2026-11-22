"""TDD tests for value-density (comparison/value_density.py).

Two layers:

* **Unit** — the arithmetic (Σ/(MV+1), role-scoping, ≥0 clamp, residual) over a small
  SYNTHETIC table, so the logic is pinned independent of the authored weights.
* **Authored-table** — loads the REAL shipped ``data/attribute_values.json`` and asserts
  the modeling acceptance: ``repeatable-draw`` is no longer wrong-signed, and the draw slot
  ranks cheap-premium draw (Mystic Remora / Phyrexian Arena) ABOVE the ubiquitous-but-
  mediocre Commander's Sphere (the popularity-bias correction).
"""

from __future__ import annotations

import pytest

# NB: import the functions from the submodule by its full dotted path — the package
# __init__ exports a `value_density` FUNCTION, which shadows the same-named submodule as a
# package attribute, so `from ...comparison import value_density` would grab the function.
from pipeline.transforms.comparison.value_density import (
    load_value_table,
    mv_residual,
    value_density,
    value_sum,
)

# A tiny synthetic table — role-family draw attrs + an off-role splashy attr + a noisy
# negative-weight attr (to prove the ≥0 clamp) + MV-bucket expectations.
SYNTH = {
    'attribute_values': {
        'repeatable-draw': 3.0,
        'draw-engine': 3.0,
        'pure-draw': 1.5,
        'draw': 1.5,
        'mana-rock': 1.4,
        'noisy': -2.0,  # a negative data coefficient — must never subtract
    },
    'expected_v_by_mv_bucket': {'1': 2.0, '3': 5.0},
    'meta': {'basis': 'popularity-not-winrate'},
}

DRAW_ATTRS = {'draw', 'pure-draw', 'draw-engine', 'repeatable-draw', 'repeatable-pure-draw', 'burst-draw'}


def test_value_sum_is_role_scoped() -> None:
    """Only role-family attributes count when role_attrs is given (off-role is ignored)."""
    attrs = {'repeatable-draw', 'draw-engine', 'pure-draw', 'draw', 'mana-rock'}
    scoped = value_sum(attrs, role_attrs=DRAW_ATTRS, table=SYNTH)
    glob = value_sum(attrs, table=SYNTH)
    assert scoped == pytest.approx(3.0 + 3.0 + 1.5 + 1.5)  # mana-rock excluded
    assert glob == pytest.approx(scoped + 1.4)  # global includes mana-rock


def test_negative_weight_never_subtracts() -> None:
    """A negative data coefficient contributes 0, not a penalty (value floor, not a model)."""
    assert value_sum({'draw', 'noisy'}, table=SYNTH) == pytest.approx(1.5)


def test_density_is_value_over_mv_plus_one() -> None:
    attrs = {'repeatable-draw', 'draw-engine', 'pure-draw', 'draw'}
    v = 3.0 + 3.0 + 1.5 + 1.5
    assert value_density(attrs, 1.0, role_attrs=DRAW_ATTRS, table=SYNTH) == pytest.approx(v / 2.0)
    assert value_density(attrs, 3.0, role_attrs=DRAW_ATTRS, table=SYNTH) == pytest.approx(v / 4.0)


def test_cheaper_same_role_card_is_denser() -> None:
    """Same role attributes, lower MV ⇒ higher density (the rate corrective)."""
    attrs = {'repeatable-draw', 'draw-engine', 'pure-draw', 'draw'}
    cheap = value_density(attrs, 1.0, role_attrs=DRAW_ATTRS, table=SYNTH)
    pricey = value_density(attrs, 3.0, role_attrs=DRAW_ATTRS, table=SYNTH)
    assert cheap > pricey


def test_zero_mv_is_finite() -> None:
    """A 0-MV card is dense, not infinite (+1 denominator)."""
    assert value_density({'draw'}, 0.0, table=SYNTH) == pytest.approx(1.5)


def test_value_density_refuses_non_finite_mv() -> None:
    """M2: a non-finite MV is refused (never laundered into a NaN density)."""
    for bad in (float('nan'), float('inf'), float('-inf')):
        with pytest.raises(ValueError, match='finite'):
            value_density({'draw'}, bad, table=SYNTH)


def test_value_density_guards_mv_at_minus_one() -> None:
    """m1: MV = -1 (denominator 0) is guarded, not a ZeroDivisionError."""
    with pytest.raises(ValueError):
        value_density({'draw'}, -1.0, table=SYNTH)
    # And a sub-(-1) MV (negative denominator) is guarded too.
    with pytest.raises(ValueError):
        value_density({'draw'}, -2.0, table=SYNTH)


def test_mv_residual_above_and_below_rate() -> None:
    v = value_sum({'repeatable-draw', 'draw-engine', 'pure-draw', 'draw'}, table=SYNTH)  # 9.0
    assert mv_residual({'repeatable-draw', 'draw-engine', 'pure-draw', 'draw'}, 1.0, table=SYNTH) == pytest.approx(
        v - 2.0
    )
    assert mv_residual({'draw'}, 3.0, table=SYNTH) == pytest.approx(1.5 - 5.0)


# --------------------------------------------------------------------------- #
# Authored-table acceptance (the real shipped weights).
# --------------------------------------------------------------------------- #


def test_authored_table_loads_and_is_shaped() -> None:
    t = load_value_table()
    assert 'attribute_values' in t
    assert t['meta']['basis'] == 'popularity-not-winrate'
    assert len(t['attribute_values']) > 50


def test_repeatable_draw_no_longer_wrong_signed() -> None:
    """The headline modeling fix: repeatable-draw carries a POSITIVE authored weight."""
    weights = load_value_table()['attribute_values']
    assert weights['repeatable-draw'] > 0.0
    assert weights['draw-engine'] > 0.0


def test_draw_slot_ranks_cheap_premium_above_ubiquitous_filler() -> None:
    """Mystic Remora / Phyrexian Arena out-rank Commander's Sphere for the draw slot."""
    remora = {'draw', 'draw-engine', 'pure-draw', 'repeatable-draw', 'repeatable-pure-draw'}
    arena = set(remora)
    sphere = {'draw', 'pure-draw', 'mana-rock', 'ramp', 'utility-mana-rock'}  # mostly off-role
    d_remora = value_density(remora, 1.0, role_attrs=DRAW_ATTRS)
    d_arena = value_density(arena, 3.0, role_attrs=DRAW_ATTRS)
    d_sphere = value_density(sphere, 3.0, role_attrs=DRAW_ATTRS)
    assert d_remora > d_sphere
    assert d_arena > d_sphere
