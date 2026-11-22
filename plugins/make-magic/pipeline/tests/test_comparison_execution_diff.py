"""Unit tests for layer ③ execution diff (``comparison.execution_diff``).

Offline + fixture-free: the diff reads hand-built execution surfaces
(``degree_axes`` / ``power_profile`` / ``curve``), and ``deck_curve`` is driven
with plain callables, so the lake never enters the test.
"""

from __future__ import annotations

from pipeline.transforms.comparison import ExecutionDiff, deck_curve, diff_execution
from pipeline.transforms.comparison.execution_diff import (
    DEGREE_AXES,
    POWER_FIELDS,
    AxisDelta,
    axis_n,
)
from pipeline.transforms.comparison.execution_diff import diff_execution as diff_execution_direct


def test_public_home_re_exports_the_cores() -> None:
    assert diff_execution is diff_execution_direct


def test_axis_n_reads_count_or_none() -> None:
    assert axis_n({'ramp': {'n': 24}}, 'ramp') == 24
    assert axis_n({'ramp': {'n': 24}}, 'tutor') is None
    assert axis_n({}, 'ramp') is None


def test_deck_curve_buckets_mean_and_early_ramp() -> None:
    pool = [['Sol Ring', 'oid1'], ['Cultivate', 'oid2'], ['Island', 'oid3'], ['Avenger', 'oid4']]
    cmc = {'Sol Ring': 1.0, 'Cultivate': 3.0, 'Island': None, 'Avenger': 6.0}
    ramp = {'Sol Ring', 'Cultivate'}  # both ramp; only Sol Ring is castable by turn 2 (cmc<=2)
    curve = deck_curve(
        pool,
        cmc_of=lambda n: cmc[n],
        is_early_ramp=lambda n: n in ramp,
    )
    assert curve['n_spells'] == 3  # Island (None) excluded
    assert curve['mean_cmc'] == round((1.0 + 3.0 + 6.0) / 3, 2)
    assert curve['buckets'] == {'0-1': 1, '2': 0, '3': 1, '4': 0, '5+': 1}
    assert curve['early_ramp_le2'] == 1  # Sol Ring only (Cultivate is cmc 3)


def test_deck_curve_empty_pool_is_safe() -> None:
    curve = deck_curve([], cmc_of=lambda _n: 0.0, is_early_ramp=lambda _n: False)
    assert curve['n_spells'] == 0
    assert curve['mean_cmc'] == 0.0
    assert curve['early_ramp_le2'] == 0


def _surface(ramp_n: int, removal_n: int, *, mean_cmc: float, ramp_cedh: bool) -> dict:
    return {
        'degree_axes': {
            'ramp': {'n': ramp_n, 'cedh_level': ramp_cedh},
            'removal': {'n': removal_n},
        },
        'power_profile': {'mean_efficiency': 0.26, 'n_interaction': removal_n, 'removal_mean_mv': 4.0},
        'curve': {'mean_cmc': mean_cmc, 'buckets': {'0-1': 1}, 'early_ramp_le2': 3},
    }


def test_diff_execution_axis_deltas_and_power() -> None:
    a = _surface(24, 14, mean_cmc=3.83, ramp_cedh=True)
    b = _surface(22, 18, mean_cmc=3.52, ramp_cedh=True)
    d = diff_execution(a, b)
    assert isinstance(d, ExecutionDiff)
    by_axis = {ad.axis: ad for ad in d.axis_deltas}
    assert by_axis['ramp'] == AxisDelta(axis='ramp', a_n=24, b_n=22, delta=-2)
    assert by_axis['removal'] == AxisDelta(axis='removal', a_n=14, b_n=18, delta=4)
    assert d.a_mean_cmc == 3.83 and d.b_mean_cmc == 3.52
    assert d.a_ramp_cedh is True and d.b_ramp_cedh is True
    assert d.power['mean_efficiency'] == (0.26, 0.26)


def test_diff_execution_reports_every_axis_in_order() -> None:
    d = diff_execution(_surface(1, 1, mean_cmc=2.0, ramp_cedh=False), _surface(1, 1, mean_cmc=2.0, ramp_cedh=False))
    assert [ad.axis for ad in d.axis_deltas] == list(DEGREE_AXES)
    assert set(d.power) == set(POWER_FIELDS)


def test_diff_execution_missing_axis_delta_is_none() -> None:
    # An axis present on one side but not the other yields a None delta (not a crash).
    a = {'degree_axes': {'ramp': {'n': 5}}, 'power_profile': {}, 'curve': {}}
    b = {'degree_axes': {}, 'power_profile': {}, 'curve': {}}
    d = diff_execution(a, b)
    ramp = next(ad for ad in d.axis_deltas if ad.axis == 'ramp')
    assert ramp.a_n == 5 and ramp.b_n is None and ramp.delta is None
