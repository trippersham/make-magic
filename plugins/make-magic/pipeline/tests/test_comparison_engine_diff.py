"""Unit tests for layer ② engine diff (``comparison.engine_diff``).

Offline + fixture-free: the diff reads hand-built engine surfaces (the normalized
``facts['engines']`` shape), so no lake / resolve enters the test. The regime switch,
the convergent-cycle set algebra, the commander-loop + relay-hub reads, the ranked-unit
deltas, and the honest ``recursive:false`` grind read are all locked here.
"""

from __future__ import annotations

from pipeline.transforms.comparison import EngineDiff, classify_regime, diff_engine
from pipeline.transforms.comparison.engine_diff import (
    REGIME_LOOP,
    REGIME_MASS,
    heaviest_cycle,
    is_certified_infinite,
    unit_deltas,
)
from pipeline.transforms.comparison.engine_diff import (
    diff_engine as diff_engine_direct,
)


def _loop_engine() -> dict:
    """A grind/loop-regime surface: a multi-event convergent cycle + closing commander loop."""
    return {
        'convergent_event_components': [
            {
                'events': ['land-enters-battlefield', 'permanent-sacrificed'],
                'n_events': 2,
                'weight': 388.0,
                'attrs': ['sac'],
            },
            {'events': ['monarch'], 'n_events': 1, 'weight': 5.0, 'attrs': ['monarch-maker']},
        ],
        'ranked_units': [
            {
                'event': 'land-enters-battlefield',
                'mass': 174.0,
                'share': 0.284,
                'formation_mode': 'bipartite',
                'recursive': False,
                'commander_coupled': True,
            },
            {
                'event': 'permanent-sacrificed',
                'mass': 105.0,
                'share': 0.17,
                'formation_mode': 'bipartite',
                'recursive': False,
                'commander_coupled': False,
            },
        ],
        'relay_hubs': ['sac', 'recursion-land'],
        'commander_loop': {'closes': True, 'reaches_back_to': ['recursion-land', 'reanimate-land']},
        'forward_chains': [['permanent-sacrificed', 'land-enters-battlefield']],
        'events': {
            'land-enters-battlefield': {
                'producers': ['recursion-land'],
                'consumers': ['landfall'],
                'mass': 174.0,
                'kind': 'static-payoff',
            },
            'permanent-sacrificed': {
                'producers': ['sac'],
                'consumers': ['sacrifice-matters'],
                'mass': 105.0,
                'kind': 'static-payoff',
            },
        },
    }


def _mass_engine() -> dict:
    """A goodstuff/mass-regime surface: only single-event components, bipartite units."""
    return {
        'convergent_event_components': [
            {'events': ['creature-attacks'], 'n_events': 1, 'weight': 60.0, 'attrs': ['aggro']}
        ],
        'ranked_units': [
            {
                'event': 'creature-attacks',
                'mass': 73.0,
                'share': 0.3,
                'formation_mode': 'bipartite',
                'recursive': False,
                'commander_coupled': False,
            },
            {
                'event': 'land-enters-battlefield',
                'mass': 40.0,
                'share': 0.16,
                'formation_mode': 'bipartite',
                'recursive': False,
                'commander_coupled': True,
            },
        ],
        'relay_hubs': ['recursion-land'],
        'commander_loop': {'closes': False, 'reaches_back_to': []},
        'forward_chains': [],
        'events': {
            'creature-attacks': {
                'producers': ['aggro'],
                'consumers': ['combat'],
                'mass': 73.0,
                'kind': 'static-payoff',
            },
            'land-enters-battlefield': {
                'producers': ['landfall'],
                'consumers': [],
                'mass': 40.0,
                'kind': 'static-payoff',
            },
        },
    }


def test_public_home_re_exports_the_core() -> None:
    assert diff_engine is diff_engine_direct


def test_heaviest_cycle_requires_two_events() -> None:
    assert heaviest_cycle(_loop_engine())['n_events'] == 2  # the multi-event SCC, not the monarch singleton
    assert heaviest_cycle(_mass_engine()) is None  # only single-event components


