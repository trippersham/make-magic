"""Tests for Phase-3 candidate generation (demand-set query → retrieval → facts).

The default suite is OFFLINE: every test injects
:func:`~pipeline.transforms.strategy.engine_hydration.make_fixture_search_client`
over a small synthetic card set, so no lake and no network are touched. Anchor
enumeration still runs the real strategy engine over the committed golden decks
(the ``load_deck`` fixture), so the asserted anchors are the real ones the
primitive attaches to. A single real-lake end-to-end test is marked
``integration`` and is deselected by the default suite.
"""

from __future__ import annotations

import pytest

from pipeline.transforms.recommend import generate as generate_mod
from pipeline.transforms.recommend.anchors import Anchor, enumerate_anchors
from pipeline.transforms.recommend.generate import (
    attribute_query,
    demand_set_query,
    generate_candidates,
)
from pipeline.transforms.strategy.engine_hydration import make_fixture_search_client

from .conftest import LAKE_AVAILABLE

WORLD_RECLAIMER = 'owned__world-reclaimer'


def _producer_anchor(deck: dict) -> Anchor:
    """The land-into-play producer side — the worked-example complement anchor."""
    hits = [
        a
        for a in enumerate_anchors(deck)
        if a.kind == 'event_side' and a.ref == 'land-enters-battlefield' and a.direction == 'producer'
    ]
    assert len(hits) == 1
    return hits[0]


def _candidate(result, oracle_id: str):
    hits = [c for c in result.candidates if c.card.oracle_id == oracle_id]
    return hits[0] if hits else None


# --------------------------------------------------------------------------- #
# attribute_query — the three-tier name→query resolver.
# --------------------------------------------------------------------------- #


def test_attribute_query_resolves_catalog_bucket() -> None:
    # A curated card_attributes entry resolves to its authored OR-of-otag query.
    q = attribute_query('removal')
    assert 'otag:removal' in q


def test_attribute_query_resolves_synth_card_field_query() -> None:
    # A synth attribute (no otag) resolves to its card-field query, not `otag:`.
    q = attribute_query('color-fixing')
    assert not q.startswith('otag:color-fixing')
    assert 'otag:' in q or 'oracle:' in q


def test_attribute_query_falls_back_to_raw_otag_slug() -> None:
    # A bare node otag (event-anchor / enchantment / EDHREC slug) → exact membership.
    assert attribute_query('death-trigger') == 'otag:death-trigger'


def test_attribute_query_strips_edge_role_suffix() -> None:
    # The `[enabler]` role tag is graph-side only; the base attribute carries the query.
    assert attribute_query('tutor-land[enabler]') == attribute_query('tutor-land')


def test_attribute_query_preserves_subtype_concentration_argument() -> None:
    # `subtype-concentration[Elf]` is a PARAMETERISED family: the bracket is the tribe
    # (a semantic argument), not a role tag. It must resolve to an argument-aware query
    # that targets Elf, never collapse to a bare `otag:subtype-concentration` that drops
    # the tribe (#119 C2). Membership mirrors the engine's `type:<Subtype>` rule.
    q = attribute_query('subtype-concentration[Elf]')
    assert 'type:Elf' in q
    assert 'subtype-concentration' not in q  # the family name is never a query slug


def test_attribute_query_subtype_concentration_multiple_tribes_or_combined() -> None:
    # Multiple dominant tribes OR together, one `type:<Subtype>` per tribe.
    q = attribute_query('subtype-concentration[Elf,Goblin]')
    assert 'type:Elf' in q
    assert 'type:Goblin' in q
    assert ' OR ' in q


# --------------------------------------------------------------------------- #
# Demand-set query — built from the catalog over the anchor's matched attrs.
# --------------------------------------------------------------------------- #


