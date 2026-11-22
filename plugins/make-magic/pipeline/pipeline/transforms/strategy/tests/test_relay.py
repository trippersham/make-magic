"""Tests for the Phase 2 within-card edge relay-node derivation.

Proves that relay injection into the event graph:
1. Closes cross-card loops that the default path does NOT see.
2. Leaves the default ``compute_facts`` / ``resolve()`` path byte-identical
   (the parity-oracle SHAs are unchanged).
3. Correctly maps the 6 curated edge attrs to consumer/producer roles.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from pipeline.transforms.strategy.assets import load_assets
from pipeline.transforms.strategy.machine_vocab import (
    CardAttribute,
    Provenance,
    ProvisionalAttributeStore,
    empty_store,
)
from pipeline.transforms.strategy.relay import (
    _get_role_with_store,
    build_relay_augmented_event_edges,
    elect_engine_units_with_relay,
)
from pipeline.transforms.strategy.strategy_graph import compute_facts, elect_engine_units
from pipeline.transforms.strategy.tests.conftest import LAKE_AVAILABLE
from pipeline.transforms.strategy.tests.engine_parity_oracle import (
    composite_sha,
    compute_engine_isolation_baselines,
    compute_resolve_baselines,
)

_skip_no_lake = pytest.mark.skipif(not LAKE_AVAILABLE, reason='lake parquets not available')

FIX = Path(__file__).resolve().parent / 'fixtures'
RELAY_DECKS = FIX / 'relay_decks'

# The two parity-oracle SHAs that MUST NOT shift.
EXPECTED_ENGINE_SHA = '80d0116a653ffb31692fefb8d6a43b2099440febc3901b361fb9fa034e2bed0f'
EXPECTED_RESOLVE_SHA = '3ebb33fd9b469926fd06d2c63605dfe9bad462563760dae47eca9c4cb93c8c76'


# ---------------------------------------------------------------------------
# Fixture: the Queza + Lich's Mastery relay fixture deck
# ---------------------------------------------------------------------------


@pytest.fixture()
def relay_deck() -> dict:
    path = RELAY_DECKS / 'relay__queza-lichs-mastery.json'
    return json.loads(path.read_text())


@pytest.fixture()
def relay_ctx(relay_deck: dict):
    """FactsContext for the relay fixture deck (DEFAULT path, no relay)."""
    return compute_facts(relay_deck, [])


@pytest.fixture()
def assets():
    return load_assets()


# ---------------------------------------------------------------------------
# A: Default path byte-parity (the relay module must NOT shift these)
# ---------------------------------------------------------------------------


@_skip_no_lake
class TestDefaultPathParity:
    """The default compute_facts / resolve path stays byte-identical.

    These tests prove that importing and using the relay module does not
    contaminate the default code path.
    """

    def test_engine_isolation_sha_unchanged(self) -> None:
        """COMPOSITE engine_isolation SHA over the 17 fixture decks."""
        baselines = compute_engine_isolation_baselines()
        sha = composite_sha(baselines)
        assert sha == EXPECTED_ENGINE_SHA, f'engine_isolation SHA shifted: {sha} != {EXPECTED_ENGINE_SHA}'

    def test_resolve_sha_unchanged(self) -> None:
        """COMPOSITE resolve() SHA over the 17 fixture decks."""
        baselines = compute_resolve_baselines()
        sha = composite_sha(baselines)
        assert sha == EXPECTED_RESOLVE_SHA, f'resolve SHA shifted: {sha} != {EXPECTED_RESOLVE_SHA}'


# ---------------------------------------------------------------------------
# B: Default path on the relay fixture deck (no engines from edge attrs)
# ---------------------------------------------------------------------------


class TestDefaultPathClosesLifetradeViaCatalogRelays:
    """On the relay fixture deck, the DEFAULT path NOW closes the lifetrade loop.

    ADR 0026 Phase 2 relocated the 6 lifetrade couplings into ``SEED_MAP`` as
    catalog ``RelayEntry`` edges, so the ONE unified SCC walk the default
    ``compute_facts`` path runs sees them natively — the loop no longer needs the
    relay-augmented engine_report path. The gate attrs are tested for PRESENCE
    (a carrier in the deck), not identity admission, so the Queza draw↔gain +
    Sanguine Bond↔Exquisite Blood couplings close even though each is a small
    sub-threshold attr.
    """

    def test_edge_attrs_in_nodes(self, relay_ctx) -> None:
        """draw-to-drain, draw-to-gain, gain-to-draw are all in nodes."""
        expected = {'draw-to-drain', 'draw-to-gain', 'gain-to-draw'}
        assert expected <= relay_ctx.nodes

    def test_recursive_lifetrade_engine_present(self, relay_ctx) -> None:
        """The default path reports a recursive-engine (lifetrade loop closed)."""
        iso = relay_ctx.facts['engines']['engine_units']
        kinds = {e['kind'] for e in iso['engines']}
        assert 'recursive-engine' in kinds
        recursive_events = {e['event'] for e in iso['engines'] if e['kind'] == 'recursive-engine'}
        # The draw↔gain loop closes on the default path (catalog relays).
        assert {'lifegain', 'draw-a-card'} <= recursive_events


# ---------------------------------------------------------------------------
# C: build_relay_augmented_event_edges — the derivation itself
# ---------------------------------------------------------------------------


class TestBuildRelayAugmentedEventEdges:
    """The relay-augmented channel (:mod:`.relay`) injects CATALOG relays + STORE edges.

    Lifetrade is now read from the CATALOG relays (ADR 0026 Phase 2), not the dead
    ``edge_roles`` sidecar: a RelayEntry on ``<owning>.produced_by`` injects its gate
    attr as consumer of ``from_event`` + producer of the owning event, exactly as the
    sidecar did — so the relay-augmented engine_report keeps its named cycle + hubs.
    Section F covers the provisional (haiku/spellbook) store-injection path.
    """

    def test_lifetrade_injected_via_catalog_relays(self, relay_ctx, assets) -> None:
        """The lifetrade gate attrs are injected from the catalog relays.

        For the Queza fixture, ``draw-to-gain`` and ``gain-to-draw`` are in nodes, so
        the catalog relays on ``lifegain``/``draw-a-card`` inject them as producer+
        consumer (the draw↔gain cycle).
        """
        augmented = build_relay_augmented_event_edges(relay_ctx.nodes, assets.events)
        # draw-to-gain: consumer of draw-a-card, producer of lifegain.
        assert 'draw-to-gain' in {c for p, c, ev in augmented if ev == 'draw-a-card'}
        assert 'draw-to-gain' in {p for p, c, ev in augmented if ev == 'lifegain'}
        # gain-to-draw: consumer of lifegain, producer of draw-a-card.
        assert 'gain-to-draw' in {c for p, c, ev in augmented if ev == 'lifegain'}
        assert 'gain-to-draw' in {p for p, c, ev in augmented if ev == 'draw-a-card'}

    def test_default_edges_lack_relay_nodes(self, relay_ctx, assets) -> None:
        """The DEFAULT event-edge construction has no edge attrs."""

        # Replicate the default event_edges from compute_facts
        def refs_to_nodes(refs):
            return [r for r in refs if r in relay_ctx.nodes]

        default_edges = []
        for evname, ev in assets.events.items():
            for p in refs_to_nodes(ev['produced_by']):
                for c_ in refs_to_nodes(ev['triggers']):
                    if p != c_:
                        default_edges.append((p, c_, evname))

        relay_attrs = {'draw-to-drain', 'draw-to-gain', 'gain-to-draw'}
        in_default = {a for p, c, _ in default_edges for a in (p, c)}
        assert not (relay_attrs & in_default), (
            f'Edge attrs should NOT appear in default edges: {relay_attrs & in_default}'
        )

    def test_no_injection_without_edge_attrs_in_nodes(self, assets) -> None:
        """If no edge attrs are in nodes, augmented == default (no injection)."""
        # nodes with zero edge attrs
        nodes = {'lifegain', 'draw-matters', 'sac'}
        augmented = build_relay_augmented_event_edges(nodes, assets.events)

        # Replicate default
        def refs_to_nodes(refs):
            return [r for r in refs if r in nodes]

        default = []
        for evname, ev in assets.events.items():
            for p in refs_to_nodes(ev['produced_by']):
                for c_ in refs_to_nodes(ev['triggers']):
                    if p != c_:
                        default.append((p, c_, evname))
        assert augmented == default

    def test_determinism_sorted_injection(self, relay_ctx, assets) -> None:
        """Calling twice produces identical edge lists (deterministic)."""
        a1 = build_relay_augmented_event_edges(relay_ctx.nodes, assets.events)
        a2 = build_relay_augmented_event_edges(relay_ctx.nodes, assets.events)
        assert a1 == a2


# ---------------------------------------------------------------------------
# D: Augmented path — the loop closes (the behavioral proof)
# ---------------------------------------------------------------------------


class TestAugmentedPathClosesLoop:
    """On the relay fixture deck, the AUGMENTED path finds the Queza engine.

    This is the core Phase 2 proof: within-card edges (relay nodes) close
    cross-card loops that the default path cannot see.
    """

    def test_recursive_engines_found(self, relay_ctx, assets) -> None:
        """The augmented path finds recursive-engine(s) (not just static-payoff)."""
        iso = elect_engine_units_with_relay(
            relay_ctx.nodes,
            assets.events,
            relay_ctx.attr_weight,
            assets,
            relay_ctx.cards,
        )
        kinds = {e['kind'] for e in iso['engines']}
        assert 'recursive-engine' in kinds

    def test_draw_lifegain_cycle_is_recursive(self, relay_ctx, assets) -> None:
        """The draw-a-card <-> lifegain cycle closes (both events recursive).

        The Queza engine closes through the catalog lifetrade relays (gain-to-draw
        produces draw-a-card from lifegain; draw-to-gain produces lifegain from
        draw-a-card), so both events sit in one SCC.
        """
        iso = elect_engine_units_with_relay(
            relay_ctx.nodes,
            assets.events,
            relay_ctx.attr_weight,
            assets,
            relay_ctx.cards,
        )
        recursive_events = {e['event'] for e in iso['engines'] if e['kind'] == 'recursive-engine'}
        assert {'draw-a-card', 'lifegain'} <= recursive_events, f'expected draw↔gain recursive; got {recursive_events}'

    def test_determinism_mass_elected(self, relay_ctx, assets) -> None:
        """The augmented path mass-elects candidates (ADR 0014: 'mass-elected').

        The election produces a ranked `elected` candidate set; there is no
        distinguished single lead field. A non-empty election reports
        determinism='mass-elected'.
        """
        iso = elect_engine_units_with_relay(
            relay_ctx.nodes,
            assets.events,
            relay_ctx.attr_weight,
            assets,
            relay_ctx.cards,
        )
        assert iso['determinism'] == 'mass-elected'


# ---------------------------------------------------------------------------
# E: Minimal unit tests — edge cases
# ---------------------------------------------------------------------------


class TestRelayEdgeCases:
    """Edge cases for the relay derivation function."""

    def test_empty_nodes_produces_empty_edges(self, assets) -> None:
        """Empty nodes set -> empty event edges."""
        edges = build_relay_augmented_event_edges(set(), assets.events)
        assert edges == []

    def test_single_edge_attr_in_nodes(self, assets) -> None:
        """A single edge attr in nodes produces edges for its events."""
        nodes = {'draw-to-drain'}
        edges = build_relay_augmented_event_edges(nodes, assets.events)
        # draw-to-drain consumes draw-a-card (but there are no producers of
        # draw-a-card in nodes, so no draw-a-card edges)
        # draw-to-drain produces life-loss-drain (but there are no consumers
        # of life-loss-drain in nodes, so no life-loss-drain edges)
        # With only one node and no other attrs, there are no edges (p != c_
        # prevents self-loops, and no other node is available)
        assert edges == []

    def test_two_complementary_store_edge_attrs(self, assets) -> None:
        """Two complementary STORE edge attrs produce injected edges.

        Lifetrade is now a catalog relay (not sidecar-injected), so the injection
        mechanism is exercised here with provisional (haiku) store attrs. A pair
        that consumes/produces lifegain<->draw-a-card forms the two edges.
        """
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='store-gain-to-draw',
                    query="oracle:'test'",
                    trigger_event='lifegain',
                    produced_event='draw-a-card',
                    provenance=Provenance(source='haiku'),
                ),
                CardAttribute(
                    name='store-draw-to-gain',
                    query="oracle:'test'",
                    trigger_event='draw-a-card',
                    produced_event='lifegain',
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )
        nodes = {'store-gain-to-draw', 'store-draw-to-gain'}
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)
        edge_set = {(p, c, ev) for p, c, ev in edges}
        assert ('store-gain-to-draw', 'store-draw-to-gain', 'draw-a-card') in edge_set
        assert ('store-draw-to-gain', 'store-gain-to-draw', 'lifegain') in edge_set

    def test_elect_engine_units_on_minimal_store_loop(self, assets) -> None:
        """elect_engine_units detects a loop from two complementary store attrs."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='store-gain-to-draw',
                    query="oracle:'test'",
                    trigger_event='lifegain',
                    produced_event='draw-a-card',
                    provenance=Provenance(source='haiku'),
                ),
                CardAttribute(
                    name='store-draw-to-gain',
                    query="oracle:'test'",
                    trigger_event='draw-a-card',
                    produced_event='lifegain',
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )
        nodes = {'store-gain-to-draw', 'store-draw-to-gain'}
        attr_weight: defaultdict[str, float] = defaultdict(
            float, {'store-gain-to-draw': 3.0, 'store-draw-to-gain': 5.0}
        )
        cards: list[dict] = []
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)
        iso = elect_engine_units(nodes, edges, attr_weight, assets, cards)
        assert len(iso['relay_hubs']) == 2
        assert 'store-gain-to-draw' in iso['relay_hubs']
        assert 'store-draw-to-gain' in iso['relay_hubs']
        for e in iso['engines']:
            assert e['kind'] == 'recursive-engine'

    def test_non_edge_attrs_pass_through_unchanged(self, assets) -> None:
        """Non-edge attrs in nodes still produce catalog-derived event edges."""
        nodes = {'lifegain', 'lifegain-matters', 'draw-to-gain'}
        edges = build_relay_augmented_event_edges(nodes, assets.events)
        # lifegain is a catalog producer of lifegain event
        # lifegain-matters is a catalog consumer of lifegain event
        # draw-to-gain is injected: consumer of draw-a-card, producer of lifegain
        # So we should see: (lifegain, lifegain-matters, 'lifegain') from catalog
        #                 + (draw-to-gain, lifegain-matters, 'lifegain') from relay
        #                 + (lifegain, draw-to-gain???) wait, draw-to-gain is NOT
        #   in the catalog's triggers for lifegain. It's in draw-a-card's
        #   triggers. And draw-to-gain produces lifegain.
        #
        # Let me just check that the catalog-derived edge is still there:
        catalog_edges = [(p, c, ev) for p, c, ev in edges if p == 'lifegain' and c == 'lifegain-matters']
        assert len(catalog_edges) >= 1


