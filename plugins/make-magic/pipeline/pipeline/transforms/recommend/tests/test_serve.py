"""Tests for Phase-4b serving (the ``recommend()`` composition + MCP schema).

The default suite is OFFLINE: every test injects a fixture ``SearchClient`` over a
small synthetic card set and a fixture ``EnrichFn`` over a static facts map, so no
lake and no network are touched (local-first rule). Anchor enumeration still runs
the real strategy engine over the committed golden decks (the ``load_deck``
fixture), so the composed generate→judge chain is the real one. A single real-lake
end-to-end test is marked ``integration`` and deselected by the default suite.
"""

from __future__ import annotations

import pytest

from pipeline.transforms.recommend.contracts import RecommendationResult
from pipeline.transforms.recommend.serve import recommend, recommendation_schema
from pipeline.transforms.strategy.engine_hydration import make_fixture_search_client

from .conftest import LAKE_AVAILABLE

WORLD_RECLAIMER = 'owned__world-reclaimer'


def _card(oracle_id: str, name: str, otags: list[str], ci: list[str]) -> dict:
    """A synthetic retrieval row for the fixture search client."""
    return {
        'oracle_id': oracle_id,
        'name': name,
        'otags': otags,
        'type_line': 'Sorcery',
        'oracle_text': '',
        'mana_cost': '',
        'color_identity': ci,
    }


def _enricher(mapping: dict[str, dict]):
    """A fixture ``EnrichFn`` over a static ``oracle_id -> facts`` map (offline)."""

    def enrich(oracle_ids: list[str]) -> dict[str, dict]:
        return {oid: mapping[oid] for oid in oracle_ids if oid in mapping}

    return enrich


def _accepted_id(result: RecommendationResult, oracle_id: str):
    hits = [c for c in result.candidates if c.card.oracle_id == oracle_id]
    return hits[0] if hits else None


def _rejected_id(result: RecommendationResult, oracle_id: str):
    hits = [c for c in result.rejected if c.card.oracle_id == oracle_id]
    return hits[0] if hits else None


# --------------------------------------------------------------------------- #
# Composition — generate → judge in one call.
# --------------------------------------------------------------------------- #


def test_recommend_composes_generate_into_judge(load_deck) -> None:
    # A clean structural fit and a banned fit, both retrieved offline; judgment
    # accepts the one and routes the other to `rejected` — the composed result.
    deck = load_deck(WORLD_RECLAIMER)  # B/G/R
    cards = [
        _card('oid-ok', 'Fixture Recursion', ['recursion-land'], ['B']),
        _card('oid-banned', 'Banned Recursion', ['recursion-land'], ['B']),
    ]
    enrich = _enricher(
        {
            'oid-ok': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}},
            'oid-banned': {'color_identity': ['B'], 'legalities': {'commander': 'banned'}},
        }
    )
    result = recommend(deck, search_client=make_fixture_search_client(cards), enrich=enrich)

    assert isinstance(result, RecommendationResult)
    assert result.ordering == 'degraded_structural'

    accepted = _accepted_id(result, 'oid-ok')
    assert accepted is not None
    assert accepted.rationale is not None
    assert accepted.rationale.verdict in ('consider', 'marginal')  # degraded, never `strong`
    assert accepted.anchors  # survivors keep their anchors

    # The banned card is routed to `rejected`, never surfaced as a candidate.
    assert _accepted_id(result, 'oid-banned') is None
    rejected = _rejected_id(result, 'oid-banned')
    assert rejected is not None
    assert rejected.rationale is not None
    assert rejected.rationale.verdict == 'pass'
    assert 'legality' in rejected.rationale.context_flags


