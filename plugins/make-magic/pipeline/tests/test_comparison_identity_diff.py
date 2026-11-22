"""Unit tests for layer ① identity diff (``comparison.identity_diff``).

Offline + fixture-free: the vectors read a small hand-built ``archetype_identity``
layer, so the tests lock the vector extraction, cosine / L1, the added/dropped set,
and the ranked reweight gap ordering directly.
"""

from __future__ import annotations

import math

from pipeline.transforms.comparison import IdentityDiff, diff_identity
from pipeline.transforms.comparison.identity_diff import (
    cosine,
    identity_ranked_vector,
    identity_share_vector,
    l1,
    ranked_gap,
)
from pipeline.transforms.comparison.identity_diff import diff_identity as diff_identity_direct


def _dump(identity: list[tuple[str, float, float]], ranked: list[tuple[str, float]]) -> dict:
    """A minimal semantic dump carrying just the archetype_identity layer."""
    return {
        'archetype_identity': {
            'archetype_identity': [{'strategy': s, 'weighted_quantity': wq, 'share': sh} for s, wq, sh in identity],
            'ranked_identity': [[s, wq, False] for s, wq in ranked],
        }
    }


def test_public_home_re_exports_the_core() -> None:
    assert diff_identity is diff_identity_direct


def test_share_and_ranked_vectors_read_their_layers() -> None:
    dump = _dump([('landfall', 47.0, 0.4)], [('landfall', 47.0), ('lands-matter', 39.0)])
    assert identity_share_vector(dump) == {'landfall': 0.4}
    assert identity_ranked_vector(dump) == {'landfall': 47.0, 'lands-matter': 39.0}


def test_cosine_identical_is_one_empty_is_zero() -> None:
    assert cosine({'a': 1.0, 'b': 2.0}, {'a': 1.0, 'b': 2.0}) == 1.0
    assert cosine({}, {'a': 1.0}) == 0.0
    assert cosine({'a': 1.0}, {}) == 0.0


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine({'a': 1.0}, {'b': 1.0}) == 0.0


def test_l1_is_absolute_share_movement() -> None:
    assert l1({'a': 0.5, 'b': 0.5}, {'a': 0.4, 'b': 0.6}) == 0.2
    assert l1({'a': 1.0}, {'b': 1.0}) == 2.0


def test_ranked_gap_is_ordered_by_abs_delta() -> None:
    a = _dump([], [('x', 10.0), ('y', 5.0), ('z', 1.0)])
    b = _dump([], [('x', 12.0), ('y', 20.0), ('w', 3.0)])
    gap = ranked_gap(a, b)
    # y moved +15 (biggest), then w +3 / z -1 / x +2 — ordered by |delta|.
    assert gap[0] == ('y', 5.0, 20.0, 15.0)
    deltas = [abs(row[3]) for row in gap]
    assert deltas == sorted(deltas, reverse=True)
    # a strategy only in A reads as a drop to 0; only in B as a gain from 0.
    assert ('z', 1.0, 0.0, -1.0) in gap
    assert ('w', 0.0, 3.0, 3.0) in gap


def test_diff_identity_surfaces_added_dropped_and_similarity() -> None:
    a = _dump([('landfall', 47.0, 0.6), ('lands-matter', 39.0, 0.4)], [('landfall', 47.0), ('lands-matter', 39.0)])
    b = _dump([('landfall', 43.0, 0.7), ('aristocrats', 20.0, 0.3)], [('landfall', 43.0), ('aristocrats', 20.0)])
    d = diff_identity(a, b)
    assert isinstance(d, IdentityDiff)
    assert d.a_identity == ['landfall', 'lands-matter']
    assert d.b_identity == ['landfall', 'aristocrats']
    assert d.added_in_b == ['aristocrats']
    assert d.dropped_in_b == ['lands-matter']
    assert 0.0 <= d.cosine_share <= 1.0
    assert d.l1_share > 0.0


def test_diff_identity_identical_decks_are_maximally_similar() -> None:
    a = _dump([('landfall', 47.0, 0.55), ('lands-matter', 39.0, 0.45)], [('landfall', 47.0), ('lands-matter', 39.0)])
    d = diff_identity(a, a)
    assert d.added_in_b == [] and d.dropped_in_b == []
    assert d.cosine_share == 1.0
    assert d.cosine_ranked == 1.0
    assert d.l1_share == 0.0
    assert all(delta == 0.0 for *_rest, delta in d.ranked_gap)


def test_cosine_matches_hand_computation() -> None:
    a = {'x': 3.0, 'y': 4.0}
    b = {'x': 4.0, 'y': 3.0}
    expected = round((3 * 4 + 4 * 3) / (math.hypot(3, 4) * math.hypot(4, 3)), 3)
    assert cosine(a, b) == expected