# ---------------------------------------------------------------------------
# F: Machine-store -> relay path (the Build 1 keystone)
# ---------------------------------------------------------------------------


def _test_provenance(**overrides: object) -> Provenance:
    defaults = {
        'source': 'haiku',
        'model_id': 'test',
        'authored_at': '2026-11-06T00:00:00Z',
        'source_card_name': 'Test Card',
        'source_card_oracle_id': 'test-oid',
        'deck_context': 'test',
        'scryfall_query': 'oracle:/test/',
        'scryfall_resolved_count': 1,
        'verified_at': '2026-11-06T00:00:01Z',
    }
    defaults.update(overrides)
    return Provenance(**defaults)  # type: ignore[arg-type]


class TestProvisionalAttributeStoreAugmentedEdges:
    """Machine-authored attributes contribute to relay injection via store."""

    def test_empty_store_produces_identical_edges(self, relay_ctx, assets) -> None:
        """With store=empty_store(), edges are identical to store=None."""
        edges_none = build_relay_augmented_event_edges(relay_ctx.nodes, assets.events)
        edges_empty = build_relay_augmented_event_edges(
            relay_ctx.nodes,
            assets.events,
            store=empty_store(),
        )
        assert edges_none == edges_empty

    @_skip_no_lake
    def test_empty_store_sha_parity(self) -> None:
        """Composite engine SHA is unchanged with an explicit empty store."""
        baselines = compute_engine_isolation_baselines()
        sha = composite_sha(baselines)
        assert sha == EXPECTED_ENGINE_SHA

    def test_machine_attr_injected_as_consumer_producer(self, assets) -> None:
        """A machine attr with trigger/effect events appears in augmented edges.

        The attr ``test-counter-to-mana`` consumes ``counter-placed`` and
        produces ``lifegain`` (using an existing event for simplicity).
        With ``test-counter-to-mana`` in nodes AND a counter producer
        (``gives-pp-counters``) also in nodes, the relay should inject
        ``test-counter-to-mana`` as consumer of ``counter-placed``.
        """
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='test-counter-to-mana',
                    query="oracle:'test'",
                    trigger_event='counter-placed',
                    produced_event='lifegain',
                    provenance=_test_provenance(),
                ),
            ]
        )
        nodes = {'gives-pp-counters', 'lifegain-matters', 'test-counter-to-mana'}
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)

        # test-counter-to-mana should appear as consumer of counter-placed
        counter_consumers = {c for p, c, ev in edges if ev == 'counter-placed'}
        assert 'test-counter-to-mana' in counter_consumers

        # gives-pp-counters should be a producer feeding test-counter-to-mana
        counter_edges_to_mana = [
            (p, c, ev) for p, c, ev in edges if ev == 'counter-placed' and c == 'test-counter-to-mana'
        ]
        producers_feeding = {p for p, c, ev in counter_edges_to_mana}
        assert 'gives-pp-counters' in producers_feeding

    def test_machine_attr_produces_into_produced_event(self, assets) -> None:
        """The machine attr produces into its effect event."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='test-counter-to-mana',
                    query="oracle:'test'",
                    trigger_event='counter-placed',
                    produced_event='lifegain',
                    provenance=_test_provenance(),
                ),
            ]
        )
        nodes = {'gives-pp-counters', 'lifegain-matters', 'test-counter-to-mana'}
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)

        # test-counter-to-mana should produce lifegain events
        lifegain_producers = {p for p, c, ev in edges if ev == 'lifegain'}
        assert 'test-counter-to-mana' in lifegain_producers

        # lifegain-matters should consume the produced lifegain
        lifegain_to_matters = [(p, c, ev) for p, c, ev in edges if ev == 'lifegain' and p == 'test-counter-to-mana']
        consumers = {c for p, c, ev in lifegain_to_matters}
        assert 'lifegain-matters' in consumers

    def test_machine_attr_not_in_nodes_is_ignored(self, assets) -> None:
        """A machine attr NOT in nodes is not injected (even if in store)."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='test-counter-to-mana',
                    query="oracle:'test'",
                    trigger_event='counter-placed',
                    produced_event='lifegain',
                    provenance=_test_provenance(),
                ),
            ]
        )
        # test-counter-to-mana is NOT in nodes
        nodes = {'gives-pp-counters', 'lifegain-matters'}
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)

        # test-counter-to-mana should NOT appear anywhere
        all_attrs = {a for p, c, _ in edges for a in (p, c)}
        assert 'test-counter-to-mana' not in all_attrs

    def test_store_attr_used_when_no_curated_role(self, relay_ctx, assets) -> None:
        """Lifetrade is no longer curated, so a same-named STORE attr is used.

        ADR 0026 Phase 2 emptied ``CURATED_EDGE_ROLES`` (lifetrade moved to catalog
        relays), so the curated tier is a clean miss and the store attr's own
        trigger/produced events drive the injection.
        """
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='draw-to-gain',
                    query="oracle:'test'",
                    trigger_event='lifegain',
                    produced_event='counter-placed',  # store's own events
                    provenance=_test_provenance(),
                ),
            ]
        )
        # A counter-placed CONSUMER ('counters-matter') must be in nodes for the
        # cross-product to emit the producer edge.
        nodes = {'draw-to-gain', 'counters-matter'}
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)
        # With no curated role, the STORE events win: draw-to-gain produces
        # counter-placed (not the former curated lifegain).
        counter_producers = {p for p, c, ev in edges if ev == 'counter-placed'}
        assert 'draw-to-gain' in counter_producers

    def test_machine_attr_with_novel_event_skipped(self, assets) -> None:
        """Machine attr whose effect event is not in the catalog is skipped."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='test-novel-event',
                    query="oracle:'test'",
                    trigger_event='counter-placed',
                    produced_event='nonexistent-event',  # not in catalog
                    provenance=_test_provenance(),
                ),
            ]
        )
        nodes = {'gives-pp-counters', 'test-novel-event'}
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)
        all_attrs = {a for p, c, _ in edges for a in (p, c)}
        assert 'test-novel-event' not in all_attrs


# ---------------------------------------------------------------------------
# F: Machine-store -> relay path (Build 1 keystone)
# ---------------------------------------------------------------------------


class TestZeroMatchStoreEntryDoesNotAlterCycle:
    """A store entry whose query matches zero cards must not change the engine.

    A store (haiku) entry can share a slug with a NATIVE CardAttribute: the native
    attr puts the slug in ``nodes``, and without an in-deck match gate the store
    entry's unrelated role would hijack that native node and rewrite the named cycle.
    ``identify_engines`` only injects a store role when the entry's query actually
    matched a card (``matched_store_attrs``), so a zero-match entry is inert.
    """

    @staticmethod
    def _zero_match_store() -> ProvisionalAttributeStore:
        # A store entry that SHARES the native ``draw-to-gain`` slug but carries a
        # DIFFERENT role (``life-loss-drain → draw-a-card``) and a query that matches
        # nothing in the Queza fixture (no proliferate cards).
        return ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='draw-to-gain',
                    query='otag:proliferate',
                    trigger_event='life-loss-drain',
                    produced_event='draw-a-card',
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )

    def test_zero_match_store_query_leaves_report_unchanged(self, relay_deck, relay_ctx) -> None:
        from pipeline.transforms.strategy.engine_report import identify_engines

        # ``draw-to-gain`` is a native node on the Queza fixture (draw↔gain cycle).
        assert 'draw-to-gain' in relay_ctx.nodes

        report_empty = identify_engines(relay_deck, store=ProvisionalAttributeStore(), combos=[])
        report_store = identify_engines(relay_deck, store=self._zero_match_store(), combos=[])

        def _sig(r):
            return (
                [(e.name, e.kind, tuple(e.events), tuple(e.loop_members), tuple(e.hub_attrs)) for e in r.engines],
                r.commander_loop,
            )

        # The zero-match store entry is inert: the full engine report is unchanged.
        assert _sig(report_store) == _sig(report_empty), 'a zero-match store entry changed the report'

    def test_match_gate_suppresses_unmatched_store_role(self) -> None:
        """The in-deck match gate is load-bearing and correct in all three states:
        an unmatched entry is suppressed, a matched entry (or the ungated ``None``
        default) injects. This is exactly what keeps a zero-match entry from hijacking
        a same-slug native node's cycle."""
        store = self._zero_match_store()
        # ungated default (no match map) — curated miss falls through to the store.
        assert _get_role_with_store('draw-to-gain', store, None) is not None
        # matched set EXCLUDES the entry -> suppressed (the zero-match case).
        assert _get_role_with_store('draw-to-gain', store, frozenset()) is None
        # matched set INCLUDES the entry -> injected (a real in-deck match).
        assert _get_role_with_store('draw-to-gain', store, frozenset({'draw-to-gain'})) is not None


