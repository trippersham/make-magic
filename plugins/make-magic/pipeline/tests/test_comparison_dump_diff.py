"""Unit tests for the shared dump diff (``comparison.dump_diff``).

Offline, synthetic dumps (no lake, no DuckDB). These lock the public home of the
re-homed differ: the three-class classifier, the exit-code ladder, the detail
extractor, and that the reproducibility shim re-exports the identical objects.
"""

from __future__ import annotations

from pipeline.transforms.comparison import dump_diff as dd
from pipeline.transforms.strategy.tests.repro import semantic_diff as sd


def _dump(decks: dict) -> dict:
    return {'meta': {'created_at': 'ignored', 'composites': {}}, 'decks': decks}


def _deck(**layers: object) -> dict:
    base = {
        'attributes': [],
        'events': {},
        'eventeffects': [],
        'archetype_identity': {},
        'engines': {},
        'pool': [],
    }
    base.update(layers)
    return base


def test_repro_shim_re_exports_the_same_objects() -> None:
    # The repro module is a thin re-export of the shared home.
    assert sd.diff_dumps is dd.diff_dumps
    assert sd.classify_layer is dd.classify_layer
    assert sd.layer_detail is dd.layer_detail
    assert sd.exit_code is dd.exit_code
    assert sd.render_report is dd.render_report
    assert sd.LAYERS is dd.LAYERS


def test_layers_catalog() -> None:
    assert dd.LAYERS == ('attributes', 'events', 'eventeffects', 'archetype_identity', 'engines', 'pool')


def test_classify_identical_is_none() -> None:
    assert dd.classify_layer('attributes', ['a', 'b'], ['a', 'b']) is None


def test_classify_benign_is_order_only() -> None:
    # attributes is an unordered set layer: a reorder is a normalization gap.
    assert dd.classify_layer('attributes', ['a', 'b'], ['b', 'a']) == dd.BENIGN


def test_classify_substantive_on_content_change() -> None:
    assert dd.classify_layer('attributes', ['a', 'b'], ['a', 'c']) == dd.SUBSTANTIVE


def test_classify_pool_change_is_card_universe() -> None:
    assert dd.classify_layer('pool', [['A', '1']], [['A', '1'], ['B', '2']]) == dd.CARD_UNIVERSE


def test_identical_dumps_exit_zero() -> None:
    d = _dump({'Deck': _deck(attributes=['a'])})
    diff = dd.diff_dumps(d, d)
    assert dd.exit_code(diff) == 0
    assert diff['decks_moved'] == 0


def test_substantive_exit_two() -> None:
    a = _dump({'Deck': _deck(attributes=['a'])})
    b = _dump({'Deck': _deck(attributes=['b'])})
    assert dd.exit_code(dd.diff_dumps(a, b)) == 2


def test_appearing_deck_is_card_universe_exit_three() -> None:
    a = _dump({'Deck': _deck()})
    b = _dump({'Deck': _deck(), 'New': _deck()})
    diff = dd.diff_dumps(a, b)
    assert dd.exit_code(diff) == 3
    assert diff['decks_only_in_b'] == ['New']


def test_layer_detail_dict_split() -> None:
    detail = dd.layer_detail('engines', {'x': 1, 'y': 2}, {'y': 3, 'z': 4})
    assert detail['added'] == ['z']
    assert detail['removed'] == ['x']
    assert detail['changed'] == {'y': {'before': 2, 'after': 3}}


def test_layer_detail_list_membership() -> None:
    detail = dd.layer_detail('eventeffects', [['a', 'b']], [['a', 'b'], ['c', 'd']])
    assert detail['added'] == [['c', 'd']]
    assert detail['removed'] == []


def test_render_report_is_text_with_verdict() -> None:
    a = _dump({'Deck': _deck(attributes=['a'])})
    b = _dump({'Deck': _deck(attributes=['b'])})
    report = dd.render_report(dd.diff_dumps(a, b))
    assert '# Semantic dump diff' in report
    assert 'SUBSTANTIVE' in report
