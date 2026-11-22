"""Offline render tests for ``comparison.report`` (the ``compare`` layout).

No lake / resolve: the report is pure formatting over the typed diffs, so these build
minimal diffs and lock the JSON payload shape + the text layout for all four layers,
including the P4 additions (layer ② engine, layer ④ intent). Runs in CI where the
lake-backed reproduction skips.
"""

from __future__ import annotations

from pipeline.transforms.comparison.engine_diff import diff_engine
from pipeline.transforms.comparison.execution_diff import diff_execution
from pipeline.transforms.comparison.identity_diff import diff_identity
from pipeline.transforms.comparison.intent_diff import diff_intent
from pipeline.transforms.comparison.report import comparison_payload, render_text

_A_DUMP = {
    'archetype_identity': {
        'archetype_identity': [{'strategy': 'aristocrats', 'share': 1.0}],
        'ranked_identity': [['aristocrats', 10.0, True]],
    }
}
_B_DUMP = {
    'archetype_identity': {
        'archetype_identity': [{'strategy': 'tokens', 'share': 1.0}],
        'ranked_identity': [['tokens', 8.0, True]],
    }
}
_A_EXEC = {
    'degree_axes': {'ramp': {'n': 5, 'cedh_level': False}},
    'power_profile': {},
    'curve': {'mean_cmc': 3.0, 'buckets': {}},
}
_B_EXEC = {
    'degree_axes': {'ramp': {'n': 3, 'cedh_level': False}},
    'power_profile': {},
    'curve': {'mean_cmc': 2.5, 'buckets': {}},
}

_A_ENGINE = {
    'convergent_event_components': [
        {'events': ['a', 'b'], 'n_events': 2, 'weight': 100.0, 'size': 10, 'attrs': ['sac']}
    ],
    'ranked_units': [
        {
            'event': 'a',
            'mass': 20.0,
            'share': 0.5,
            'formation_mode': 'bipartite',
            'recursive': False,
            'commander_coupled': True,
        }
    ],
    'relay_hubs': ['sac'],
    'commander_loop': {'closes': True, 'reaches_back_to': ['sac']},
    'forward_chains': [['a', 'b']],
    'events': {'a': {'producers': ['sac'], 'consumers': ['death'], 'mass': 20.0, 'kind': 'static-payoff'}},
}
_B_ENGINE = {
    'convergent_event_components': [{'events': ['c'], 'n_events': 1, 'weight': 5.0, 'size': 3, 'attrs': ['tok']}],
    'ranked_units': [
        {
            'event': 'c',
            'mass': 8.0,
            'share': 0.4,
            'formation_mode': 'bipartite',
            'recursive': False,
            'commander_coupled': False,
        }
    ],
    'relay_hubs': [],
    'commander_loop': {'closes': False, 'reaches_back_to': []},
    'forward_chains': [],
    'events': {'c': {'producers': ['tok'], 'consumers': [], 'mass': 8.0, 'kind': 'static-payoff'}},
}


def _intent_surface(engine: dict, cards: list[dict]) -> dict:
    return {'engine': engine, 'card_attributes': cards}


def _payload():
    id_diff = diff_identity(_A_DUMP, _B_DUMP)
    exec_diff = diff_execution(_A_EXEC, _B_EXEC)
    engine_diff = diff_engine(_A_ENGINE, _B_ENGINE, a_name='A', b_name='B')
    a_cards = [{'oracle_id': 'o1', 'name': 'Sac Outlet', 'attrs': ['sac', 'death'], 'mv': 4.0, 'commander': True}]
    intent_diff = diff_intent(
        _intent_surface(_A_ENGINE, a_cards),
        _intent_surface(_B_ENGINE, []),
        a_name='A',
        b_name='B',
    )
    return comparison_payload(
        id_diff, exec_diff, a_name='A', b_name='B', engine_diff=engine_diff, intent_diff=intent_diff
    )


def test_payload_carries_engine_and_intent_sections() -> None:
    p = _payload()
    assert p['engine']['regime_a'] == 'loop' and p['engine']['regime_b'] == 'mass'
    assert p['engine']['a_cycle_events'] == ['a', 'b']
    assert p['engine']['a_loop_closes'] is True
    assert p['engine']['a_certified_infinite'] is False
    assert any(u['event'] == 'a' for u in p['engine']['unit_deltas'])
    # Intent: the sac event diverges (A invests, B does not) with the signature card named.
    sac = next(e for e in p['intent']['divergent_events'] if e['event'] == 'a')
    assert sac['leader'] == 'A'
    assert sac['a_signature'][0]['name'] == 'Sac Outlet'
    assert sac['a_signature'][0]['oracle_id'] == 'o1'
    assert sac['a_high_mv'][0]['name'] == 'Sac Outlet'  # MV 4 -> on the bench


def test_default_payload_omits_engine_intent_when_not_passed() -> None:
    # Backward-compatible: without the new diffs the payload has no engine/intent keys.
    id_diff = diff_identity(_A_DUMP, _B_DUMP)
    exec_diff = diff_execution(_A_EXEC, _B_EXEC)
    p = comparison_payload(id_diff, exec_diff, a_name='A', b_name='B')
    assert 'engine' not in p and 'intent' not in p


def test_render_text_includes_all_four_layers() -> None:
    text = render_text(_payload())
    assert 'IDENTITY (layer 1)' in text
    assert 'ENGINE (layer 2)' in text
    assert 'EXECUTION (layer 3)' in text
    assert 'INTENT (layer 4)' in text
    # The regime-switched reading and the honest grind read are in the engine section.
    assert 'regime: A=loop  B=mass' in text
    assert 'value grind that closes, not a certified infinite' in text
    # The intent section names the signature card.
    assert 'Sac Outlet' in text
