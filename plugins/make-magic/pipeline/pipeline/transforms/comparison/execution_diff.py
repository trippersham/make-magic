"""Layer ③ — execution diff: the play-difference axis identity is blind to.

Two decks can read as the *same* archetype (layer ①) yet play very differently:
one ramps harder, interacts more, curves lower, closes faster. This layer surfaces
that. It diffs an **execution surface** — the engine's measured ``degree_axes``
(ramp / removal / counterspells / tutor / draw / protection / stax / evasion
counts) and ``power_profile`` (interaction efficiency), plus a mana curve computed
from the lake.

The diff itself is a **pure function** over two precomputed execution surfaces
(see :func:`diff_execution`); the lake-coupled part is isolated in
:func:`deck_curve`, which takes plain callables so it, too, stays pure and the
lake lives only at the caller's edge.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

#: The degree axes the execution layer diffs, in report order (ramp / interaction
#: / consistency / resilience / clock). Sourced from ``facts['degree_axes']``.
DEGREE_AXES: tuple[str, ...] = (
    'ramp',
    'removal',
    'counterspells',
    'tutor',
    'draw',
    'protection',
    'stax',
    'evasion',
)

#: The ``power_profile`` fields the layer reports as (a, b) pairs.
POWER_FIELDS: tuple[str, ...] = (
    'mean_efficiency',
    'total_efficiency',
    'n_interaction',
    'removal_mean_mv',
    'removal_pct_instant',
)

#: Mana-curve buckets (nonland MV), in order.
_CURVE_BUCKETS: tuple[str, ...] = ('0-1', '2', '3', '4', '5+')

_CMC_DP = 2


def axis_n(degree_axes: dict[str, Any], axis: str) -> int | None:
    """The ``n`` (card count) of a degree axis, or None when the axis is absent."""
    return (degree_axes.get(axis) or {}).get('n')


def axis_field(degree_axes: dict[str, Any], axis: str, field: str) -> Any:
    """An arbitrary field of a degree axis (e.g. ``frac``, ``cedh_level``)."""
    return (degree_axes.get(axis) or {}).get(field)


def deck_curve(
    pool: Iterable[Any],
    *,
    cmc_of: Callable[[str], float | None],
    is_early_ramp: Callable[[str], bool],
) -> dict[str, Any]:
    """Compute the nonland mana curve for a card pool. Pure given the callables.

    ``pool`` is the dump's ``pool`` layer (``[name, oracle_id]`` pairs). ``cmc_of``
    returns a card's converted mana cost, or ``None`` to exclude it from the curve
    (a land, or a card the lake can't price). ``is_early_ramp`` flags a ramp-tagged
    nonland playable by turn two (the speed proxy). The lake lives entirely behind
    these two callables, so this function never touches I/O.

    Returns ``{mean_cmc, n_spells, buckets, early_ramp_le2}``.
    """
    cmcs: list[float] = []
    early_ramp = 0
    buckets = dict.fromkeys(_CURVE_BUCKETS, 0)
    for entry in pool:
        name = entry[0] if isinstance(entry, (list, tuple)) else entry
        cmc = cmc_of(name)
        if cmc is None:
            continue
        cmcs.append(cmc)
        if cmc <= 1:
            buckets['0-1'] += 1
        elif cmc == 2:
            buckets['2'] += 1
        elif cmc == 3:
            buckets['3'] += 1
        elif cmc == 4:
            buckets['4'] += 1
        else:
            buckets['5+'] += 1
        if cmc <= 2 and is_early_ramp(name):
            early_ramp += 1
    n = len(cmcs) or 1
    return {
        'mean_cmc': round(sum(cmcs) / n, _CMC_DP),
        'n_spells': len(cmcs),
        'buckets': buckets,
        'early_ramp_le2': early_ramp,
    }


@dataclass(frozen=True)
class AxisDelta:
    """The per-axis count delta (A → B) for one degree axis."""

    axis: str
    a_n: int | None
    b_n: int | None
    delta: int | None


@dataclass(frozen=True)
class ExecutionDiff:
    """The execution (play-difference) delta between two decks (A → B).

    Attributes:
        axis_deltas: per-degree-axis ``(a_n, b_n, delta)`` in :data:`DEGREE_AXES`
            order (ramp, removal, counterspells, tutor, draw, protection, stax,
            evasion).
        a_mean_cmc / b_mean_cmc: nonland mean converted mana cost.
        a_buckets / b_buckets: nonland curve buckets.
        a_early_ramp / b_early_ramp: ramp-tagged nonlands castable by turn two.
        a_ramp_cedh / b_ramp_cedh: the ramp axis's ``cedh_level`` flag.
        power: ``field -> (a, b)`` over :data:`POWER_FIELDS` (interaction profile).
    """

    axis_deltas: list[AxisDelta]
    a_mean_cmc: float | None
    b_mean_cmc: float | None
    a_buckets: dict[str, int]
    b_buckets: dict[str, int]
    a_early_ramp: int | None
    b_early_ramp: int | None
    a_ramp_cedh: bool | None
    b_ramp_cedh: bool | None
    power: dict[str, tuple[Any, Any]]


def _delta(a: int | None, b: int | None) -> int | None:
    """``b - a`` when both are present; None when either side is missing the axis."""
    if a is None or b is None:
        return None
    return b - a


def diff_execution(a_exec: dict[str, Any], b_exec: dict[str, Any]) -> ExecutionDiff:
    """Diff two execution surfaces (A → B). Pure.

    Each surface is ``{degree_axes, power_profile, curve}`` (as cached on a
    :class:`~pipeline.transforms.comparison.cache.DeckDump`). The curve must be
    precomputed (see :func:`deck_curve`) so this function needs no lake.
    """
    a_da, b_da = a_exec.get('degree_axes') or {}, b_exec.get('degree_axes') or {}
    a_pp, b_pp = a_exec.get('power_profile') or {}, b_exec.get('power_profile') or {}
    a_curve, b_curve = a_exec.get('curve') or {}, b_exec.get('curve') or {}

    axis_deltas = [
        AxisDelta(axis=ax, a_n=axis_n(a_da, ax), b_n=axis_n(b_da, ax), delta=_delta(axis_n(a_da, ax), axis_n(b_da, ax)))
        for ax in DEGREE_AXES
    ]
    power = {f: (a_pp.get(f), b_pp.get(f)) for f in POWER_FIELDS}
    return ExecutionDiff(
        axis_deltas=axis_deltas,
        a_mean_cmc=a_curve.get('mean_cmc'),
        b_mean_cmc=b_curve.get('mean_cmc'),
        a_buckets=a_curve.get('buckets') or {},
        b_buckets=b_curve.get('buckets') or {},
        a_early_ramp=a_curve.get('early_ramp_le2'),
        b_early_ramp=b_curve.get('early_ramp_le2'),
        a_ramp_cedh=axis_field(a_da, 'ramp', 'cedh_level'),
        b_ramp_cedh=axis_field(b_da, 'ramp', 'cedh_level'),
        power=power,
    )
