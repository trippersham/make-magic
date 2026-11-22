"""Phase 3 — T'Challa artifact-cast-MV>=4 gated edge (DATA authoring on the landed #83 mechanism).

The gate mechanism already landed in the base (PR 114 / #83): the ``SEED_MAP
{from_event, gate}`` relay-entry shape, wired guard evaluation, and the
machine-vocab ``trigger_event``/``produced_event`` within-card relay channel
(:mod:`.relay`). This module authors **no mechanism**. It authors ONE
comparison-specific gated relay edge as **DATA** — a machine-vocab
:class:`.machine_vocab.CardAttribute` carried in a **frozen snapshot fixture**
(``machine_vocab_tchalla_frozen.json``), never the live shared store — and proves:

1. ``verify()`` passes on the ``artifact-cast-mv4`` query: it resolves on the lake,
   hits the trigger-bearing carrier(s), and does NOT over-capture (no cheap
   artifacts, no non-counter payoffs, no non-trigger cards).
2. Loading the snapshot adds **exactly** the ``spell-cast -> counter-placed`` relay
   carrier to T'Challa's engine view (``counter-placed`` gains ``artifact-cast-mv4``
   as a producer, off the cast trigger — NOT ``artifact-enters``); with the
   empty/default store the edge is absent. The engine surface is otherwise
   byte-identical (isolation).
3. World Reclaimer's land-loop SCC + closing commander loop, and Queza's lifetrade
   SCC, already resolve on the base with **NO new authored edge** (confirm, don't author).

The carrier is the COMMANDER that bears the trigger (``T'Challa, the Black Panther``:
"Whenever you cast an artifact spell with mana value 4 or greater, put two +1/+1
counters on T'Challa"), exactly as the landed lifetrade relays carry their gate on
Queza. The query language has no numeric MV operator (only ``otag``/``keyword``/
``type``/``oracle``/``devotion``), so the "artifact AND MV>=4" guard is encoded in the
oracle-text regex, corroborated by the cast-trigger and pp-counter otags.

See ``subagents/p3-tchalla-edge/prompt.md`` + design.md Concept 7 (§6 item 0).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.transforms.strategy.engine_hydration import make_lake_search_client, verify
from pipeline.transforms.strategy.engine_report import identify_engines
from pipeline.transforms.strategy.machine_vocab import (
    ProvisionalAttributeStore,
    empty_store,
    load_frozen_snapshot,
)
from pipeline.transforms.strategy.resolve import resolve
from pipeline.transforms.strategy.tests.conftest import LAKE_AVAILABLE

_skip_no_lake = pytest.mark.skipif(not LAKE_AVAILABLE, reason='lake parquets not available')

# Comparison decks live in their OWN fixtures dir, deliberately NOT ``fixtures/decks/``:
# the engine-parity oracle globs ``fixtures/decks/*.json`` into the composite baseline
# SHA, so adding a deck there would shift the held SHA. These decks are read only by
# this module.
FIX = Path(__file__).resolve().parent / 'fixtures'
DECKS = FIX / 'comparison_decks'
FROZEN_SNAPSHOT = FIX / 'machine_vocab_tchalla_frozen.json'

#: The authored gate attribute name + its within-card relay roles (DATA under test).
GATE_NAME = 'artifact-cast-mv4'
GATE_FROM_EVENT = 'spell-cast'
GATE_OWNING_EVENT = 'counter-placed'

#: The exact lake hit set of the gate query: the two cards that bear the
#: "cast an artifact spell with mana value 4 or greater -> put +1/+1 counters"
#: trigger (the relay carriers). NOT the >=4-MV artifact spells themselves —
#: those are the eligible FUEL; the carrier is the card that reacts to the cast.
EXPECTED_CARRIERS = ["T'Challa, the Black Panther", 'Zuri, Warrior of Wakanda']


def _load_deck(stem: str) -> dict:
    return json.loads((DECKS / f'{stem}.json').read_text())


def _snapshot_store() -> ProvisionalAttributeStore:
    return load_frozen_snapshot(FROZEN_SNAPSHOT)


def _engine_surface(deck: dict, store: ProvisionalAttributeStore) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Canonical {event -> (producers, consumers)} from the relay-augmented engine view."""
    rep = identify_engines(deck, store=store)
    surface: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for e in rep.raw_isolation.get('engines') or []:
        surface[e['event']] = (
            tuple(sorted(e.get('producers') or [])),
            tuple(sorted(e.get('consumers') or [])),
        )
    return surface