def test_classify_regime_loop_vs_mass() -> None:
    assert classify_regime(_loop_engine()) == REGIME_LOOP
    assert classify_regime(_mass_engine()) == REGIME_MASS
    assert classify_regime({}) == REGIME_MASS  # empty surface is mass (no cycle)


def test_grind_engine_reads_as_convergent_plus_closing_not_certified_infinite() -> None:
    # wr-framework-admission.md: a grind loop is a convergent component + closing commander
    # loop, with every unit recursive:false — NOT a certified-infinite flag (#90 deferred).
    loop = _loop_engine()
    assert classify_regime(loop) == REGIME_LOOP
    assert loop['commander_loop']['closes'] is True
    assert is_certified_infinite(loop) is False
    d = diff_engine(loop, _mass_engine(), a_name='WR', b_name='precon')
    assert d.a_certified_infinite is False
    assert 'value grind that closes, not a certified infinite' in d.reading
    assert 'convergent cycle over {land-enters-battlefield, permanent-sacrificed}' in d.reading


def test_diff_engine_regime_shift_and_cycle_set_algebra() -> None:
    d = diff_engine(_loop_engine(), _mass_engine(), a_name='A', b_name='B')
    assert isinstance(d, EngineDiff)
    assert (d.regime_a, d.regime_b) == (REGIME_LOOP, REGIME_MASS)
    assert d.a_cycle_events == ['land-enters-battlefield', 'permanent-sacrificed']
    assert d.b_cycle_events == []  # mass regime has no multi-event cycle
    assert d.a_cycle_weight == 388.0
    assert d.dropped_cycle_events == ['land-enters-battlefield', 'permanent-sacrificed']
    assert d.a_loop_closes is True and d.b_loop_closes is False
    assert d.a_reaches_back == ['recursion-land', 'reanimate-land']
    assert 'recursion-land' in d.shared_relay_hubs  # shared across both hub lists


def test_unit_deltas_mass_and_tags() -> None:
    a = {
        'ranked_units': [
            {
                'event': 'e1',
                'mass': 10.0,
                'share': 0.5,
                'formation_mode': 'bipartite',
                'recursive': False,
                'commander_coupled': True,
            },
            {
                'event': 'only_a',
                'mass': 4.0,
                'share': 0.2,
                'formation_mode': 'bipartite',
                'recursive': False,
                'commander_coupled': False,
            },
        ]
    }
    b = {
        'ranked_units': [
            {
                'event': 'e1',
                'mass': 25.0,
                'share': 0.6,
                'formation_mode': 'terminal-producer',
                'recursive': False,
                'commander_coupled': True,
            },
            {
                'event': 'only_b',
                'mass': 7.0,
                'share': 0.3,
                'formation_mode': 'bipartite',
                'recursive': False,
                'commander_coupled': False,
            },
        ]
    }
    deltas = {u.event: u for u in unit_deltas(a, b)}
    assert deltas['e1'].delta_mass == 15.0
    assert deltas['e1'].tag == 'shifted'  # formation mode moved bipartite -> terminal-producer
    assert deltas['only_a'].tag == 'only_A' and deltas['only_a'].b_mass is None
    assert deltas['only_b'].tag == 'only_B' and deltas['only_b'].a_mass is None
    # Heaviest combined mass first.
    assert unit_deltas(a, b)[0].event == 'e1'


def test_shared_regime_reading_names_both_decks() -> None:
    d = diff_engine(_mass_engine(), _mass_engine(), a_name='X', b_name='Y')
    assert d.regime_a == d.regime_b == REGIME_MASS
    assert 'both decks are in the mass regime' in d.reading
    assert 'X: bipartite payoff mass' in d.reading and 'Y: bipartite payoff mass' in d.reading


def test_empty_surfaces_are_safe() -> None:
    d = diff_engine({}, {})
    assert d.regime_a == d.regime_b == REGIME_MASS
    assert d.unit_deltas == []
    assert d.a_cycle_events == [] and d.b_cycle_events == []
