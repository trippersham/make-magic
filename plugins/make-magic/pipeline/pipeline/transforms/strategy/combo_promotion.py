"""Combo-certified relay promotion — the resource-dependent families (ADR 0026 §4).

Issue #83 Phase 3 + Phase 3b. The unified event graph (ADR 0026) closes a within-card loop
by reading event→event relay edges. For the one clean family (lifetrade) those edges are
authored as catalog ``RelayEntry`` gate-queries (Phase 2). The resource/repeatability-dependent
families get their edges by **combo-certified promotion** instead — the drift pressure-test
proved that **naive gate queries manufacture false circuits** for them: the ADR-0009 closed
query vocabulary can match the cards but cannot express the net-mana / repeatability / zone
arithmetic that edge validity needs (Grim Monolith nets -1 mana yet a gate matches it as an
untap↔mana edge; a one-shot copy like Narset's Reversal matches a cast/copy edge though it
never recurs itself). The promoted families:

* **Phase 3 (7):** ETB/death-recursion, mana-refund, cast/copy, draw-wheel, untap-mana,
  counter, landfall — each a 2-cycle between two co-certified events.
* **Phase 3b (2):** self-mill (``card-enters-graveyard ↔ draw-a-card``, a 2-cycle) and
  extra-turn (``extra-turn``, a single self-sustaining event → self-edge). The extra-turn and
  self-mill carriers that ``fid_route`` routes OUTSIDE ``intermediate`` (``turn-monopoly`` /
  ``self-deplete``) are surfaced by :func:`.combo_win.certified_unbounded_events`, the superset
  extractor the promotion consumes.

So a family's edge is injected into the engine graph ONLY from a loop ``combo_win`` has ALREADY
certified unbounded (:func:`.combo_win.certified_unbounded_events`). This reuses the
combo-detector's structural ground truth (ADR 0016) as the liveness filter — it is correct by
construction, because a family edge ``(a, b)`` is admitted only when BOTH its endpoint events
are certified unbounded **in the same combo** (and a single self-sustaining event only when it
is itself certified unbounded). A false carrier's event is never certified unbounded (Grim
Monolith's untap routes ``oneshot``, a finite copy's ``spell-on-stack`` routes ``oneshot``, a
one-shot extra-turn spell routes ``oneshot``, a finite Thassa's-Oracle self-mill routes a
finite ``self-deplete``), so its family edge can never fire — the Grim-Monolith / one-shot-copy /
token-ETB-false-draw / one-shot-extra-turn / one-shot-mill circuits stay structurally open.

These are **per-deck runtime provisional edges**, not catalog-static relays: a deck
earns them from the combos it actually contains. They are injected into the one SCC
walk (``strategy_graph.recursive_event_set``) alongside the catalog lifetrade relays;
the SCC then closes the genuine loop so the event's engine unit reports
``recursive-engine``. The promotion only reclassifies the *kind* of an engine unit that
already exists (an event with in-deck producer AND consumer attrs); it never admits a
new node, so there is zero identity/strategy/community drift — only ``facts['engines']``
moves. (Combo detection itself — ``combos_in_deck``/``combo_win_verdict`` — is NOT
re-run here; this module consumes its already-computed output. Deck-specific combo
knowledge lives in the lake's combo data, never as a branch in this engine code.)
"""

from __future__ import annotations

from pipeline.transforms.strategy.combo_win import certified_unbounded_events

#: The resource-dependent relay families as declarative event→event 2-cycle edge rules
#: (the 7 Phase-3 families + the Phase-3b self-mill family).
#: Each rule is ``(family, (event_a, event_b))``: an undirected relay between the two
#: events, wired as a 2-cycle (both directions) when BOTH endpoints are certified
#: unbounded in one combo. Edges only — zero new events (every carrier already exists in
#: the 33-event catalog, per ``loop-coverage-audit.md`` §4). The pairs come directly from
#: that audit's §3 family inventory:
#:
#: * ETB/death-recursion — the aristocrat loop ``creature-enters ↔ dies/sacrificed`` (via
#:   recursion-from-graveyard; the recursion event is the mechanism, the ETB and the
#:   death/sac are the certified-unbounded endpoints).
#: * mana-refund — the ritual engine ``mana-produced ↔ spell-cast`` (produce mana, cast
#:   the refunder, produce again).
#: * cast/copy — magecraft/storm ``spell-cast ↔ spell-on-stack ↔ spell-copied`` (the full
#:   pairwise triangle among whichever of the three the combo makes unbounded).
#: * draw-wheel — ``draw-a-card ↔ discard`` (loot) and ``draw-a-card ↔ untap`` (untap-to-
#:   redraw).
#: * untap-mana — the untapper loop ``untap ↔ mana-produced``.
#: * counter — proliferate/untap counter loop ``counter-placed ↔ untap``.
#: * landfall — bounce-land recursion ``land-enters ↔ permanent-leaves`` and the mana a
#:   recurred land taps for ``land-enters ↔ mana-produced``.
#:
#: Because EVERY rule requires both endpoints certified unbounded, the whole classifier is
#: one uniform predicate (:func:`certified_family_pairs`); there is no per-family branch.
#: (The two Phase-3b families — extra-turn and self-mill — are NOT here: each is a single
#: self-sustaining event with no co-unbounded partner; see :data:`SELF_SUSTAINING_EVENTS`.)
FAMILY_RELAY_EDGES: tuple[tuple[str, tuple[str, str]], ...] = (
    ('etb-death-recursion', ('creature-enters-battlefield', 'permanent-dies')),
    ('etb-death-recursion', ('creature-enters-battlefield', 'permanent-sacrificed')),
    ('mana-refund', ('mana-produced', 'spell-cast')),
    ('cast-copy', ('spell-cast', 'spell-on-stack')),
    ('cast-copy', ('spell-cast', 'spell-copied')),
    ('cast-copy', ('spell-on-stack', 'spell-copied')),
    ('draw-wheel', ('draw-a-card', 'discard')),
    ('draw-wheel', ('draw-a-card', 'untap')),
    ('untap-mana', ('untap', 'mana-produced')),
    ('counter', ('counter-placed', 'untap')),
    ('landfall', ('land-enters-battlefield', 'permanent-leaves-battlefield')),
    ('landfall', ('land-enters-battlefield', 'mana-produced')),
)

