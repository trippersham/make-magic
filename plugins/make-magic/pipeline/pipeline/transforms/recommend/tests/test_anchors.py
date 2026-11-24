"""Tests for Phase-2 anchor enumeration + saturation.

Assert the deck-side facts the recommendation primitive builds on: the four v1
structural anchor kinds, the ``complement`` / ``augment`` saturation verdict (with
the landfall worked example), that enumeration is read-only over the strategy
engine (classification stays byte-stable), and the per-anchor
:class:`FactSnapshot` projection. Everything runs offline on the committed golden
decks — no lake, no network.
"""

from __future__ import annotations

import json

from pipeline.transforms.recommend.anchors import (
    DEFAULT_SATURATION,
    V1_ANCHOR_KINDS,
    Anchor,
    SaturationConfig,
    classify_saturation,
    enumerate_anchors,
)
from pipeline.transforms.strategy.resolve import resolve

WORLD_RECLAIMER = 'owned__world-reclaimer'


def _by(anchors: list[Anchor], *, kind: str, ref: str, direction: str | None) -> Anchor:
    hits = [a for a in anchors if a.kind == kind and a.ref == ref and a.direction == direction]
    assert len(hits) == 1, f'expected exactly one {kind}:{ref}:{direction}, got {len(hits)}'
    return hits[0]


# --------------------------------------------------------------------------- #
# Saturation metric — pure unit tests of the verdict.
# --------------------------------------------------------------------------- #


def test_thin_side_with_rich_counterpart_is_complement() -> None:
    assert classify_saturation(support=2, connectivity=10) == 'complement'


def test_well_supplied_side_is_augment() -> None:
    assert classify_saturation(support=10, connectivity=2) == 'augment'


def test_equal_sides_are_augment() -> None:
    # support == connectivity is not the thinner half, so it deepens (augment).
    assert classify_saturation(support=5, connectivity=5) == 'augment'


def test_thin_side_with_insubstantial_counterpart_is_augment() -> None:
    # connectivity below the presence floor: no rich counterpart to complete.
    assert classify_saturation(support=1, connectivity=2) == 'augment'


def test_complement_ratio_t_tightens_the_verdict() -> None:
    # support 6 vs connectivity 10: thin at t=1.0, not thin at a stricter t=0.5.
    assert classify_saturation(6, 10, SaturationConfig(complement_ratio_t=1.0)) == 'complement'
    assert classify_saturation(6, 10, SaturationConfig(complement_ratio_t=0.5)) == 'augment'


# --------------------------------------------------------------------------- #
# Worked example — the payoff-rich / producer-thin landfall engine.
# --------------------------------------------------------------------------- #


def test_land_enters_producer_side_is_a_complement_anchor(load_deck) -> None:
    anchors = enumerate_anchors(load_deck(WORLD_RECLAIMER))
    producer = _by(anchors, kind='event_side', ref='land-enters-battlefield', direction='producer')
    consumer = _by(anchors, kind='event_side', ref='land-enters-battlefield', direction='consumer')

    # The deck is rich in landfall PAYOFFS but thin on cards that actually put
    # lands into play: the producer side is the thin half of a lopsided edge.
    assert producer.support > 0
    assert producer.connectivity >= DEFAULT_SATURATION.substantive_side_floor
    assert producer.support < producer.connectivity
    assert producer.mode == 'complement'

    # Its payoff (consumer) side is the rich half: augment, not complement.
    assert consumer.support == producer.connectivity
    assert consumer.connectivity == producer.support
    assert consumer.mode == 'augment'

    # The matched demand set is the present producer attributes (the Phase-3 query
    # is built from these); the directed path names the event edge.
    assert 'recursion-land' in producer.matched_attributes
    assert producer.path == ('land-enters-battlefield/producer', 'land-enters-battlefield/consumer')


# --------------------------------------------------------------------------- #
# v1 anchor-kind closure — exactly the four structural kinds.
# --------------------------------------------------------------------------- #


def test_only_the_four_v1_kinds_are_emitted(load_deck) -> None:
    anchors = enumerate_anchors(load_deck(WORLD_RECLAIMER))
    kinds = {a.kind for a in anchors}

    assert kinds <= V1_ANCHOR_KINDS
    # a rich multi-engine deck exercises all four
    assert kinds == {'event_side', 'engine_slot', 'card_attribute', 'strategy'}
    # the deferred kinds never appear
    assert 'combo' not in kinds
    assert 'degree_axis' not in kinds


def test_directions_match_their_kind(load_deck) -> None:
    anchors = enumerate_anchors(load_deck(WORLD_RECLAIMER))
    for a in anchors:
        if a.kind == 'event_side':
            assert a.direction in {'producer', 'consumer'}
        elif a.kind == 'engine_slot':
            assert a.direction in {'hub', 'payoff'}
        else:  # card_attribute / strategy are undirected node anchors
            assert a.direction is None


def test_land_enters_engine_has_a_hub_slot(load_deck) -> None:
    # The elected land engine is relay-driven (hub = recursion-land), so its
    # engine_slot anchors carry a hub side distinct from the raw event sides.
    anchors = enumerate_anchors(load_deck(WORLD_RECLAIMER))
    hub = _by(anchors, kind='engine_slot', ref='land-enters-battlefield', direction='hub')
    assert hub.matched_attributes == ('recursion-land',)
    assert hub.support > 0


# --------------------------------------------------------------------------- #
# Read-only consumption — classification is byte-stable across enumeration.
# --------------------------------------------------------------------------- #


def test_enumeration_does_not_change_classification(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    before = json.dumps(resolve(deck), sort_keys=True)
    enumerate_anchors(deck)
    after = json.dumps(resolve(deck), sort_keys=True)
    assert before == after


def test_archetype_identity_unchanged_by_enumeration(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    identity_before = resolve(deck).get('archetype_identity')
    enumerate_anchors(deck)
    assert resolve(deck).get('archetype_identity') == identity_before


def test_enumeration_is_deterministic(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    first = enumerate_anchors(deck)
    second = enumerate_anchors(deck)
    assert first == second


# --------------------------------------------------------------------------- #
# FactSnapshot projection — the deck-side "before" state.
# --------------------------------------------------------------------------- #


def test_anchor_projects_before_state_onto_fact_snapshot(load_deck) -> None:
    anchors = enumerate_anchors(load_deck(WORLD_RECLAIMER))
    producer = _by(anchors, kind='event_side', ref='land-enters-battlefield', direction='producer')
    snap = producer.to_fact_snapshot()

    assert snap.support_before == producer.support
    assert snap.connectivity_before == producer.connectivity
    assert snap.matched_attributes == list(producer.matched_attributes)
    assert snap.path == list(producer.path)
    # the candidate-add "after" delta is Phase 3's job, so it stays zero here
    assert snap.support_after == 0
    assert snap.connectivity_after == 0


def test_every_anchor_has_a_mode_and_nonneg_counts(load_deck) -> None:
    anchors = enumerate_anchors(load_deck(WORLD_RECLAIMER))
    assert anchors  # the fixture is rich enough to produce anchors
    for a in anchors:
        assert a.mode in {'complement', 'augment'}
        assert a.support >= 0
        assert a.connectivity >= 0
        assert a.matched_attributes  # every anchor names its demand set