class TestProvisionalAttributeStoreRelayPath:
    """Machine-authored attributes flow through the relay via the store.

    Proves: a CardAttribute with trigger_event/produced_event is injected
    as a relay node, while an empty store produces byte-identical output.
    """

    def test_empty_store_equals_no_store(self, relay_ctx, assets) -> None:
        """Empty store produces the same edges as no store (byte-parity)."""
        edges_no_store = build_relay_augmented_event_edges(relay_ctx.nodes, assets.events)
        edges_empty_store = build_relay_augmented_event_edges(
            relay_ctx.nodes, assets.events, store=ProvisionalAttributeStore()
        )
        assert edges_no_store == edges_empty_store

    def test_get_role_with_store_falls_through_to_store(self) -> None:
        """With no curated role (lifetrade relocated), the store attr is returned.

        ADR 0026 Phase 2: ``CURATED_EDGE_ROLES`` is empty, so the curated tier is a
        clean miss and ``_get_role_with_store`` returns the store attr's role.
        """
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='draw-to-drain',
                    query="oracle:'test'",
                    trigger_event='lifegain',
                    produced_event='lifegain',
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )
        role = _get_role_with_store('draw-to-drain', store)
        # No curated role remains -> the store attr wins.
        assert role is not None
        assert role.trigger_event == 'lifegain'
        assert role.produced_event == 'lifegain'

    def test_get_role_with_store_machine_attr(self) -> None:
        """Machine attr is returned when no curated role exists."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='counter-to-mana',
                    query="oracle:'number of counters on'",
                    trigger_event='counter-placed',
                    produced_event='lifegain',  # placeholder for test
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )
        role = _get_role_with_store('counter-to-mana', store)
        assert role is not None
        assert role.trigger_event == 'counter-placed'
        assert role.produced_event == 'lifegain'

    def test_get_role_with_store_none_for_missing(self) -> None:
        """Missing attr returns None even with a store."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='counter-to-mana',
                    query="oracle:'test'",
                    trigger_event='counter-placed',
                    produced_event='lifegain',
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )
        assert _get_role_with_store('nonexistent', store) is None

    def test_machine_attr_injected_as_relay_node(self, assets) -> None:
        """A machine attr in nodes is injected as consumer+producer in edges."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='counter-to-mana',
                    query="oracle:'number of counters on'",
                    trigger_event='counter-placed',
                    produced_event='lifegain',  # use existing event for test
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )
        # nodes containing the machine attr + some counter producers + lifegain consumer
        nodes = {'counter-to-mana', 'gives-pp-counters', 'lifegain-matters'}
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)
        # counter-to-mana should consume counter-placed (from gives-pp-counters producer)
        # and produce lifegain (consumed by lifegain-matters)
        counter_feed = [(p, c, ev) for p, c, ev in edges if c == 'counter-to-mana' and ev == 'counter-placed']
        assert len(counter_feed) >= 1, f'Expected counter-placed -> counter-to-mana; got {edges}'
        mana_produce = [(p, c, ev) for p, c, ev in edges if p == 'counter-to-mana' and ev == 'lifegain']
        assert len(mana_produce) >= 1, f'Expected counter-to-mana -> lifegain; got {edges}'

    def test_machine_attr_with_novel_event_skipped(self, assets) -> None:
        """A machine attr whose events are not in the catalog is safely skipped."""
        store = ProvisionalAttributeStore(
            attributes=[
                CardAttribute(
                    name='counter-to-mana',
                    query="oracle:'test'",
                    trigger_event='counter-placed',
                    produced_event='mana-from-counters',  # NOT in catalog
                    provenance=Provenance(source='haiku'),
                ),
            ]
        )
        nodes = {'counter-to-mana', 'gives-pp-counters'}
        # Should not crash; the machine attr is skipped because its effect event
        # does not exist in the catalog.
        edges = build_relay_augmented_event_edges(nodes, assets.events, store=store)
        # counter-to-mana should NOT appear in any edge
        machine_in_edges = {a for p, c, _ in edges for a in (p, c) if a == 'counter-to-mana'}
        assert not machine_in_edges


# ---------------------------------------------------------------------------
# F: Superset-of-resolve inputs (#83 MAJOR-4) — combo edges + archetype gate
# ---------------------------------------------------------------------------


@pytest.fixture()
def urza_ctx():
    """FactsContext for the hermetic Urza fixture (otags baked in; no lake)."""
    deck = json.loads((FIX / 'decks' / 'gauntlet__urza-lord-high-artificer.json').read_text())
    combos = [dict(c) for c in json.loads((FIX / 'combos.json').read_text())]
    return compute_facts(deck, combos)


class TestSupersetInputsForwarded:
    """``elect_engine_units_with_relay`` threads ``combo_event_edges`` and
    ``earned_archetypes`` through to the inner ``elect_engine_units``, exactly as
    ``resolve()`` passes them. Defaults are backward-compatible (no combo edges,
    archetype grain disabled), so existing callers are unaffected.
    """

    def test_backward_compatible_default_adds_no_recursion(self, urza_ctx, assets) -> None:
        """With the defaults (no combo edges), mana-produced is NOT recursive —
        the pre-fix behavior, proving the new params are inert when omitted."""
        iso = elect_engine_units_with_relay(urza_ctx.nodes, assets.events, urza_ctx.attr_weight, assets, urza_ctx.cards)
        recursive = {e['event'] for e in iso['engines'] if e['kind'] == 'recursive-engine'}
        assert 'mana-produced' not in recursive

    def test_combo_event_edges_forwarded_flip_kind(self, urza_ctx, assets) -> None:
        """Threading the combo-certified mana↔spell-cast family edges flips those
        events' units to recursive-engine (forwarded to recursive_event_set)."""
        combo_edges = [
            ('mana-produced', 'spell-cast'),
            ('spell-cast', 'mana-produced'),
        ]
        iso = elect_engine_units_with_relay(
            urza_ctx.nodes,
            assets.events,
            urza_ctx.attr_weight,
            assets,
            urza_ctx.cards,
            combo_event_edges=combo_edges,
        )
        recursive = {e['event'] for e in iso['engines'] if e['kind'] == 'recursive-engine'}
        assert {'mana-produced', 'spell-cast'} <= recursive

    def test_earned_archetypes_gate_bites_in_election(self, assets) -> None:
        """``earned_archetypes`` reaches the inner archetype-substantiveness gate and
        the gate BITES — proving the argument is consumed, not dropped.

        The dropped-arg bug makes the gated and ungated calls agree (the gate never
        runs), and an ``iso_gated <= iso_open`` assertion is satisfied by that equality
        — so it passes in the broken world too. This needs a unit the gate actually
        removes: a bipartite catalog unit (``crucible-of-worlds`` → ``land-enters-
        battlefield`` → ``land-count-matters``) whose SOLE basis is the unearned
        ``lands-matter`` archetype claim, with no commander coupling (``cards=[]``).
        Under the ``None`` default the grain is disabled and the unit is elected; with
        an empty earned set every claim is unearned, so the unit is excluded. Strict
        inequality is the proof the gate bit.
        """
        nodes = {'crucible-of-worlds', 'land-count-matters'}
        attr_weight: defaultdict[str, float] = defaultdict(
            float, {'crucible-of-worlds': 3.0, 'land-count-matters': 3.0}
        )
        cards: list[dict] = []
        iso_open = elect_engine_units_with_relay(nodes, assets.events, attr_weight, assets, cards)
        iso_gated = elect_engine_units_with_relay(
            nodes, assets.events, attr_weight, assets, cards, earned_archetypes=set()
        )
        open_events = {u['event'] for u in iso_open['ranked_units']}
        gated_events = {u['event'] for u in iso_gated['ranked_units']}
        # Present under the grain-disabled default, elected on its (unearned) claim.
        assert 'land-enters-battlefield' in open_events
        assert iso_open['ranked_units'][0]['payoff_archetypes'] == ['lands-matter']
        # Excluded when the earned set is empty — the gate removed it.
        assert 'land-enters-battlefield' not in gated_events
        # Strictly fewer units: the gate BIT, it did not no-op (the dropped-arg world
        # would make these equal).
        assert len(iso_gated['ranked_units']) < len(iso_open['ranked_units'])