# ---------------------------------------------------------------------------
# The authored DATA is self-consistent (no lake needed).
# ---------------------------------------------------------------------------


def test_snapshot_authors_the_single_gated_edge() -> None:
    """The frozen snapshot holds exactly the one T'Challa relay edge, on the relay-entry shape."""
    store = _snapshot_store()
    assert store.names() == frozenset({GATE_NAME})
    attr = store.get(GATE_NAME)
    assert attr is not None
    # The {from_event, gate} relay-entry shape, expressed as a machine-vocab edge:
    # trigger_event == from_event, produced_event == the owning event, name == gate.
    assert attr.trigger_event == GATE_FROM_EVENT
    assert attr.produced_event == GATE_OWNING_EVENT
    assert attr.query  # a Scryfall-pasteable eligibility query (data, not code)
    # Not persisted to the live store — provenance records the authoring context.
    assert attr.provenance.source_card_name == "T'Challa, the Black Panther"
    assert attr.provenance.deck_context == 'wakanda-fourever'


# ---------------------------------------------------------------------------
# verify() — the authored query passes the three-check gate (lake substrate).
# ---------------------------------------------------------------------------


@_skip_no_lake
def test_verify_passes_on_artifact_cast_mv4() -> None:
    """verify() passes: RE2-pasteable, resolves, hits the source card, corroborates."""
    attr = _snapshot_store().get(GATE_NAME)
    assert attr is not None
    res = verify(
        attr.query,
        (GATE_FROM_EVENT, GATE_OWNING_EVENT),
        expected_card_names=["T'Challa, the Black Panther"],
        search_client=make_lake_search_client(),
        decision='edge',
    )
    assert res.passed, res.error
    assert res.pasteability_ok
    assert res.resolves
    assert res.expected_cards_hit
    # Edge decision => real otag corroboration (not the synth bypass): every captured
    # card intersects BOTH spell-cast's triggers AND counter-placed's produced_by.
    assert res.corroboration_rate == 1.0
    assert res.failing_cards == []


@_skip_no_lake
def test_verify_hit_set_is_exactly_the_trigger_carriers_no_overcapture() -> None:
    """The query captures EXACTLY the two trigger-bearing carriers — no over/under-capture."""
    attr = _snapshot_store().get(GATE_NAME)
    assert attr is not None
    hits = sorted(c['name'] for c in make_lake_search_client()(attr.query))
    assert hits == sorted(EXPECTED_CARRIERS)
    # Battery Bearer ("cast an artifact spell with mana value 6 or greater, DRAW a card")
    # bears a sibling trigger but a DIFFERENT payoff (draw, not counters); the
    # repeatable-pp-counters otag constraint excludes it — no payoff over-capture.
    assert 'Battery Bearer' not in hits


# ---------------------------------------------------------------------------
# The edge fires ONLY with the snapshot, and ONLY as a counter-placed producer
# off the cast trigger — the engine surface is otherwise byte-identical.
# ---------------------------------------------------------------------------