def test_recommend_default_target_ref_is_deck(load_deck) -> None:
    # With no `target`, the result is scoped to the whole deck (target_ref = deck id).
    deck = load_deck(WORLD_RECLAIMER)
    cards = [_card('oid-ok', 'Fixture Recursion', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-ok': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})
    result = recommend(deck, search_client=make_fixture_search_client(cards), enrich=enrich)
    assert result.target_ref == (deck.get('uuid') or deck.get('name'))


# --------------------------------------------------------------------------- #
# Target scope — fewer / narrower anchors than the bare-deck call.
# --------------------------------------------------------------------------- #


def test_target_scopes_to_fewer_anchors(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    # `recursion-land` fills BOTH a `card_attribute` node anchor (ref recursion-land)
    # and the land event's producer side (ref land-enters-battlefield), so the bare
    # call carries several anchors on the one candidate.
    cards = [_card('oid-multi', 'Multi Anchor', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-multi': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})

    bare = recommend(deck, search_client=make_fixture_search_client(cards), enrich=enrich)
    bare_cand = _accepted_id(bare, 'oid-multi')
    assert bare_cand is not None
    assert len(bare_cand.anchors) >= 2

    # Scoping to the land-event ref keeps only the anchors AT that instance; the
    # `recursion-land` card_attribute anchor (a different instance) is dropped.
    scoped = recommend(
        deck,
        target='land-enters-battlefield',
        search_client=make_fixture_search_client(cards),
        enrich=enrich,
    )
    assert scoped.target_ref == 'land-enters-battlefield'
    scoped_cand = _accepted_id(scoped, 'oid-multi')
    assert scoped_cand is not None
    assert 0 < len(scoped_cand.anchors) < len(bare_cand.anchors)
    assert all(a.target_ref == 'land-enters-battlefield' for a in scoped_cand.anchors)
    assert any('scoped to target' in n for n in scoped.notes)


def test_target_with_no_matching_anchor_drops_candidate(load_deck) -> None:
    # A target no enumerated anchor matches yields an empty candidate set (the card
    # fits the deck but not the requested instance) — scoping, not a crash.
    deck = load_deck(WORLD_RECLAIMER)
    cards = [_card('oid-multi', 'Multi Anchor', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-multi': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})
    result = recommend(
        deck,
        target='a-strategy-this-card-does-not-touch',
        search_client=make_fixture_search_client(cards),
        enrich=enrich,
    )
    assert _accepted_id(result, 'oid-multi') is None
    assert result.candidates == []


# --------------------------------------------------------------------------- #
# Passthrough seams — membership tags, does-not-want, the inert-budget caveat.
# --------------------------------------------------------------------------- #


def test_does_not_want_rejects_through_recommend(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [_card('oid-ok', 'Fixture Recursion', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-ok': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})
    result = recommend(
        deck,
        search_client=make_fixture_search_client(cards),
        enrich=enrich,
        does_not_want=['recursion-land'],  # matches the anchor's attribute
    )
    assert _accepted_id(result, 'oid-ok') is None
    rejected = _rejected_id(result, 'oid-ok')
    assert rejected is not None
    assert 'does-not-want' in (rejected.rationale.context_flags if rejected.rationale else {})


def test_target_scope_does_not_bypass_does_not_want(load_deck) -> None:
    # The `recursion-land` card fills BOTH the recursion-land attribute anchor AND the
    # land-event producer anchor. does_not_want=['recursion-land'] matches via the
    # former. Scoping to a DIFFERENT anchor (land-enters-battlefield) must NOT let the
    # card through: judgment sees the FULL anchor set (so does-not-want still fires),
    # and the scope only narrows the display afterwards (#119 M5). Before the fix,
    # scoping ran first and hid the recursion-land anchor, wrongly accepting the card.
    deck = load_deck(WORLD_RECLAIMER)
    cards = [_card('oid-multi', 'Multi Anchor', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-multi': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})
    result = recommend(
        deck,
        target='land-enters-battlefield',
        search_client=make_fixture_search_client(cards),
        enrich=enrich,
        does_not_want=['recursion-land'],
    )
    assert _accepted_id(result, 'oid-multi') is None  # NOT accepted despite the target scope
    rejected = _rejected_id(result, 'oid-multi')
    assert rejected is not None  # still rejected for does-not-want, scoped into the rejected view
    assert 'does-not-want' in (rejected.rationale.context_flags if rejected.rationale else {})


def test_membership_tags_pass_through(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [_card('oid-ok', 'Fixture Recursion', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-ok': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})
    result = recommend(
        deck,
        search_client=make_fixture_search_client(cards),
        enrich=enrich,
        inventory_lookup=lambda oid: oid == 'oid-ok',
        chase_lookup=lambda _oid: False,
    )
    cand = _accepted_id(result, 'oid-ok')
    assert cand is not None
    assert cand.membership_tags == ['inventory']


def test_budget_without_price_source_notes_inert(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [_card('oid-ok', 'Fixture Recursion', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-ok': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})
    result = recommend(deck, search_client=make_fixture_search_client(cards), enrich=enrich, budget=5.0)
    # A budget with no price_lookup cannot filter; the result says so rather than
    # silently ignoring the ceiling, and no card is budget-rejected.
    assert any('budget' in n and 'inactive' in n for n in result.notes)
    assert _accepted_id(result, 'oid-ok') is not None


def test_budget_with_price_source_filters(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [_card('oid-pricey', 'Pricey Recursion', ['recursion-land'], ['B'])]
    enrich = _enricher({'oid-pricey': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})
    result = recommend(
        deck,
        search_client=make_fixture_search_client(cards),
        enrich=enrich,
        budget=5.0,
        price_lookup=lambda _oid: 42.0,  # over the ceiling
    )
    assert _accepted_id(result, 'oid-pricey') is None
    rejected = _rejected_id(result, 'oid-pricey')
    assert rejected is not None
    assert 'budget' in (rejected.rationale.context_flags if rejected.rationale else {})


# --------------------------------------------------------------------------- #
# MCP schema — model_json_schema() smoke.
# --------------------------------------------------------------------------- #


def test_recommendation_schema_is_valid() -> None:
    schema = recommendation_schema()
    assert schema['type'] == 'object'
    assert 'candidates' in schema['properties']
    assert 'rejected' in schema['properties']
    assert 'ordering' in schema['properties']
    # The schema is self-contained (the nested DTOs are present as $defs).
    assert 'Candidate' in schema['$defs']


# --------------------------------------------------------------------------- #
# Real-lake end-to-end — OPTIONAL, integration-only (deselected by default).
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.skipif(not LAKE_AVAILABLE, reason='requires a hydrated lake')
def test_real_lake_recommend_end_to_end(load_deck) -> None:
    # Against the real lake, the whole composition runs: a non-empty, judged result
    # whose accepted candidates all carry a degraded rationale and whose ordering is
    # the labelled structural fallback.
    result = recommend(load_deck(WORLD_RECLAIMER))
    assert result.ordering == 'degraded_structural'
    assert result.candidates
    assert all(c.rationale is not None for c in result.candidates)
    assert all(c.rationale is not None and c.rationale.verdict != 'strong' for c in result.candidates)


@pytest.mark.integration
@pytest.mark.skipif(not LAKE_AVAILABLE, reason='requires a hydrated lake')
def test_real_lake_land_producer_anchor_carries_join_facts(load_deck) -> None:
    # Phase-6 behavioral criterion: against the real lake, the payoff-rich /
    # producer-thin landfall deck surfaces at least one candidate anchored at the
    # `land-enters-battlefield` PRODUCER side, and every such anchor's neutral
    # fact_snapshot records a valid join — a non-empty directed path and the real C1
    # marginal: support rises by AT MOST one (exactly one for a non-land, zero for a
    # land, which is excluded from the side's support), connectivity by at most one
    # (one for a both-sides relay). Never the old flat `+1` that over-counted lands.
    result = recommend(load_deck(WORLD_RECLAIMER))
    producer_anchors = [
        anchor
        for candidate in result.candidates
        for anchor in candidate.anchors
        if anchor.target_ref == 'land-enters-battlefield' and anchor.direction == 'producer'
    ]
    assert producer_anchors, 'expected land-enters-battlefield producer-side anchors on the real lake'
    for anchor in producer_anchors:
        snap = anchor.fact_snapshot
        assert snap.path, 'producer anchor must carry a non-empty directed path'
        assert snap.support_before <= snap.support_after <= snap.support_before + 1
        assert snap.connectivity_before <= snap.connectivity_after <= snap.connectivity_before + 1