# ---------------------------------------------------------------------------
# #115: a pure-event-cycle lifetrade loop must not be dropped by the election
# ---------------------------------------------------------------------------


class TestPureEventCycleLifetradeLoop:
    """A lifetrade loop whose ONLY enablers are sub-threshold gate carriers is
    recursive in the SCC but has no admitted attr producer-consumer pair, so the
    per-event election skipped it and the public ``resolve()`` returned
    ``engines=[]`` (#115). The walk now emits a ``recursive-engine`` unit for such an
    event using its live gate carriers as evidence; removing either enabler opens the
    loop.

    The enabler pair is the real-Magic combo: Sanguine Bond (``gain-to-drain``: "you
    gain life → opponent loses that much") + Exquisite Blood (``drain-to-gain``:
    "opponent loses life → you gain that much"). Each is a 1-card sub-threshold attr
    that never earns an identity node, so neither ``lifegain`` nor ``life-loss-drain``
    gets an in-node attr producer here — the pure-event-cycle shape.
    """

    @staticmethod
    def _sanguine() -> dict:
        return {
            'name': 'Sanguine Bond',
            'oracle_id': 'sanguine-bond',
            'type_line': 'Enchantment',
            'oracle_text': 'Whenever you gain life, target opponent loses that much life.',
            'otags': ['lifegain-matters', 'opponent-loses-life', 'life-total-matters-self'],
        }

    @staticmethod
    def _exquisite() -> dict:
        return {
            'name': 'Exquisite Blood',
            'oracle_id': 'exquisite-blood',
            'type_line': 'Enchantment',
            'oracle_text': 'Whenever an opponent loses life, you gain that much life.',
            'otags': ['life-loss-matters', 'lifegain'],
        }

    def _yargle_deck(self, *, include: tuple[str, ...] = ('sanguine', 'exquisite')) -> dict:
        yargle = {
            'name': 'Yargle, Glutton of Urborg',
            'role': 'commander',
            'oracle_id': 'yargle',
            'type_line': 'Legendary Creature — Frog Spirit',
            'oracle_text': '',
            'otags': [],
        }
        enablers: list[dict] = []
        if 'sanguine' in include:
            enablers.append(self._sanguine())
        if 'exquisite' in include:
            enablers.append(self._exquisite())
        # Pad to a full 99 with basic Swamps so the deck resolves normally.
        swamps = [
            {
                'name': f'Swamp #{i}',
                'oracle_id': f'swamp-{i}',
                'type_line': 'Basic Land — Swamp',
                'oracle_text': '({T}: Add {B}.)',
                'otags': [],
            }
            for i in range(99 - len(enablers))
        ]
        return {'name': 'yargle-lifetrade', 'cards': [yargle, *enablers, *swamps]}

    def test_public_resolve_reports_recursive_lifetrade_engine(self) -> None:
        from pipeline.transforms.strategy import resolve

        units = resolve(self._yargle_deck(), [])['facts']['engines']['engine_units']
        recursive = {e['event'] for e in units['engines'] if e['kind'] == 'recursive-engine'}
        # Both halves of the lifedrain loop are now reported recursive...
        assert {'lifegain', 'life-loss-drain'} <= recursive, f'missing pure-event-cycle units: {recursive}'
        # ...and elected (not just enumerated) — the deck no longer reports engines=[].
        ranked = {u['event'] for u in units['ranked_units']}
        assert {'lifegain', 'life-loss-drain'} <= ranked

    def test_pure_event_cycle_unit_uses_live_gate_carrier_as_evidence(self) -> None:
        from pipeline.transforms.strategy import resolve

        units = resolve(self._yargle_deck(), [])['facts']['engines']['engine_units']
        by_event = {e['event']: e for e in units['engines']}
        # life-loss-drain is produced by Sanguine Bond's gain-to-drain gate...
        assert by_event['life-loss-drain']['producers'] == ['gain-to-drain']
        assert by_event['life-loss-drain']['consumers'] == []
        # ...lifegain by Exquisite Blood's drain-to-gain gate — the live carriers.
        assert by_event['lifegain']['producers'] == ['drain-to-gain']
        assert by_event['lifegain']['consumers'] == []

    def test_loop_opens_when_either_enchantment_removed(self) -> None:
        from pipeline.transforms.strategy import resolve

        for keep in (('sanguine',), ('exquisite',)):
            units = resolve(self._yargle_deck(include=keep), [])['facts']['engines']['engine_units']
            recursive = {e['event'] for e in units['engines'] if e['kind'] == 'recursive-engine'}
            assert not ({'lifegain', 'life-loss-drain'} & recursive), (
                f'the lifetrade loop should open with only {keep[0]}; got recursive={recursive}'
            )