#: Families whose certified loop is a SINGLE self-sustaining event — one event that recurs
#: without bound, with no co-unbounded partner to pair into a 2-cycle (ADR 0026 §4 / Phase 3b).
#: Each is wired as an event SELF-EDGE (``e → e``), which the SCC definition treats as a
#: 1-node cycle (``_graph.strongly_connected_components`` doc), recognised by
#: ``recursive_event_set``. Entries are ``(family, event)``:
#:
#: * **extra-turn** (``extra-turn``) — infinite turns. A certified ``turn-monopoly`` combo
#:   makes ONLY ``extra-turn`` unbounded (its other fid is the finite ``lock`` state), so there
#:   is no second event to pair. NB: ``extra-turn`` is not an engine UNIT in any corpus deck
#:   (no attr triggers ON an extra turn), so this is mechanically complete but corpus-inert at
#:   the engine-unit level today — it closes the loop in the recursive SET and is future-proof
#:   if a catalog change ever gives ``extra-turn`` a consumer.
#: * **self-mill** (``card-enters-graveyard``) — an infinite self-mill / graveyard-recursion
#:   churn: cards entering a graveyard without bound. The carrier is a single self-sustaining
#:   event (the "draw/mill -> card-enters-graveyard" bridge of the audit's self-mill residue is
#:   itself the recurrence), so a self-edge is the faithful shape; ``card-enters-graveyard`` IS
#:   an engine unit in many decks, so this flips the genuine infinite-mill (Bruvac/Phenax),
#:   graveyard-recursion (Karador/Muldrotha/Tayam) and Underworld-Breach-loop (Jeleva/Kess/Ral/
#:   Vivi) engines.
#:
#: The W5-style guarantee is the same as every other family: a finite one-shot (a single
#: extra-turn spell routes ``oneshot``; a finite / one-shot self-mill — Thassa's Oracle library
#: depletion — routes a finite ``self-deplete``) is never certified unbounded, so it never
#: self-loops. And ``deckout-opp`` (opponent mill, a §4 terminal) is excluded from the
#: certified set, so an opponent-deckout combo never makes ``card-enters-graveyard`` recurse.
SELF_SUSTAINING_EVENTS: tuple[tuple[str, str], ...] = (
    ('extra-turn', 'extra-turn'),
    ('self-mill', 'card-enters-graveyard'),
)


def certified_family_pairs(unbounded: set[str]) -> set[tuple[str, str]]:
    """The relay family pairs one combo earns, given its certified-unbounded events.

    A family edge ``(a, b)`` fires iff BOTH ``a`` and ``b`` are in *unbounded* — i.e.
    the one combo makes BOTH endpoint events infinite. This is the structural W1
    guarantee: a false carrier (Grim Monolith, a one-shot copy, a token maker with no
    real draw return) never certifies both endpoints unbounded, so its family edge is
    never admitted. Returns the canonical ``(a, b)`` pairs (``a``/``b`` in the order
    they appear in :data:`FAMILY_RELAY_EDGES`); the caller wires each as a 2-cycle.
    """
    return {edge for _family, edge in FAMILY_RELAY_EDGES if edge[0] in unbounded and edge[1] in unbounded}


def combo_certified_event_edges(win_combo_fids: list[list[int]]) -> list[tuple[str, str]]:
    """Per-deck combo-certified event→event edges for the one SCC walk.

    *win_combo_fids* is the fid list of every WIN combo detected in the deck (the walk's
    ``combo_win_verdict`` already decided win-ness; this consumes that, it does not
    re-detect). For each win combo, the events it certifies unbounded
    (:func:`.combo_win.certified_unbounded_events` — the ``intermediate`` carriers PLUS the
    Phase-3b ``turn-monopoly``/``self-deplete`` self-sustaining carriers) select both the
    2-cycle family pairs (:func:`certified_family_pairs`) and the single-event self-sustaining
    loops (:data:`SELF_SUSTAINING_EVENTS`). Each 2-cycle pair becomes directed edges
    ``(a, b)`` and ``(b, a)``; each self-sustaining event becomes a self-edge ``(e, e)``.
    Edges from all win combos are unioned and returned sorted (determinism, ADR 0003).

    The per-combo scoping is load-bearing: two UNRELATED combos (one makes infinite
    untap, another infinite mana) must NOT compose into a spurious untap↔mana loop, so
    both endpoints of a family edge must be certified unbounded in the SAME combo.
    """
    pairs: set[tuple[str, str]] = set()
    selfloops: set[str] = set()
    for fids in win_combo_fids:
        certified = certified_unbounded_events(fids)
        pairs |= certified_family_pairs(certified)
        selfloops |= {ev for _family, ev in SELF_SUSTAINING_EVENTS if ev in certified}
    edges: set[tuple[str, str]] = set()
    for a, b in pairs:
        edges.add((a, b))
        edges.add((b, a))
    for ev in selfloops:
        edges.add((ev, ev))
    return sorted(edges)
