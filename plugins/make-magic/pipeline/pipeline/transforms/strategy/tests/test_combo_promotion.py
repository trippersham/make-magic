"""Combo-certified relay promotion — the 7 resource-dependent families (ADR 0026 §4, #83 Phase 3).

The seven resource/repeatability-dependent families (ETB/death, mana-refund, cast/copy,
draw-wheel, untap-mana, counter, landfall) get their event→event edges ONLY from loops
``combo_win`` already certified unbounded — NEVER from naive gate queries. These tests pin:

* the family classifier (:func:`certified_family_pairs`) — each family fires iff BOTH its
  endpoint events are certified unbounded; a single endpoint never fires (the W1 guarantee);
* :func:`unbounded_intermediate_events` returns exactly the ``infinite``/``near-infinite``
  carrier events (finite effects excluded), reusing the bundled fid adapter (offline);
* :func:`combo_certified_event_edges` wires each family pair as a directed 2-cycle, unions
  across win combos, and is PER-COMBO scoped (two unrelated combos never compose);
* the one SCC walk (:func:`recursive_event_set`) closes the genuine loop when the combo
  edges are present and leaves it OPEN when they are not;
* **W5 regression (MANDATORY):** the 10 named false circuits of ``drift-pressure-test.md``
  stay OPEN — Grim Monolith does NOT make ``untap`` recursive, one-shot copies do NOT make
  the cast/copy events recursive, token/ETB makers do NOT make ``draw-a-card`` recursive.
  The hermetic layer proves the MECHANISM (a finite carrier is never in ``U`` → its family
  edge is never admitted); the lake-gated layer proves it end-to-end on the real corpus.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

import pytest

from pipeline.transforms.strategy.combo_promotion import (
    FAMILY_RELAY_EDGES,
    SELF_SUSTAINING_EVENTS,
    certified_family_pairs,
    combo_certified_event_edges,
)
from pipeline.transforms.strategy.combo_win import certified_unbounded_events, unbounded_intermediate_events
from pipeline.transforms.strategy.strategy_graph import recursive_event_set

# ---------------------------------------------------------------------------
# Representative fids from the BUNDLED fid adapter (data/fid_event_adapter.json),
# so the fid-level tests run OFFLINE (the adapter is not lake-backed).
# ---------------------------------------------------------------------------
FID_INF_UNTAP = 57  # "Infinite untap of creatures you control"
FID_INF_MANA = 5  # "Infinite colored mana"
FID_INF_SPELL_CAST = 17  # "Infinite storm count"
FID_INF_SPELL_ON_STACK = 77  # "Infinite magecraft triggers"
FID_INF_ETB = 4  # "Infinite creature ETB"
FID_INF_DIES = 7  # "Infinite death triggers"
FID_INF_SAC = 6  # "Infinite creature sacrifice triggers"
FID_INF_DRAW = 24  # "Infinite card draw"
FID_INF_DISCARD = 36  # "Infinite self-discard triggers"
FID_INF_COUNTER = 115  # "Infinite +1/+1 counters"
FID_INF_LAND = 119  # "Infinite landfall triggers"
FID_INF_LTB = 3  # "Infinite creature LTB"
FID_FINITE_CAST = 107  # finite: "Opponents can only cast one non-artifact…"
FID_FINITE_SAC = 135  # finite: "Opponents sacrifice all their creatures"
# Phase 3b fixtures (the two self-sustaining families + their W5-style false carriers)
FID_INF_EXTRA_TURN = 42  # "Infinite turns" (route turn-monopoly)
FID_FINITE_EXTRA_TURN = 1742  # finite "Extra Turn Spell" (route oneshot) — must NOT close
FID_INF_SELF_MILL = 10  # "Infinite self-mill" card-enters-graveyard (route intermediate)
FID_FINITE_SELF_MILL = 1631  # finite "Mill all creature cards in your library" (oneshot)
FID_INF_DECKOUT_OPP = 9  # "Infinite mill" library-depleted (route deckout-opp — a §4 terminal)
FID_FINITE_SELF_DEPLETE = 88  # finite "Exile your library" (route self-deplete, finite)


# ---------------------------------------------------------------------------
# certified_family_pairs — each family fires iff BOTH endpoints certified unbounded
# ---------------------------------------------------------------------------


class TestFamilyClassifier:
    def test_all_seven_families_present_in_spec(self):
        families = {fam for fam, _edge in FAMILY_RELAY_EDGES}
        assert families == {
            'etb-death-recursion',
            'mana-refund',
            'cast-copy',
            'draw-wheel',
            'untap-mana',
            'counter',
            'landfall',
        }

    def test_etb_death_recursion(self):
        assert ('creature-enters-battlefield', 'permanent-dies') in certified_family_pairs(
            {'creature-enters-battlefield', 'permanent-dies'}
        )
        assert ('creature-enters-battlefield', 'permanent-sacrificed') in certified_family_pairs(
            {'creature-enters-battlefield', 'permanent-sacrificed'}
        )

    def test_mana_refund(self):
        assert certified_family_pairs({'mana-produced', 'spell-cast'}) == {('mana-produced', 'spell-cast')}

    def test_cast_copy_triangle(self):
        pairs = certified_family_pairs({'spell-cast', 'spell-on-stack', 'spell-copied'})
        assert pairs == {
            ('spell-cast', 'spell-on-stack'),
            ('spell-cast', 'spell-copied'),
            ('spell-on-stack', 'spell-copied'),
        }

    def test_draw_wheel(self):
        assert certified_family_pairs({'draw-a-card', 'discard'}) == {('draw-a-card', 'discard')}
        assert certified_family_pairs({'draw-a-card', 'untap'}) >= {('draw-a-card', 'untap')}

    def test_untap_mana(self):
        assert ('untap', 'mana-produced') in certified_family_pairs({'untap', 'mana-produced'})

    def test_counter(self):
        assert ('counter-placed', 'untap') in certified_family_pairs({'counter-placed', 'untap'})

    def test_landfall(self):
        assert ('land-enters-battlefield', 'permanent-leaves-battlefield') in certified_family_pairs(
            {'land-enters-battlefield', 'permanent-leaves-battlefield'}
        )
        assert ('land-enters-battlefield', 'mana-produced') in certified_family_pairs(
            {'land-enters-battlefield', 'mana-produced'}
        )

    def test_empty(self):
        assert certified_family_pairs(set()) == set()

    # --- the W1 guarantee: a SINGLE endpoint never fires a family edge ------
    def test_single_endpoint_never_fires(self):
        # Grim Monolith nets -1 mana: it produces (net) mana but NOT infinite untap, so a
        # Monolith-only combo's U lacks ``untap`` → the untap-mana edge is never admitted.
        assert certified_family_pairs({'mana-produced'}) == set()
        assert certified_family_pairs({'untap'}) == set()
        # A one-shot copy: spell-on-stack alone (no co-unbounded cast) → no cast/copy edge.
        assert certified_family_pairs({'spell-on-stack'}) == set()
        # A token/ETB maker with no real draw return: creature-enters alone → no etb-death
        # edge, and draw-a-card alone → no draw-wheel edge.
        assert certified_family_pairs({'creature-enters-battlefield'}) == set()
        assert certified_family_pairs({'draw-a-card'}) == set()


# ---------------------------------------------------------------------------
# unbounded_intermediate_events — reuse fid_route; only infinite carriers
# ---------------------------------------------------------------------------


class TestUnboundedIntermediateEvents:
    def test_infinite_included(self):
        assert unbounded_intermediate_events([FID_INF_UNTAP, FID_INF_MANA]) == {'untap', 'mana-produced'}

    def test_finite_excluded(self):
        # A finite cast effect routes ``oneshot``, never ``intermediate`` — so it is NOT a
        # certified loop carrier (this is why one-shot copies cannot close cast/copy).
        assert unbounded_intermediate_events([FID_FINITE_CAST]) == set()
        assert unbounded_intermediate_events([FID_FINITE_SAC]) == set()

    def test_mixed_keeps_only_infinite(self):
        assert unbounded_intermediate_events([FID_INF_SPELL_CAST, FID_FINITE_CAST]) == {'spell-cast'}

    def test_unknown_fid_ignored(self):
        assert unbounded_intermediate_events([-1, 0]) == set()


# ---------------------------------------------------------------------------
# combo_certified_event_edges — 2-cycles, union, PER-COMBO scoping, W5 mechanism
# ---------------------------------------------------------------------------


class TestComboCertifiedEventEdges:
    def test_two_cycle_both_directions(self):
        edges = combo_certified_event_edges([[FID_INF_UNTAP, FID_INF_MANA]])
        assert set(edges) == {('untap', 'mana-produced'), ('mana-produced', 'untap')}

    def test_etb_death_cycle(self):
        edges = set(combo_certified_event_edges([[FID_INF_ETB, FID_INF_DIES, FID_INF_SAC]]))
        assert ('creature-enters-battlefield', 'permanent-dies') in edges
        assert ('permanent-dies', 'creature-enters-battlefield') in edges
        assert ('creature-enters-battlefield', 'permanent-sacrificed') in edges

    def test_sorted_deterministic(self):
        edges = combo_certified_event_edges([[FID_INF_UNTAP, FID_INF_MANA]])
        assert edges == sorted(edges)

    def test_union_across_combos(self):
        edges = set(combo_certified_event_edges([[FID_INF_UNTAP, FID_INF_MANA], [FID_INF_ETB, FID_INF_DIES]]))
        assert ('untap', 'mana-produced') in edges
        assert ('creature-enters-battlefield', 'permanent-dies') in edges

    def test_per_combo_scoping(self):
        # Two UNRELATED combos (one makes only infinite untap, the other only infinite mana)
        # must NOT compose into a spurious untap↔mana loop — both endpoints must be certified
        # unbounded in the SAME combo.
        assert combo_certified_event_edges([[FID_INF_UNTAP], [FID_INF_MANA]]) == []

    def test_w5_finite_carrier_yields_no_edge(self):
        # A finite one-shot (the one-shot-copy class) certifies nothing → no edge.
        assert combo_certified_event_edges([[FID_FINITE_CAST]]) == []
        # A mana-only infinite (the Grim-Monolith class once promoted to mana) certifies only
        # ``mana-produced`` → no family pair (untap-mana needs untap; mana-refund needs cast).
        assert combo_certified_event_edges([[FID_INF_MANA]]) == []


# ---------------------------------------------------------------------------
# recursive_event_set — the one SCC walk CLOSES with combo edges, OPEN without
# ---------------------------------------------------------------------------


class TestSccClosure:
    def test_combo_edges_close_the_loop(self):
        # No catalog, no attr legs — the pure event→event 2-cycle closes on its own.
        rec = recursive_event_set([], {}, set(), None, [('untap', 'mana-produced'), ('mana-produced', 'untap')])
        assert rec == {'untap', 'mana-produced'}

    def test_no_combo_edges_leaves_open(self):
        assert recursive_event_set([], {}, set(), None, None) == set()
        assert recursive_event_set([], {}, set(), None, []) == set()

    def test_one_directional_edge_does_not_close(self):
        # A single direction is not a cycle — nothing recurses.
        assert recursive_event_set([], {}, set(), None, [('untap', 'mana-produced')]) == set()

    def test_self_edge_closes_single_event_loop(self):
        # A 1-node SCC with a self-edge IS a cycle (Phase 3b extra-turn / self-mill).
        assert recursive_event_set([], {}, set(), None, [('extra-turn', 'extra-turn')]) == {'extra-turn'}


# ---------------------------------------------------------------------------
# Phase 3b — the two self-sustaining families (extra-turn, self-mill) + their
# extraction extension and W5-style regression
# ---------------------------------------------------------------------------


class TestCertifiedUnboundedEventsExtension:
    """``certified_unbounded_events`` surfaces the turn-monopoly + self-mill carriers that
    ``fid_route`` routes OUTSIDE ``intermediate`` — and ONLY the unbounded, non-terminal ones."""

    def test_extra_turn_surfaced(self):
        # extra-turn routes ``turn-monopoly`` (never ``intermediate``) — the superset sees it,
        # the Phase-3 intermediate extractor does not.
        assert certified_unbounded_events([FID_INF_EXTRA_TURN]) == {'extra-turn'}
        assert unbounded_intermediate_events([FID_INF_EXTRA_TURN]) == set()

    def test_self_mill_surfaced(self):
        assert certified_unbounded_events([FID_INF_SELF_MILL]) == {'card-enters-graveyard'}

    def test_finite_extra_turn_excluded(self):
        # A single extra-turn spell routes ``oneshot`` — never certified unbounded.
        assert certified_unbounded_events([FID_FINITE_EXTRA_TURN]) == set()

    def test_finite_self_mill_excluded(self):
        assert certified_unbounded_events([FID_FINITE_SELF_MILL]) == set()

    def test_deckout_opp_excluded(self):
        # Opponent library depletion (``deckout-opp``) is a §4 one-shot terminal, NOT a
        # self-sustaining loop — it must never surface as a loop carrier.
        assert certified_unbounded_events([FID_INF_DECKOUT_OPP]) == set()

    def test_finite_self_deplete_excluded(self):
        # Thassa's-Oracle-style finite self-deplete is a one-shot win, not a loop.
        assert certified_unbounded_events([FID_FINITE_SELF_DEPLETE]) == set()

    def test_superset_of_intermediate(self):
        # The superset contains every intermediate carrier plus the new ones.
        fids = [FID_INF_UNTAP, FID_INF_MANA, FID_INF_EXTRA_TURN]
        assert certified_unbounded_events(fids) >= unbounded_intermediate_events(fids)
        assert 'extra-turn' in certified_unbounded_events(fids)


class TestSelfSustainingFamilies:
    """extra-turn and self-mill are single self-sustaining events → self-edges."""

    def test_spec_lists_both(self):
        assert {ev for _fam, ev in SELF_SUSTAINING_EVENTS} == {'extra-turn', 'card-enters-graveyard'}

    def test_extra_turn_self_edge(self):
        assert combo_certified_event_edges([[FID_INF_EXTRA_TURN]]) == [('extra-turn', 'extra-turn')]

    def test_self_mill_self_edge(self):
        assert combo_certified_event_edges([[FID_INF_SELF_MILL]]) == [
            ('card-enters-graveyard', 'card-enters-graveyard')
        ]

    def test_w5_finite_extra_turn_no_edge(self):
        assert combo_certified_event_edges([[FID_FINITE_EXTRA_TURN]]) == []

    def test_w5_one_shot_mill_no_edge(self):
        assert combo_certified_event_edges([[FID_FINITE_SELF_MILL]]) == []

    def test_w5_deckout_opp_no_edge(self):
        # An opponent-deckout combo must NOT make card-enters-graveyard (or anything) recurse.
        assert combo_certified_event_edges([[FID_INF_DECKOUT_OPP]]) == []


# ---------------------------------------------------------------------------
# W5 end-to-end on the real corpus (lake-gated) + per-family closes
# ---------------------------------------------------------------------------


def _check_lake() -> bool:
    base = os.environ.get('MAKE_MAGIC_DATA_DIR')
    root = Path(base) if base else (Path.home() / '.local/share/make-magic')
    return (root / 'raw' / 'oracle_cards.parquet').exists() and (root / 'normalized' / 'card_otag.parquet').exists()


_LAKE_AVAILABLE = _check_lake()


def _recursive_units(deck, combos, assets):
    from pipeline.transforms.strategy.strategy_graph import compute_facts

    ctx = compute_facts(deck, list(combos), assets=assets)
    return {e['event'] for e in ctx.facts['engines']['engine_units']['engines'] if e['kind'] == 'recursive-engine'}


def _resolve_gauntlet(substr: str):
    """Build every gauntlet deck whose commander/stem contains *substr* (lower-cased)."""
    from pipeline.transforms.strategy.corpus import (
        GAUNTLET_DIR,
        Lake,
        attach_meta,
        build_deck_from_dck,
        gauntlet_decks,
        load_card_meta,
    )

    lake = Lake()
    meta = load_card_meta()
    out = []
    for info in gauntlet_decks(GAUNTLET_DIR):
        stem = info['path'].stem
        name = str(info.get('commander_hint') or stem)
        if substr in (name + ' ' + stem).lower():
            out.append((stem, attach_meta(build_deck_from_dck(info, lake), meta)))
    return out


@pytest.mark.skipif(not _LAKE_AVAILABLE, reason='lake parquets not available')
class TestW5FalseCircuitsStayOpenOnCorpus:
    """The 10 named false circuits (drift-pressure-test.md §4) stay OPEN end-to-end.

    Combo-certified promotion never injects a false carrier's family edge (the carrier's
    event is finite → not in ``U``), so the named (deck, event) never reports
    ``recursive-engine``. Where the SAME event IS recursive via a genuinely-certified
    DIFFERENT combo (Rocco's untap via Kiki-Jiki + Village Bell-Ringer; Selvala's
    creature-enters via Temur Sabertooth blink), the event is still NOT an engine UNIT in
    that deck — these decks have no ``untap``/``creature-enters`` unit — so the assertion
    holds. See the Phase-3 outbox Open Questions for that owner-flagged nuance.
    """

    # (commander substring, event that must NOT be a recursive engine unit)
    FALSE_CIRCUITS: ClassVar[list[tuple[str, str]]] = [
        ('bruvac', 'spell-cast'),
        ('rakdos, the muscle', 'spell-copied'),
        ('hashaton', 'spell-copied'),
        ('narset', 'spell-on-stack'),
        ('jhoira', 'token-created'),
        ('selvala', 'creature-enters-battlefield'),
        ("k'rrik", 'untap'),
        ('ketramose', 'untap'),
        ('rocco', 'untap'),
        ('etali', 'untap'),
    ]

    def test_named_false_circuits_never_recursive(self):
        from pipeline.transforms.strategy import load_assets
        from pipeline.transforms.strategy.combo import load_combos_from_lake

        assets = load_assets()
        combos = load_combos_from_lake()
        violations = []
        for substr, event in self.FALSE_CIRCUITS:
            for stem, deck in _resolve_gauntlet(substr):
                rec = _recursive_units(deck, combos, assets)
                if event in rec:
                    violations.append(f'{stem}:{event}')
        assert not violations, f'W5 FALSE CIRCUIT CLOSED (blocker): {violations}'


@pytest.mark.skipif(not _LAKE_AVAILABLE, reason='lake parquets not available')
class TestFamilyLoopsCloseOnCorpus:
    """Each family (7 Phase-3 + the Phase-3b self-mill) reports a ``recursive-engine`` on a
    named corpus deck. (extra-turn is intentionally absent — it is never an engine unit in
    the corpus, see the Phase-3b outbox; its mechanism is covered hermetically above.)"""

    # (commander substring, family event that must become recursive)
    FAMILY_CLOSES: ClassVar[list[tuple[str, str]]] = [
        ('ghave', 'creature-enters-battlefield'),  # ETB/death recursion
        ('karador', 'permanent-dies'),  # ETB/death recursion (death side)
        ('selvala', 'mana-produced'),  # untap-mana / mana-refund
        ('derevi', 'mana-produced'),  # untap-mana
        ('jeleva', 'spell-cast'),  # cast/copy (storm)
        ('kalamax', 'spell-on-stack'),  # cast/copy (magecraft)
        ('ezuri', 'counter-placed'),  # counter
        ('aesi', 'land-enters-battlefield'),  # landfall
        ('ral', 'discard'),  # draw-wheel
        ('bruvac', 'card-enters-graveyard'),  # self-mill (Phase 3b)
        ('phenax', 'card-enters-graveyard'),  # self-mill (Phase 3b)
    ]

    def test_family_loops_report_recursive(self):
        from pipeline.transforms.strategy import load_assets
        from pipeline.transforms.strategy.combo import load_combos_from_lake

        assets = load_assets()
        combos = load_combos_from_lake()
        missing = []
        for substr, event in self.FAMILY_CLOSES:
            decks = _resolve_gauntlet(substr)
            if not decks:
                missing.append(f'{substr}:NO-DECK')
                continue
            if not any(event in _recursive_units(deck, combos, assets) for _stem, deck in decks):
                missing.append(f'{substr}:{event}')
        assert not missing, f'family loop did not close: {missing}'
