"""Unit tests for the shared card-set diff (``comparison.card_diff``).

Offline, fixture-free: the core is a pure partition of two keyed mappings, so the
tests drive it directly over small dicts and lock the four-way split, the
order-preservation contract, and the ``is_empty`` convenience.
"""

from __future__ import annotations

from pipeline.transforms.comparison import CardSetDiff, diff_card_sets
from pipeline.transforms.comparison.card_diff import diff_card_sets as diff_card_sets_direct


def test_public_home_re_exports_the_core() -> None:
    # The package __init__ is the advertised home; it must surface the same object.
    assert diff_card_sets is diff_card_sets_direct


def test_four_way_partition() -> None:
    before = {'a': 1, 'b': 2, 'c': 3}
    after = {'b': 2, 'c': 30, 'd': 4}
    d = diff_card_sets(before, after)
    assert d.added == {'d': 4}
    assert d.removed == {'a': 1}
    assert d.changed == {'c': (3, 30)}
    assert d.unchanged == {'b': 2}


def test_added_is_keyed_from_after_removed_from_before() -> None:
    # A card that moved value: `changed` carries (before, after) in that order.
    d = diff_card_sets({'x': 'old'}, {'x': 'new'})
    assert d.changed == {'x': ('old', 'new')}
    assert not d.added and not d.removed and not d.unchanged


def test_identical_sets_are_empty() -> None:
    d = diff_card_sets({'a': 1, 'b': 2}, {'a': 1, 'b': 2})
    assert d.is_empty
    assert d.added == {} and d.removed == {} and d.changed == {}
    assert d.unchanged == {'a': 1, 'b': 2}


def test_is_empty_false_when_anything_moves() -> None:
    assert not diff_card_sets({'a': 1}, {}).is_empty  # removed
    assert not diff_card_sets({}, {'a': 1}).is_empty  # added
    assert not diff_card_sets({'a': 1}, {'a': 2}).is_empty  # changed


def test_order_follows_inputs() -> None:
    # added follows *after* order; removed/unchanged/changed follow *before* order.
    before = {'c': 1, 'a': 1, 'b': 9}
    after = {'z': 0, 'a': 2, 'm': 0}
    d = diff_card_sets(before, after)
    assert list(d.added) == ['z', 'm']  # after order
    assert list(d.removed) == ['c', 'b']  # before order
    assert list(d.changed) == ['a']


def test_presence_only_use_ignores_value() -> None:
    # The drift path keys by name and never reads the value: added/removed keys
    # are a pure set delta regardless of the (differing) row objects behind them.
    before = {'Sol Ring': {'qty': 1}, 'Island': {'qty': 10}}
    after = {'Island': {'qty': 99}, 'Forest': {'qty': 1}}
    d = diff_card_sets(before, after)
    assert set(d.removed) == {'Sol Ring'}
    assert set(d.added) == {'Forest'}


def test_is_a_dataclass_instance() -> None:
    d = diff_card_sets({'a': 1}, {'a': 1})
    assert isinstance(d, CardSetDiff)