def test_demand_set_query_is_or_of_catalog_queries(load_deck) -> None:
    anchor = _producer_anchor(load_deck(WORLD_RECLAIMER))
    query = demand_set_query(anchor)

    # Every matched attribute contributes its resolved membership query, parenthesised.
    for attr in anchor.matched_attributes:
        assert f'({attribute_query(attr)})' in query
    # Combined with OR when the anchor has more than one demand attribute.
    if len({attribute_query(a) for a in anchor.matched_attributes}) > 1:
        assert ' OR ' in query


# --------------------------------------------------------------------------- #
# The core generation chain — one complement producer-side anchor.
# --------------------------------------------------------------------------- #


def test_producer_candidate_carries_correct_sgm_and_after_delta(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    anchor = _producer_anchor(deck)

    cards = [
        {
            'oracle_id': 'oid-recur',
            'name': 'Fixture Recursion',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],  # within the deck's B/G/R identity
        }
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))

    cand = _candidate(result, 'oid-recur')
    assert cand is not None
    assert cand.rationale is None  # no judgment in Phase 3

    producer_sgm = [s for s in cand.anchors if s.target_ref == 'land-enters-battlefield' and s.direction == 'producer']
    assert len(producer_sgm) == 1
    sgm = producer_sgm[0]
    assert sgm.role == 'anchored_at'
    assert sgm.target_kind == 'event_side'
    assert sgm.direction == 'producer'
    assert sgm.proposed_membership_ref == f'{result.target_ref}:oid-recur'

    snap = sgm.fact_snapshot
    assert snap.matched_attributes == ['recursion-land']  # the attribute THIS card carried
    assert snap.path == ['candidate', 'land-enters-battlefield/producer', 'land-enters-battlefield/consumer']
    # The before state is the anchor's; the card joins the thin side → +1 support,
    # connectivity unchanged in singleton.
    assert snap.support_before == anchor.support
    assert snap.support_after == anchor.support + 1
    assert snap.connectivity_before == anchor.connectivity
    assert snap.connectivity_after == anchor.connectivity


def test_already_in_deck_card_is_excluded(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    in_deck_oid = deck['cards'][0]['oracle_id']

    cards = [
        {
            'oracle_id': in_deck_oid,  # already a member of the deck
            'name': 'Already Here',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        }
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))
    assert _candidate(result, in_deck_oid) is None


def test_out_of_identity_card_is_excluded(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)  # B/G/R
    cards = [
        {
            'oracle_id': 'oid-blue',
            'name': 'Blue Interloper',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['U'],  # outside B/G/R
        }
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))
    assert _candidate(result, 'oid-blue') is None


def test_colorless_candidate_is_admitted(load_deck) -> None:
    # An empty color_identity is a decidable subset of any deck identity, so a
    # colorless card is kept (distinct from a missing field, which is undecidable).
    deck = load_deck(WORLD_RECLAIMER)
    cards = [
        {
            'oracle_id': 'oid-artifact',
            'name': 'Colorless Rock',
            'otags': ['recursion-land'],  # on the land-producer demand, so it is retrieved
            'type_line': 'Artifact',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': [],
        }
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))
    assert _candidate(result, 'oid-artifact') is not None


# --------------------------------------------------------------------------- #
# Dedupe — one card filling many anchors is ONE candidate with many anchors.
# --------------------------------------------------------------------------- #


def test_card_filling_multiple_anchors_dedupes_to_one_candidate(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    # `recursion-land` is both a card_attribute node AND a producer-side attribute
    # of the land event, so a single card fills several distinct anchors.
    cards = [
        {
            'oracle_id': 'oid-multi',
            'name': 'Multi Anchor',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        }
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))

    multi = [c for c in result.candidates if c.card.oracle_id == 'oid-multi']
    assert len(multi) == 1  # exactly one Candidate, not one per anchor
    cand = multi[0]
    assert len(cand.anchors) >= 2
    # The anchors are distinct instances, all for this one card.
    keys = {(s.target_kind, s.target_ref, s.direction) for s in cand.anchors}
    assert len(keys) == len(cand.anchors)
    assert {'card_attribute', 'event_side'} <= {s.target_kind for s in cand.anchors}