@_skip_no_lake
def test_tchalla_edge_fires_only_with_snapshot_and_in_isolation() -> None:
    """Loading the snapshot adds exactly one thing: artifact-cast-mv4 as a counter-placed producer."""
    deck = _load_deck('wakanda')
    before = _engine_surface(deck, empty_store())
    after = _engine_surface(deck, _snapshot_store())

    # (a) Same set of events either way.
    assert set(before) == set(after)

    # (b) The ONLY delta across the whole engine surface is on counter-placed,
    #     and it is exactly: artifact-cast-mv4 added to the PRODUCERS (consumers
    #     unchanged). Every other event is byte-identical. This is the
    #     before/after "semantic_dump" diff showing ONLY the new edge.
    changed = {ev for ev in before if before[ev] != after[ev]}
    assert changed == {GATE_OWNING_EVENT}, f'collateral drift on {changed - {GATE_OWNING_EVENT}}'

    prod_before, cons_before = before[GATE_OWNING_EVENT]
    prod_after, cons_after = after[GATE_OWNING_EVENT]
    assert set(prod_after) - set(prod_before) == {GATE_NAME}
    assert cons_after == cons_before  # consumers untouched

    # (c) The edge is a CAST edge, not an artifact-enters edge: the new carrier
    #     appears on counter-placed (off spell-cast), NEVER on artifact-enters.
    assert GATE_NAME not in after.get('artifact-enters', ((), ()))[0]
    assert GATE_NAME not in after.get('artifact-enters', ((), ()))[1]


@_skip_no_lake
def test_default_resolve_path_never_sees_the_authored_edge() -> None:
    """The committed resolve() path (no store) is untouched: the gate attr is not present."""
    deck = _load_deck('wakanda')
    rec = resolve(deck)
    assert GATE_NAME not in rec['facts']['attributes_present']
    # And the empty-store engine view carries no counter-placed producer by that name.
    before = _engine_surface(deck, empty_store())
    assert GATE_NAME not in before[GATE_OWNING_EVENT][0]


# ---------------------------------------------------------------------------
# CONFIRM (do NOT author): WR + Queza already resolve their loops on the base.
# ---------------------------------------------------------------------------


@_skip_no_lake
def test_world_reclaimer_land_loop_framework_admitted_no_new_edge() -> None:
    """WR's land-sac loop is a convergent SCC + a closing commander loop on the base — no authoring."""
    deck = _load_deck('world_reclaimer')
    rec = resolve(deck)
    components = rec['facts']['engines']['convergent_event_components']
    # The 6-event convergent land/sacrifice/graveyard component (the land loop).
    land_loop = next(
        (
            c
            for c in components
            if {'card-enters-graveyard', 'land-enters-battlefield', 'permanent-sacrificed'} <= set(c['events'])
        ),
        None,
    )
    assert land_loop is not None, 'WR land-loop convergent component missing'
    assert land_loop['n_events'] >= 6
    assert land_loop['size'] >= 20  # a real multi-node SCC, not a degenerate pair

    # The commander (Szarel) closes the loop — with the empty/default store (no authored edge).
    rep = identify_engines(deck, store=empty_store())
    assert rep.commander_loop is not None
    assert rep.commander_loop.get('closes') is True


@_skip_no_lake
def test_queza_lifetrade_loop_closes_on_base_no_new_edge() -> None:
    """Queza's lifegain<->life-loss-drain lifetrade loop closes via the landed relays — no authoring."""
    deck = _load_deck('queza')
    rec = resolve(deck)
    components = rec['facts']['engines']['convergent_event_components']
    lifetrade = next(
        (c for c in components if {'lifegain', 'life-loss-drain'} == set(c['events'])),
        None,
    )
    assert lifetrade is not None, 'Queza lifetrade convergent component missing'
    assert lifetrade['size'] >= 2  # a closed multi-node cycle, not an isolated node


@_skip_no_lake
def test_wr_and_queza_confirmations_need_no_authored_store() -> None:
    """Both confirmations hold under the EMPTY store — proving they are base behavior, not our edge."""
    for stem, needed_events in (
        ('world_reclaimer', {'card-enters-graveyard', 'land-enters-battlefield', 'permanent-sacrificed'}),
        ('queza', {'lifegain', 'life-loss-drain'}),
    ):
        deck = _load_deck(stem)
        rep = identify_engines(deck, store=empty_store())
        events_seen = {e['event'] for e in rep.raw_isolation.get('engines') or []}
        assert needed_events <= events_seen