# --------------------------------------------------------------------------- #
# Membership tags — only when a store lookup is injected.
# --------------------------------------------------------------------------- #


def test_membership_tags_absent_without_lookups(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [
        {
            'oracle_id': 'oid-recur',
            'name': 'Fixture Recursion',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        }
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))
    cand = _candidate(result, 'oid-recur')
    assert cand is not None
    assert cand.membership_tags == []


def test_membership_tags_set_when_lookups_injected(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [
        {
            'oracle_id': 'oid-recur',
            'name': 'Fixture Recursion',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        }
    ]
    result = generate_candidates(
        deck,
        search_client=make_fixture_search_client(cards),
        inventory_lookup=lambda oid: oid == 'oid-recur',
        chase_lookup=lambda oid: False,
    )
    cand = _candidate(result, 'oid-recur')
    assert cand is not None
    assert cand.membership_tags == ['inventory']


# --------------------------------------------------------------------------- #
# Ordering — the transparent degraded structural fallback.
# --------------------------------------------------------------------------- #


def test_ordering_is_labelled_degraded_structural(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [
        {
            'oracle_id': 'oid-recur',
            'name': 'Fixture Recursion',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        }
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))
    assert result.ordering == 'degraded_structural'
    assert any('degraded_structural' in n for n in result.notes)


def test_more_anchors_rank_ahead_of_fewer(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [
        {
            'oracle_id': 'oid-many',
            'name': 'AAA Many',  # name would sort first; ranking must not be by name
            'otags': ['recursion-land'],  # fills several anchors
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        },
        {
            'oracle_id': 'oid-few',
            'name': 'ZZZ Few',
            'otags': ['mill'],  # on a single complement anchor — fewer anchors filled
            'type_line': 'Creature',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        },
    ]
    result = generate_candidates(deck, search_client=make_fixture_search_client(cards))
    many = _candidate(result, 'oid-many')
    few = _candidate(result, 'oid-few')
    assert many is not None and few is not None
    if len(many.anchors) != len(few.anchors):
        order = [c.card.oracle_id for c in result.candidates]
        assert order.index('oid-many') < order.index('oid-few')


# --------------------------------------------------------------------------- #
# Lake precondition — the default path fails LOUD, never silently empty.
# --------------------------------------------------------------------------- #


def test_lake_absent_on_default_path_fails_loud(load_deck, monkeypatch) -> None:
    def _absent_client():
        def search(_q: str) -> list[dict]:
            raise FileNotFoundError('Lake data not found')

        return search

    # Patch the default client factory the module reaches for when none is injected.
    monkeypatch.setattr(generate_mod, 'make_lake_search_client', _absent_client)

    with pytest.raises(RuntimeError, match='hydrate-lake'):
        generate_candidates(load_deck(WORLD_RECLAIMER))  # no search_client → default path


def test_injected_client_errors_propagate_unwrapped(load_deck) -> None:
    # A caller-injected client is not the default lake path, so its own errors are
    # not reinterpreted as a lake-precondition failure.
    def _boom(_q: str) -> list[dict]:
        raise FileNotFoundError('fixture-specific IO error')

    with pytest.raises(FileNotFoundError, match='fixture-specific'):
        generate_candidates(load_deck(WORLD_RECLAIMER), search_client=_boom)


# --------------------------------------------------------------------------- #
# Real-lake end-to-end — OPTIONAL, integration-only (deselected by default).
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.skipif(not LAKE_AVAILABLE, reason='requires a hydrated lake')
def test_real_lake_surfaces_land_producers(load_deck) -> None:
    # Against the real ~40k-card lake, the payoff-rich / producer-thin landfall deck
    # surfaces producer-side candidates anchored at the land event.
    result = generate_candidates(load_deck(WORLD_RECLAIMER))
    assert result.ordering == 'degraded_structural'
    assert result.candidates
    producer_anchored = [
        c
        for c in result.candidates
        if any(s.target_ref == 'land-enters-battlefield' and s.direction == 'producer' for s in c.anchors)
    ]
    assert producer_anchored
