"""FACTS layer — the deterministic per-deck strategy graph.

Set-membership + counting only; does NOT depend on clustering. Produces the
inspectable ``facts`` object (attributes present + weighted counts, every
archetype strategy that fires + its weighted card quantity / requires_met /
member cards, convergent engines, support/degree quantities, answered_by facts,
and core-combo facts) plus a :class:`FactsContext` carrying the intermediate
state the thin NAMING layer (:mod:`.naming`) reads.

The modularity communities and convergent event components are STRUCTURE-ONLY:
they are reported as ``facts["engines"]`` and never decide the label.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from pipeline.transforms.strategy import _graph
from pipeline.transforms.strategy.assets import (
    ANSWERS_MAP,
    ANSWERS_MAP_ALL_SLUGS,
    StrategyAssets,
    is_land,
    load_assets,
)
from pipeline.transforms.strategy.combo import (
    combo_label,
    combo_norm,
    combos_in_deck,
)
from pipeline.transforms.strategy.combo_win import (
    _RULE_TERMINAL_EVENTS,
    combo_win_verdict,
    resolve_fids_from_feature_names,
    walk_is_win,
    walk_win_kind,
)
from pipeline.transforms.strategy.config import FACTS_ONLY_WHEN_DISABLED, StrategyConfig
from pipeline.transforms.strategy.degree import compute_degree_axes, power_profile
from pipeline.transforms.strategy.derive_card_attributes import derive_card_attributes
from pipeline.transforms.strategy.derive_card_events import RelayEntry
from pipeline.transforms.strategy.determine_strategy_substantiveness import determine_event
from pipeline.transforms.strategy.distill_strategies import synth_attrs_for
from pipeline.transforms.strategy.query import CardView
from pipeline.transforms.strategy.tribes import compute_tribes, creature_subtypes

TYPAL = 'typal/tribal'


@dataclass
class FactsContext:
    """Intermediate state shared from the facts layer to the naming layer."""

    deck: dict
    cards: list[dict]
    n_nonland: int
    assets: StrategyAssets
    cfg: StrategyConfig
    facts: dict
    strategies: dict[str, dict]
    facts_only: set[str]
    arch_members: dict[str, set[int]]
    arch_has_cmd: dict[str, bool]
    nodes: set[str]
    support: frozenset[str]
    attr_weight: defaultdict[str, float]
    card_w: dict[int, float]
    identity_mass: float
    coverage: float
    demoted_archetypes: list[str]
    cs_axis: dict
    stax_axis: dict
    ramp_axis: dict
    protection_degree: dict
    power: dict
    tribe_info: dict
    density_signals: dict
    cmd_attrs: set[str]
    detected: list[dict]
    win_combos: list[dict]
    predicate_wins: list[dict]
    mana_sink_wins: list[dict]
    mana_sinks_present: list[str] = field(default_factory=list)


def determine_node_eligible(support_count: int, is_cmd: bool) -> bool:
    """Substantiveness presence gate: >= 3 payoffs, or >= 2 when a commander carries
    the attr. ONE named implementation (ADR 0004) -- requires_met, lands_signal_present,
    and otag_typal_registered all call it."""
    return support_count >= 3 or (is_cmd and support_count >= 2)


def determine_node_admitted(support_count: int, is_cmd: bool) -> bool:
    """Graph node admission: >= 3 support cards, or a commander-carried attr admitted
    UNCONDITIONALLY. ONE named implementation (ADR 0004) -- the facts `nodes` set and
    engine_report's machine-attr admission both call it. Distinct from determine_node_eligible:
    a commander attr is admitted here at any support count."""
    return support_count >= 3 or is_cmd


def _compute_answered_by(cards: list[dict], strategies: dict[str, dict]) -> dict:
    by_slug: dict[str, list[str]] = defaultdict(list)
    for c in cards:
        if is_land(c):
            continue
        ot = set(c.get('otags', []))
        for a in ANSWERS_MAP_ALL_SLUGS & ot:
            by_slug[a].append(c['name'])
    out: dict[str, dict] = {}
    for arch, st in strategies.items():
        slugs = ANSWERS_MAP.get(arch)
        if not slugs or not st.get('requires_met'):
            continue
        in_deck = sorted({n for s in slugs for n in by_slug.get(s, [])})
        out[arch] = {'answer_slugs': slugs, 'n_in_deck_answers': len(in_deck), 'in_deck_answer_cards': in_deck}
    return out


def unified_event_graph_edges(
    event_edges: list[tuple[str, str, str]],
    events: dict,
    nodes: set[str],
    relay_gate_carriers: frozenset[str] | set[str] | None = None,
    combo_event_edges: list[tuple[str, str]] | None = None,
) -> tuple[set[tuple[str, str]], list[tuple[tuple[str, str], tuple[str, str]]]]:
    """Edges of the ONE unified CardAttribute | Event graph (ADR 0026).

    The engine models a single concept — the Event — as a first-class NODE, not a
    bipartite edge label. Node keys are ``('a', <attr>)`` and ``('e', <event>)``
    (the tag disambiguates an attr whose slug equals an event slug). Each
    attr→attr ``event_edge`` ``(p, c, ev)`` is the collapsed form of the two
    bipartite legs this graph restores: ``p → ev`` (``p`` produces the event) and
    ``ev → c`` (the event triggers ``c``). A :class:`RelayEntry` authored on an
    event's ``produced_by`` side (``produced_by``-only, owner-approved) is the
    gated event→event edge ``from_event → owning-event``; it is live iff its
    ``gate`` CardAttribute **has a carrier in the deck** (ADR 0026 §1 / the design's
    "enabling cards" rule: the edge is live iff the gate has >= 1 carrier — a gate
    carrier is the edge's enabler, NOT a claim that the gate is a substantive
    identity node). *Relay-gate liveness is therefore PRESENCE, distinct from the
    admission that governs string attr legs:* a relay closes a structural loop
    (Sanguine Bond's gain→drain + Exquisite Blood's drain→gain = the recursive
    lifegain↔life-loss-drain engine) whenever both enabler cards are in the deck,
    even when each is a 1-2-card sub-threshold attr that never earns an identity
    node. ``relay_gate_carriers`` names the present-attr set the gate is tested
    against; when ``None`` it falls back to ``nodes`` (the structural callers that
    pass a single set where present == admitted, e.g. ``loop_aware_nodes``, which
    already passes ALL present attrs). This is the one place a relay is INTERPRETED
    as a graph edge rather than dropped as a clean-miss string.

    Legs come from TWO sources, unioned: the ``events`` catalog (every string
    producer/consumer leg + the gated event→event relay edges) AND the passed
    ``event_edges`` (which, on the relay-augmented walk, carries within-card edge
    attrs injected into events that are NOT in the catalog dict — see
    :mod:`.relay`). Building catalog legs directly from ``events`` — rather than
    only from ``event_edges`` — is load-bearing: a relay-PRODUCED event has no
    string producer, so its trigger legs ``ev → consumer`` would be missing from
    the collapsed ``event_edges`` and its relay cycle could never close. A string
    attr on BOTH sides of one event (e.g. ``rad`` ↔ proliferate) is a genuine
    ``attr → ev → attr`` 2-cycle and is kept (no ``p != c`` filter here — that
    filter is for the attr-only projected view, not the unified graph).

    The returned node set contains exactly the nodes that appear in >= 1 edge; an
    isolated event (no in-deck producer or consumer) cannot sit on a cycle and is
    therefore immaterial to SCC detection.
    """
    gate_carriers = relay_gate_carriers if relay_gate_carriers is not None else nodes
    graph_nodes: set[tuple[str, str]] = set()
    edges: list[tuple[tuple[str, str], tuple[str, str]]] = []

    def add(u: tuple[str, str], v: tuple[str, str]) -> None:
        graph_nodes.add(u)
        graph_nodes.add(v)
        edges.append((u, v))

    # (1) attr legs from the (possibly augmented) event_edges — the source of the
    # relay-augmented walk's injected within-card edge attrs.
    for p, c_, ev in event_edges:
        add(('a', p), ('e', ev))
        add(('e', ev), ('a', c_))
    # (2) attr legs + gated relay edges straight from the event catalog.
    for ev, spec in events.items():
        en = ('e', ev)
        for ref in spec.get('produced_by', ()):
            if isinstance(ref, RelayEntry):
                # event→event relay (ADR 0026, produced_by-only): live iff its gate
                # CardAttribute HAS A CARRIER in the deck (presence), not iff the
                # gate is an admitted identity node. A lifetrade relay closes its
                # loop on the enabler card alone (Sanguine Bond + Exquisite Blood),
                # which is often a 1-2-card sub-threshold attr.
                if ref.gate in gate_carriers:
                    add(('e', ref.from_event), en)
            elif ref in nodes:
                add(('a', ref), en)
        for ref in spec.get('triggers', ()):
            if isinstance(ref, str) and ref in nodes:
                add(en, ('a', ref))
    # (3) Optional per-deck event→event edges injected directly into the SCC walk. Each
    # ``(a, b)`` is a pure event→event edge wired alongside the catalog relays; the caller
    # passes both directions of a pair so the SCC closes the loop and the endpoint events'
    # engine units report ``recursive-engine``. This is the injection seam reserved for the
    # 7 resource-dependent combo-certified families, which are DEFERRED to #90 (ADR 0026 §4);
    # in the shipped engine every caller passes ``None`` here, so no family edge reaches the
    # graph. The seam is kept covered so #90 re-adds a vetted family layer without re-plumbing.
    for a, b in combo_event_edges or ():
        add(('e', a), ('e', b))
    return graph_nodes, edges


def _catalog_event_edges(events: dict, present: set[str]) -> list[tuple[str, str, str]]:
    """``(producer, consumer, event)`` attr-edges over *present* string refs.

    The ``produced_by x triggers`` cross-product the facts layer walks, restricted
    to string CardAttribute refs that are ``present`` (``p != c``). RelayEntry refs
    are NOT attr-edges — they are interpreted as event→event edges downstream.
    """
    edges: list[tuple[str, str, str]] = []
    for evname, ev in events.items():
        prods = [r for r in ev['produced_by'] if isinstance(r, str) and r in present]
        cons = [r for r in ev['triggers'] if isinstance(r, str) and r in present]
        for p in prods:
            for c_ in cons:
                if p != c_:
                    edges.append((p, c_, evname))
    return edges


def loop_aware_nodes(
    prevalent: set[str],
    attr_weight: dict[str, float],
    assets: StrategyAssets,
) -> set[str]:
    """Loop-aware substantiveness admission (ADR 0026 — the W2 rule).

    A node is substantive iff it is **statistically prevalent** (``>= 3`` carriers,
    or commander-carried — already in *prevalent*) **OR structurally load-bearing**
    (on a closed cycle, among otherwise-substantive nodes). This is the provisional
    cycle pass that lives INSIDE the substantiveness stage (structure, not mass —
    honouring the ADR 0021 substantiveness→centrality order): it admits a
    sub-threshold attr only when it participates in a nontrivial SCC of the unified
    CardAttribute | Event graph that is **anchored by >= 1 already-prevalent attr**
    (the "among otherwise-substantive nodes" clause — a cycle formed purely from
    sub-threshold noise never earns admission). Binary SCC-membership, never a
    continuous score; centrality/mass-election (ADR 0014) is untouched.

    The provisional graph is built over every present attribute (``attr_weight``
    keys), so a candidate's loop may route through other candidates as long as the
    component carries a substantive anchor.
    """
    all_attrs = set(attr_weight)
    candidates = all_attrs - prevalent
    if not candidates:
        return set(prevalent)
    # The unified graph builds all catalog attr legs straight from ``events`` over
    # the ``all_attrs`` filter, so no pre-collapsed ``event_edges`` is needed here.
    graph_nodes, uni_edges = unified_event_graph_edges([], assets.events, all_attrs)
    admitted = set(prevalent)
    for comp in _graph.strongly_connected_components(graph_nodes, uni_edges):
        if len(comp) < 2:
            continue
        attrs_in = {name for tag, name in comp if tag == 'a'}
        if attrs_in & prevalent:  # anchored by an otherwise-substantive node
            admitted |= attrs_in & candidates
    return admitted


def recursive_event_set(
    event_edges: list[tuple[str, str, str]],
    events: dict,
    nodes: set[str],
    relay_gate_carriers: frozenset[str] | set[str] | None = None,
    combo_event_edges: list[tuple[str, str]] | None = None,
) -> set[str]:
    """Events on a true cycle of the unified graph — the ``recursive-engine`` set.

    One sorted-Tarjan SCC pass over :func:`unified_event_graph_edges`. An event is
    recursive iff its node lands in a nontrivial strongly-connected component
    (size >= 2 — a real directed cycle). This REPLACES the ``producers &
    consumers`` heuristic, which flagged an event as recursive whenever any one of
    its producers also consumed *some* (possibly unrelated) event, without proving
    the cycle ever returned. Per-event static-unit enumeration is unaffected: the
    SCC only reclassifies recursive-vs-static.

    ``relay_gate_carriers`` is the present-attr set a catalog :class:`RelayEntry`'s
    gate is tested against (presence, per ADR 0026 §1); ``None`` falls back to
    ``nodes``. Passing the full present set lets a lifetrade relay close its loop on
    the enabler cards alone, even when the gate attr is sub-threshold for identity.

    ``combo_event_edges`` is the optional per-deck event→event injection seam reserved for
    the 7 resource-dependent combo-certified families (ADR 0026 §4), which are DEFERRED to
    #90. The shipped engine passes ``None``, so no family edge enters the SCC walk; only the
    Phase-1 framework cycles and the Phase-2 lifetrade catalog relays close loops here.
    """
    graph_nodes, edges = unified_event_graph_edges(event_edges, events, nodes, relay_gate_carriers, combo_event_edges)
    recursive: set[str] = set()
    for comp in _graph.strongly_connected_components(graph_nodes, edges):
        if len(comp) < 2:
            continue
        for tag, name in comp:
            if tag == 'e':
                recursive.add(name)
    return recursive


def elect_engine_units(
    nodes: set[str],
    event_edges: list[tuple[str, str, str]],
    attr_weight: defaultdict[str, float],
    assets: StrategyAssets,
    cards: list[dict],
    earned_archetypes: set[str] | None = None,
    combo_event_edges: list[tuple[str, str]] | None = None,
    gate_carriers: frozenset[str] | None = None,
) -> dict:
    """STRUCTURE-ONLY a-priori engine isolation over the DIRECTED event graph.

    Where ``convergent_event_components`` fuses everything weakly-connected into
    one blob (so a land-recursion engine and a sacrifice engine merge through the
    shared ``sac`` relay), this isolates engines WITHOUT transitive chaining:

    * per-event convergence — each event with producer-nodes AND consumer-nodes is
      one atomic bipartite engine (producers -> event -> consumers); no fusion.
    * hub classification — the engine HUB is the producer that is ALSO a *relay*
      (consumes an upstream event + mints this one): the attribute the deck's own
      machinery feeds. A relay hub => ``recursive-engine``; an event minted only by
      pure sources (``ramp``, ``flicker``) => ``static-payoff``.
    * bounded forward chains — the ONLY legal fusion: engine Y feeds engine X when
      X's relay hub consumes Y's event. DAG-bounded (the catalog is a strict
      forward DAG: 0 SCCs), so this never grows into the weak-component blob.
    * commander-anchored loop probe — a value LOOP cannot appear as an SCC in a
      forward DAG, but the commander recurs every turn; seed forward reachability
      at what the commander produces and test whether it reaches back to what the
      commander consumes. Non-empty => a commander-anchored cycle the DAG can't hold.

    The ranked ``ranked_units`` set (0..n strategies) is chosen by node-weighted
    event-neighborhood **mass** -- ``m(U) = Σ attr_weight`` over a unit's producers U
    consumers (its ``mass``). Units are ordered by mass desc (recursive status
    breaks an EXACT mass tie only); the ranked set keeps EVERY substantive unit
    that is not a member-Jaccard-≥-0.5 near-duplicate of a kept unit — rank-don't-
    cut, no relative-mass threshold (ADR 0022, superseding the 0.6 keep-ratio of
    ADR 0014). ``recursive`` / ``interior_node`` / ``commander_coupled`` /
    ``share`` / ``formation_mode`` / ``marginal`` are reported attributes, never election
    inputs. There is no distinguished single ``lead`` — consumers that need "the
    top candidate" read ``ranked_units[0]`` locally.

    Before the election, EVENT-SUBSTANTIVENESS admission (ADR 0002 third level /
    ADR 0020) filters the candidate pool: a unit whose event carries a
    ``produced_by_commander`` Requirement is an election candidate only when the deck's
    command zone anchors that event's production (``determine_event``). The gate is
    DATA-keyed (only ``mana-produced`` today) and reads only commander attrs +
    the event's own refs — never mass. The reported ``engines`` list stays
    complete; exclusion applies to election candidacy only.

    ARCHETYPE-SUBSTANTIVENESS admission (ADR 0002 second level / ADR 0021 B6)
    completes the ordering invariant (substantiveness -> centrality -> synthesis)
    at the ARCHETYPE grain. When ``earned_archetypes`` is supplied (the set of this
    deck's archetypes that clear their ``Requirement.satisfied`` bar — the SAME
    one-named determiner the naming layer consults), a candidate is admitted unless
    its SOLE basis is an UNEARNED archetype claim. The owner-approved exemption
    policy KEEPS a unit that (a) has >=1 EARNED ``payoff_archetype``, or (b) is
    STRUCTURALLY anchored (``commander_coupled``, or a non-bipartite anchor mode —
    terminal-producer / exogenous-environmental), or (c) makes NO archetype claim
    (empty ``payoff_archetypes`` — a pure structural event-loop). Only a unit whose
    payoff archetypes are ALL unearned AND that is not structurally anchored is
    gated. GENERIC — the earned verdict is the existing per-archetype data, no new
    threshold/knob. ``earned_archetypes=None`` (the default) disables the grain for
    the structure-only relay-augmentation path (:mod:`.relay`), which is not the P0
    election. The reported ``engines`` list stays complete; exclusion applies to
    election candidacy only.

    ``gate_carriers`` is the present-attr set a catalog :class:`RelayEntry`'s gate is
    tested against for liveness (PRESENCE, ADR 0026 §1). It defaults to
    ``set(attr_weight)``, which is correct for the store-free ``compute_facts`` path
    where every weight is a native card attribute. The ``engine_report`` path instead
    injects store (haiku) slugs into ``attr_weight`` by name+query match, so it passes a
    ROLE-MATCHED live set: the genuine native carriers (frozen before any store injection)
    PLUS the matched store slugs whose declared role agrees with the gate's catalog relay
    direction. A correct-role matched store slug makes its gate live — that is how a
    legitimate store-backed lifetrade loop closes through the catalog relay edges here —
    while a same-named, wrong-role or zero-match slug is excluded, so it cannot make a
    catalog gate falsely live and mint a phantom recursive engine.

    Purely additive to the label: reported under ``facts['engines']`` and never
    consulted by the naming layer (the label is unchanged; STRUCTURE-ONLY).
    """
    out_adj: dict[str, set[str]] = defaultdict(set)
    in_adj: dict[str, set[str]] = defaultdict(set)
    ev_prod: dict[str, set[str]] = defaultdict(set)
    ev_cons: dict[str, set[str]] = defaultdict(set)
    for p, c_, ev in event_edges:
        out_adj[p].add(c_)
        in_adj[c_].add(p)
        ev_prod[ev].add(p)
        ev_cons[ev].add(c_)
    producers = {n for n in nodes if out_adj.get(n)}
    consumers = {n for n in nodes if in_adj.get(n)}
    relay = producers & consumers

    # True-SCC recursion (ADR 0026): an event is a ``recursive-engine`` iff its
    # node sits on a real directed cycle of the unified CardAttribute | Event
    # graph, replacing the ``producers & consumers`` heuristic. ``relay`` stays as
    # the reported hub signal (the producer that is also a consumer); the SCC
    # decides the recursive-vs-static KIND. Catalog relay gates are tested for
    # PRESENCE (a carrier in the deck = any attr in ``attr_weight``), not identity
    # admission: a lifetrade loop (Sanguine Bond's gain→drain + Exquisite Blood's
    # drain→gain) closes on the two enabler cards even when each gate attr is a
    # 1-2-card sub-threshold attr that never earns an identity node (ADR 0026 §1).
    # ``combo_event_edges`` is the optional injection seam for the 7 combo-certified
    # families (ADR 0026 §4), DEFERRED to #90: the shipped engine passes ``None``, so this
    # walk closes loops only from framework cycles and lifetrade relays.
    # Catalog relay-gate liveness is tested against ROLE-MATCHED live carriers of the
    # gate CardAttribute (``gate_present``), NOT the raw ``attr_weight`` keys: the
    # engine_report path injects store (haiku) slugs into ``attr_weight`` by
    # name+query match, so ``set(attr_weight)`` would admit a same-named, wrong-role
    # store slug as a live catalog gate. ``gate_carriers`` is the native carriers the
    # caller froze before any store injection, UNIONED with the matched store slugs
    # whose role agrees with the gate's catalog relay direction — a correct-role store
    # gate is live, a wrong-role one is not. ``None`` falls back to ``set(attr_weight)``
    # (correct for the store-free ``compute_facts`` path, where every weight is native,
    # keeping that path byte-identical).
    gate_present = gate_carriers if gate_carriers is not None else frozenset(attr_weight)
    # ONE sorted-Tarjan SCC pass over the unified CardAttribute | Event graph. It names
    # both the recursive event set (events on a real directed cycle) AND, per event,
    # the set of events sharing its strongly-connected component (``event_scc``) — the
    # return-path membership the pure-event-cycle hub election reads below. This is the
    # same computation as :func:`recursive_event_set`, kept inline because the hub
    # election needs the per-event component, not just the flattened event set.
    _graph_nodes, _uni_edges = unified_event_graph_edges(
        event_edges, assets.events, nodes, gate_present, combo_event_edges
    )
    recursive_events: set[str] = set()
    event_scc: dict[str, frozenset[str]] = {}
    for _comp in _graph.strongly_connected_components(_graph_nodes, _uni_edges):
        if len(_comp) < 2:
            continue
        _comp_events = frozenset(name for tag, name in _comp if tag == 'e')
        recursive_events |= _comp_events
        for _ev_name in _comp_events:
            event_scc[_ev_name] = _comp_events

    def by_w(xs):
        return sorted(xs, key=lambda a: (-attr_weight[a], a))

    def archs(xs):
        return sorted({x for a in xs if (x := assets.distill_attr(a))})

    engines = []
    for ev in sorted(ev_prod):
        P, C = ev_prod[ev], ev_cons[ev]
        relay_producers = by_w(p for p in P if p in relay)
        hub = relay_producers[0] if relay_producers else None
        engines.append(
            {
                'event': ev,
                'interior_node': hub,
                'enabler': hub or (by_w(P)[0] if P else None),
                'kind': 'recursive-engine' if ev in recursive_events else 'static-payoff',
                'producers': by_w(P),
                'consumers': by_w(C),
                'payoff_archetypes': archs(C),
                # EVENT-NODE MASS (ADR 0026 W3 — owner-confirmed 2026-11-19, option
                # (a): events carry ZERO intrinsic mass; the decision is recorded in
                # ADR 0003 and the drift ledger). Events are first-class NODES for
                # cycle detection, but a unit's mass is the node-weighted CardAttribute
                # neighbourhood ``Σ attr_weight`` over its producer | consumer ATTRS —
                # the ADR 0014 criterion is held. Events have no card carriers, so any
                # intrinsic event weight would be an invented constant that distorts the
                # election; zero is the deterministic, defensible default.
                'mass': round(sum(attr_weight[a] for a in P | C), 1),
            }
        )

    # PURE-EVENT-CYCLE loops (ADR 0026 §1 / #115): a catalog relay loop can make an
    # event RECURSIVE through event→event edges alone, with NO admitted attr
    # producer-consumer pair on that event — so the event never enters ``ev_prod`` and
    # the per-event enumeration above skips it. The real Magic case is a lifetrade loop
    # whose only enablers are sub-threshold gate carriers: Sanguine Bond (``gain-to-
    # drain``) + Exquisite Blood (``drain-to-gain``) close the ``lifegain`` /
    # ``life-loss-drain`` cycle, but each gate is a 1-2-card attr that never earns an
    # identity node, so ``lifegain`` / ``life-loss-drain`` have no in-node attr producer
    # and would be dropped, leaving a deck whose one loop is real reporting ``engines=[]``. Emit a
    # ``recursive-engine`` unit for each such event, using its LIVE gate carriers (the
    # relay enablers present in the deck — the same ``≥1 carrier`` liveness the SCC walk
    # used) as the producer evidence. The unit makes no archetype claim (no attr
    # consumers), so it is admitted as a pure structural event-loop; removing either
    # enabler kills a relay, the SCC opens, and the unit is no longer emitted. Mass stays
    # the node-weighted ``Σ attr_weight`` over the gate carriers (ADR 0014 unchanged;
    # a dead gate carrier contributes 0 via the defaultdict).
    for ev in sorted(recursive_events - set(ev_prod)):
        spec = assets.events.get(ev, {})
        relays = [
            ref for ref in spec.get('produced_by', ()) if isinstance(ref, RelayEntry) and ref.gate in gate_present
        ]
        live_gates = by_w(ref.gate for ref in relays)
        if not live_gates:
            continue
        # HUB = the loop's RETURN-PATH gate, not the globally-heaviest live gate. A
        # relay ``(from_event, gate)`` is the edge ``from_event -> ev``; it closes the
        # cycle only when ``from_event`` sits in ``ev``'s own SCC. A gate whose
        # ``from_event`` is OUTSIDE the component merely FEEDS the loop (e.g. a draw
        # engine: ``draw-a-card -> life-loss-drain`` via ``draw-to-drain``) and must
        # not be named the interior hub. Fall back to the heaviest live gate only when
        # no relay closes from within the component (defensive; a recursive event
        # reached here always has at least one in-component return relay).
        scc = event_scc.get(ev, frozenset())
        return_gates = by_w(ref.gate for ref in relays if ref.from_event in scc)
        hub = return_gates[0] if return_gates else live_gates[0]
        engines.append(
            {
                'event': ev,
                'interior_node': hub,
                'enabler': hub,
                'kind': 'recursive-engine',
                'producers': live_gates,
                'consumers': [],
                'payoff_archetypes': [],
                'mass': round(sum(attr_weight[a] for a in live_gates), 1),
                # Flags the producer-only pure-event-cycle shape so ``_anchor`` gives
                # it its own ``formation_mode`` instead of the ``bipartite`` fallback
                # (ADR 0014 reserves bipartite for a unit with BOTH in-deck sides).
                'relay_loop': True,
            }
        )

    # MASS ELECTION (ADR 0014): node-weighted event-neighborhood mass is the
    # authoritative engine signal. Each unit's mass m(U) = Σ attr_weight over
    # producers | consumers is already computed as e['mass']. Order by mass
    # desc; recursive status breaks an EXACT mass tie only (a reported attribute,
    # never a primary sort key); event name is the final determinism tiebreak.
    engines.sort(key=lambda e: (-e['mass'], e['kind'] != 'recursive-engine', e['event']))

    # bounded forward chains: feeder_event -> fed_event (fed hub consumes feeder)
    chains = []
    for e in engines:
        if e['interior_node'] is None:
            continue
        for e2 in engines:
            if e2['event'] != e['event'] and e['interior_node'] in e2['consumers']:
                chains.append([e2['event'], e['event']])
    chains.sort()

    # commander attrs (used by the event-/archetype-substantiveness gates AND the loop
    # probe). Kept as the raw-otag-derived intersection those consumers already use, so
    # their behaviour — and the parity baselines — are unchanged.
    cmd_idx = [i for i, c in enumerate(cards) if c.get('role') == 'commander']
    cmd_attrs: set[str] = set()
    # Catalog RELAY gates (generic, DATA-keyed): the synthesized edge CardAttributes
    # (gain-to-drain etc.) that author a catalog event->event relay. They are
    # SYNTH_ALWAYS, never a raw Scryfall otag, so the raw-otag rebuild above drops
    # them — yet a commander (e.g. Vito) is often the SOLE carrier of such a gate.
    relay_gates = {
        ref.gate
        for spec in assets.events.values()
        for ref in spec.get('produced_by', ())
        if isinstance(ref, RelayEntry)
    }
    # Commander-COUPLING attrs ADD the commander's synthesized relay-gate attributes
    # to the raw-otag commander attrs. These synth gates are the sole carriers that
    # couple a pure-event-cycle unit to the command zone, so without them a
    # commander-anchored lifetrade loop would report ``commander_coupled=False``.
    # Scoped to relay gates and used ONLY by the reported ``commander_coupled`` field
    # (never the mass election, ADR 0014), so only a pure-event-cycle unit — whose
    # members ARE relay gates — can move; every other unit's members are string event
    # refs that no relay gate shadows, leaving the parity baselines otherwise intact.
    cmd_coupling_attrs: set[str] = set()
    for i in cmd_idx:
        cmd_attrs |= {t.lower() for t in cards[i].get('otags', [])} & nodes
        cmd_coupling_attrs |= synth_attrs_for(cards[i], assets.synth_predicates) & nodes & relay_gates
    cmd_coupling_attrs |= cmd_attrs

    def _members(e: dict) -> set[str]:
        return set(e['producers']) | set(e['consumers'])

    def _jaccard(a: set[str], b: set[str]) -> float:
        return len(a & b) / len(a | b) if (a or b) else 0.0

    def _anchor(e: dict) -> str:
        # A producer-only pure-event-cycle (``relay_loop``) unit is a recursive
        # STRUCTURAL loop closed entirely through event->event relays (ADR 0026 §1): it
        # has gate producers and ``consumers=[]``, so without this it reaches the
        # ``has_p and not has_c`` fallthrough and is mislabeled ``bipartite`` — the mode
        # ADR 0014 reserves for a unit with BOTH in-deck sides. It is its own formation
        # mode (the loop IS the structure; it never reaches ``game-end`` as a producer).
        if e.get('relay_loop'):
            return 'recursive-relay-loop'
        # Generic + structure-derived; NO hardcoded payoff list. The current
        # event_edges are in-deck producer -> in-deck consumer, so every unit is
        # bipartite (anchor mode 1). The terminal-producer (mode 2) and
        # exogenous-environmental (mode 3) anchors populate only once producer-
        # only / consumer-only edges exist (later pipeline steps: mana-wiring,
        # edge-author hydration, exogenous-source modelling). The terminal set is the
        # game-rules-derived reachable-to-game-end set (``_RULE_TERMINAL_EVENTS``),
        # never a frozen payoff literal.
        has_p, has_c = bool(e['producers']), bool(e['consumers'])
        if has_p and has_c:
            return 'bipartite'
        if has_p and e['event'] in _RULE_TERMINAL_EVENTS:
            return 'terminal-producer'
        if has_c and not has_p:
            return 'exogenous-environmental'
        return 'bipartite'

    # EVENT-SUBSTANTIVENESS admission (ADR 0002 third level / ADR 0020): before the
    # mass election, drop any unit whose EVENT fails its ``produced_by_commander`` bar for
    # this deck — the event's engine unit is admitted to ELECTION candidacy only when
    # the command zone anchors its production. GENERIC: keyed on the event's
    # Requirement DATA (``EVENT_SUBSTANTIVENESS``), never an ``if mana`` / deck-name
    # branch; an event with no data row is admitted (today's behavior). The reported
    # ``engines`` list stays COMPLETE — exclusion applies to candidacy only, so
    # diagnostics, chains, hubs and the loop probe are untouched.
    pool = [e for e in engines if determine_event(e['event'], cmd_attrs, assets)]

    # ARCHETYPE-SUBSTANTIVENESS admission (ADR 0002 second level / ADR 0021 B6):
    # after the EVENT grain, gate a candidate whose SOLE basis is an UNEARNED
    # archetype claim. ``earned_archetypes`` is the deck's set of archetypes that
    # clear ``requirement_for(arch).satisfied`` — the SAME one-named determiner the
    # naming layer uses (ADR 0001/0002), passed in by ``compute_facts`` (it is the
    # only caller that gates; the relay path passes None). Exemptions (keep the
    # unit) are owner-approved and GENERIC — no deck-specific logic, no new
    # threshold: (a) >=1 EARNED payoff_archetype, (b) structurally anchored
    # (commander-coupled OR a non-bipartite anchor mode), (c) no archetype claim.
    def _archetype_admitted(e: dict) -> bool:
        claims = e['payoff_archetypes']
        if not claims:  # (c) pure structural event-loop — no archetype claim
            return True
        if any(a in earned_archetypes for a in claims):  # (a) >=1 earned claim
            return True
        if _members(e) & cmd_attrs:  # (b) structural: commander-coupled
            return True
        # (b) structural: a non-bipartite anchor mode (terminal/exogenous); else the
        # unit's SOLE basis is an unearned archetype claim -> gate.
        return _anchor(e) != 'bipartite'

    if earned_archetypes is not None:
        pool = [e for e in pool if _archetype_admitted(e)]

    # Engine SET (0..n): RANK-DON'T-CUT (ADR 0022, #72).  Keep ALL substantive
    # units ranked by mass desc, with ONLY the Jaccard<0.5 near-duplicate dedup —
    # there is no relative-mass cut.  Units are already substantive by
    # construction (determine_node_admitted + producer-anchor event-admission upstream), so
    # a relative-mass cut among them discarded earned engines on a density-
    # sensitive ratio (the retired 0.6·top keep-ratio of ADR 0014).  Centrality
    # RANKS, it does not CUT; substantiveness (upstream) already decided which
    # units exist.
    #
    # recursive / commander_coupled / share / formation_mode / marginal are REPORTED
    # attributes only, never election inputs.
    elected: list[dict] = []
    if pool:
        total_mass = sum(e['mass'] for e in pool) or 1e-6
        kept_members: list[set[str]] = []
        for e in pool:
            mem = _members(e)
            if any(_jaccard(mem, km) >= 0.5 for km in kept_members):
                continue
            anchor = _anchor(e)
            elected.append(
                {
                    'event': e['event'],
                    'interior_node': e['interior_node'],
                    'mass': e['mass'],
                    'share': round(e['mass'] / total_mass, 3),
                    'formation_mode': anchor,
                    'recursive': e['kind'] == 'recursive-engine',
                    'commander_coupled': bool(mem & cmd_coupling_attrs),
                    'marginal': anchor == 'exogenous-environmental',
                    'payoff_archetypes': e['payoff_archetypes'],
                }
            )
            kept_members.append(mem)

    # commander-anchored loop probe
    cmd_loop = None
    if cmd_idx:
        cmd_prod = {a for a in cmd_attrs if a in producers}
        cmd_cons = {a for a in cmd_attrs if a in consumers}
        seen = set(cmd_prod)
        stack = list(cmd_prod)
        while stack:
            u = stack.pop()
            for v in out_adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        back = seen & cmd_cons
        cmd_loop = {
            'commander': sorted({cards[i]['name'] for i in cmd_idx}),
            'produces': by_w(cmd_prod),
            'consumes': by_w(cmd_cons),
            'reaches_back_to': by_w(back),
            'closes': bool(back),
        }

    if elected:
        determinism = 'mass-elected'
    elif cmd_loop is not None and cmd_loop['closes']:
        determinism = 'loop-commander-anchored'
    else:
        determinism = 'loop-or-flat-no-forward-hub'

    return {
        'relay_hubs': by_w(relay),
        'ranked_units': elected,
        'engines': engines,
        'forward_chains': chains,
        'commander_loop': cmd_loop,
        'determinism': determinism,
        'note': 'a-priori directed-graph engine isolation (per-event convergence + '
        'relay hub + DAG-bounded chains + commander loop probe); the ranked '
        '`elected` set comes from node-weighted event-neighborhood MASS '
        "(rank-don't-cut + Jaccard-0.5 dedup, ADR 0022), not recursive-first; "
        'STRUCTURE-ONLY, never decides the label',
    }


def compute_facts(
    deck: dict,
    combos: list[dict] | None = None,
    assets: StrategyAssets | None = None,
) -> FactsContext:
    """Compute the deterministic facts object + the naming-layer context."""
    assets = assets or load_assets()
    cfg = assets.cfg
    cmd_w = cfg.cmd_weight

    if 'cards' not in deck:
        raise ValueError("deck missing 'cards'")
    cards = deck['cards']
    nonland = [c for c in cards if not is_land(c)]
    n_nonland = len(nonland)

    card_attrs: dict[int, set[str]] = {}
    card_closures: dict[int, set[str]] = {}
    for i, c in enumerate(cards):
        closure = {t.lower() for t in c.get('otags', [])}
        card_closures[i] = closure
        # otag-derived attrs via the shared one-named-rule derivation (also used
        # by Phase-A combo_edges, ADR 0021 B1 / 0001). EDHREC family slugs are
        # surfaced onto the card as themselves (ADR 0006).
        attrs = derive_card_attributes(closure, assets)
        # Synth attributes (vehicle/energy/extra-turn/devotion from card fields,
        # morph from keywords) come from the compiled vocab predicates — one
        # resolution path, no hand-coded field tests.
        attrs |= synth_attrs_for(c, assets.synth_predicates)
        card_attrs[i] = attrs

    # -------------------------------------------------- combo detection (per deck)
    # Win classification via the closure-mechanism walk (ADR 0016).
    # The walk is deck-aware: a combo's win-ness depends on the deck's card
    # composition (e.g. infinite mana + a mana_sink card = win).
    combos = combos or []
    have_oid: set[str] = {str(c['oracle_id']) for c in cards if c.get('oracle_id')}
    have_norm: set[str] = {combo_norm(c.get('name', '')) for c in cards}
    detected = combos_in_deck(have_oid, have_norm, combos) if combos else []

    # Run the closure walk for each detected combo to determine win-ness
    for c in detected:
        feature_names = [f.strip() for f in c['result'].split(';') if f.strip()]
        fids = resolve_fids_from_feature_names(feature_names)
        verdict = combo_win_verdict(fids=fids, deck_card_names=have_norm)
        c['win'] = walk_is_win(verdict)
        c['_walk_verdict'] = verdict

    win_combos = [c for c in detected if c['win']]
    # All wins come from the walk — no separate predicate/mana-sink categories
    predicate_wins = win_combos
    mana_sink_wins: list[dict] = []
    mana_sinks_present: list[str] = []

    combo_piece_idx: set[int] = set()
    name2idx: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(cards):
        name2idx[combo_norm(c.get('name', ''))].append(i)
    for c in detected:
        for nn in c['norm_names']:
            for i in name2idx.get(nn, []):
                combo_piece_idx.add(i)

    spell_n = sum(1 for c in cards if not is_land(c) and assets.spell_re.search(c.get('type_line') or ''))
    art_n = sum(1 for c in cards if not is_land(c) and assets.art_re.search(c.get('type_line') or ''))
    ench_n = sum(1 for c in cards if not is_land(c) and assets.ench_re.search(c.get('type_line') or ''))

    tribe_info = compute_tribes(cards, sub_t=assets.sub_t, cmd_weight=cmd_w, typal_dom_share=cfg.typal_dom_share)
    tribe_ranked = tribe_info['ranked']
    dom_sub = tribe_info['top']
    dom_raw = next((d['raw_creatures'] for d in tribe_ranked if d['tribe'] == dom_sub), 0) if tribe_ranked else 0
    density_signals = {
        'spell_density': {'n': spell_n, 'on': spell_n >= assets.spell_t},
        'artifact_density': {'n': art_n, 'on': art_n >= assets.art_t},
        'enchantment_density': {'n': ench_n, 'on': ench_n >= assets.ench_t},
        'creature_type_density': {
            'subtype': dom_sub,
            'n': dom_raw,
            'on': dom_raw >= assets.sub_t,
            'tribes_ranked': [d['tribe'] for d in tribe_ranked[:4]],
        },
    }

    # ---- weighted attribute densities + support cards + commander attrs
    attr_weight: defaultdict[str, float] = defaultdict(float)
    support_cards: dict[str, set[int]] = defaultdict(set)
    cmd_attrs: set[str] = set()
    for i, c in enumerate(cards):
        w = cmd_w if c.get('role') == 'commander' else 1.0
        for a in card_attrs[i]:
            attr_weight[a] += w
            if not is_land(c):
                support_cards[a].add(i)
            if c.get('role') == 'commander':
                cmd_attrs.add(a)

    # Statistical prevalence (ADR 0004): >= 3 carriers, or commander-carried.
    prevalent = {a for a in attr_weight if determine_node_admitted(len(support_cards[a]), a in cmd_attrs)}
    # Loop-aware substantiveness (ADR 0026): add sub-threshold attrs that are
    # structurally load-bearing on a closed cycle among otherwise-substantive
    # nodes. Prevalence alone still suppresses incidental non-loop noise.
    nodes = loop_aware_nodes(prevalent, attr_weight, assets)
    covered = sum(1 for i, c in enumerate(cards) if not is_land(c) and (card_attrs[i] & nodes))
    coverage = covered / n_nonland if n_nonland else 0.0

    attributes_present = {a: round(attr_weight[a], 1) for a in sorted(nodes, key=lambda a: (-attr_weight[a], a))}

    card_w: dict[int, float] = {i: (cmd_w if cards[i].get('role') == 'commander' else 1.0) for i in range(len(cards))}
    arch_members: dict[str, set[int]] = defaultdict(set)
    arch_attrs: dict[str, set[str]] = defaultdict(set)
    for i in range(len(cards)):
        if is_land(cards[i]):
            continue
        for a in card_attrs[i]:
            if a not in nodes:
                continue
            arch = assets.distill_attr(a)
            if arch is None:
                continue
            arch_members[arch].add(i)
            arch_attrs[arch].add(a)

    # ---- T3 typal concentration membership (otag-less dominant-tribe fallback)
    typal_otag_members = set(arch_members.get(TYPAL, set()))
    dominant = set(tribe_info['dominant_tribes'])
    _n_ot = len(typal_otag_members)
    _ot_has_cmd = any(cards[i].get('role') == 'commander' for i in typal_otag_members)
    otag_typal_registered = determine_node_eligible(_n_ot, _ot_has_cmd)
    typal_conc_members: set[int] = set()
    if dominant and not otag_typal_registered:
        for i in range(len(cards)):
            if is_land(cards[i]):
                continue
            if set(creature_subtypes(cards[i])) & dominant:
                typal_conc_members.add(i)
        if typal_conc_members:
            arch_members[TYPAL] |= typal_conc_members
            arch_attrs[TYPAL].add('subtype-concentration[' + ','.join(sorted(dominant)) + ']')
    typal_fired_by = sorted(
        ({'otag'} if typal_otag_members else set()) | ({'concentration'} if typal_conc_members else set())
    )

    # ---- T2 correction 1: tutor-land ENABLER (conditional lands-matter contribution)
    lands_enabler = {'added': [], 'signal_present': False, 'n_candidates': 0, 'base_wq': 0.0}
    lands = 'lands-matter'
    lands_base_mem = set(arch_members.get(lands, set()))
    base_n = len(lands_base_mem)
    base_has_cmd = any(cards[i].get('role') == 'commander' for i in lands_base_mem)
    lands_signal_present = determine_node_eligible(base_n, base_has_cmd)
    land_tutor_cards = sorted(
        i for i in range(len(cards)) if not is_land(cards[i]) and assets.tutor_land_pred(CardView.from_card(cards[i]))
    )
    lands_enabler['signal_present'] = lands_signal_present
    lands_enabler['n_candidates'] = len(land_tutor_cards)
    lands_enabler['base_wq'] = round(sum(card_w[i] for i in lands_base_mem), 1)
    if lands_signal_present:
        for i in land_tutor_cards:
            if i not in arch_members[lands]:
                arch_members[lands].add(i)
                lands_enabler['added'].append(cards[i]['name'])
        if lands_enabler['added']:
            arch_attrs[lands].add('tutor-land[enabler]')

    # identity mass (each identity card counted once; for share-based guards)
    facts_only: set[str] = set(FACTS_ONLY_WHEN_DISABLED)
    identity_cards: set[int] = set()
    for arch, mem in arch_members.items():
        if arch in facts_only:
            continue
        identity_cards |= mem
    identity_mass = sum(card_w[i] for i in identity_cards) or 1e-6

    def requires_met(mem: set[int]) -> bool:
        n = len(mem)
        has_cmd = any(cards[i].get('role') == 'commander' for i in mem)
        return determine_node_eligible(n, has_cmd)

    strategies: dict[str, dict] = {}
    for arch, mem in arch_members.items():
        wq = sum(card_w[i] for i in mem)
        strategies[arch] = {
            'weighted_quantity': round(wq, 1),
            'n_member_cards': len(mem),
            'requires_met': requires_met(mem),
            'contributing_attrs': sorted(arch_attrs[arch], key=lambda a: (-attr_weight[a], a)),
            'member_cards': sorted({cards[i]['name'] for i in mem}),
            'is_identity': arch not in facts_only,
        }
    if TYPAL in strategies:
        strategies[TYPAL]['tribe'] = tribe_info['top']
        strategies[TYPAL]['tribes_ranked'] = tribe_ranked[:6]
        strategies[TYPAL]['fired_by'] = typal_fired_by
        strategies[TYPAL]['dominant_tribes'] = sorted(dominant)

    support_quantities = {}
    for s in sorted(assets.support):
        if s in nodes:
            support_quantities[s] = {'weighted_quantity': round(attr_weight[s], 1), 'n_cards': len(support_cards[s])}

    # ---- EDGES + ENGINES (structure-only)
    pair_co: Counter = Counter()
    nl_ids = [i for i, c in enumerate(cards) if not is_land(c)]
    for i in nl_ids:
        present = [a for a in card_attrs[i] if a in nodes]
        for a, b in combinations(sorted(present), 2):
            pair_co[(a, b)] += 1
    cooc_edges = []
    for (a, b), nab in pair_co.items():
        if nab < 3:
            continue
        na, nb = len(support_cards[a]), len(support_cards[b])
        if na == 0 or nb == 0:
            continue
        lift = nab * n_nonland / (na * nb)
        if lift > 1.2:
            cooc_edges.append((a, b, round(lift, 2), nab))

    # attr→attr event edges (the projected view the communities + per-event unit
    # enumeration walk). A RelayEntry ref is NOT an attr edge — it is interpreted
    # as a gated event→event edge in the unified SCC graph (elect_engine_units /
    # unified_event_graph_edges), so ``_catalog_event_edges`` keeps only string
    # CardAttribute refs here rather than silently dropping the relay (ADR 0026).
    event_edges = _catalog_event_edges(assets.events, nodes)

    weighted = [(a, b, lift) for a, b, lift, _nab in cooc_edges]
    weighted += [(p, c_, 1.5) for p, c_, _ev in event_edges]
    communities = _graph.greedy_modularity_communities(nodes, weighted) or [[n] for n in sorted(nodes)]

    comps_raw = _graph.weakly_connected_components((p, c_) for p, c_, _ev in event_edges)
    # one directed edge per (p, c_) ordered pair, LAST event wins — matches the
    # networkx DiGraph the prototype built (add_edge overwrites the edge attr), so
    # the structure-only engine facts stay byte-identical to the validated output.
    pair_event: dict[tuple[str, str], str] = {}
    for p, c_, ev in event_edges:
        pair_event[(p, c_)] = ev
    out_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (p, c_), ev in pair_event.items():
        out_adj[p].append((c_, ev))
    comps = []
    for comp in comps_raw:
        evset: set[str] = set()
        chains: list[str] = []
        for u in comp:
            for v, ev in out_adj.get(u, []):
                if v in comp:
                    evset.add(ev)
                    chains.append(f'{u} --{ev}--> {v}')
        comps.append(
            {
                'attrs': sorted(comp, key=lambda a: (-attr_weight[a], a)),
                'events': sorted(evset),
                'chains': sorted(chains),
                'size': len(comp),
                'n_events': len(evset),
                'weight': round(sum(attr_weight[a] for a in comp), 1),
                'archetypes': sorted({x for a in comp if (x := assets.distill_attr(a))}),
            }
        )
    comps.sort(key=lambda c: (-c['n_events'], -c['size'], -c['weight'], ','.join(c['attrs'])))

    engine_communities = []
    for cl in communities:
        cl_nodes = [a for a in cl if a in nodes]
        if len(cl_nodes) < 3:
            continue
        archs: Counter = Counter()
        for a in cl_nodes:
            x = assets.distill_attr(a)
            if x:
                archs[x] += 1
        engine_communities.append(
            {
                'size': len(cl_nodes),
                'weight': round(sum(attr_weight[a] for a in cl_nodes), 1),
                'archetypes_merged': sorted(archs),
                'top_attrs': sorted(cl_nodes, key=lambda a: (-attr_weight[a], a))[:8],
            }
        )
    engine_communities.sort(key=lambda e: (-e['weight'], -e['size'], ','.join(e['top_attrs'])))

    # ---- T4 degree-axis + power readings
    deck_ci = sorted({x for c in cards for x in (c.get('color_identity') or [])})
    degree_axes = compute_degree_axes(cards, deck_ci, over_t=cfg.over_t)
    power = power_profile(cards)
    prot_axis = degree_axes['protection']
    protection_degree = {
        'n': prot_axis['n'],
        'frac': prot_axis['frac'],
        'over_served': prot_axis['over_served'],
        'under_served': prot_axis['under_served'],
        'reading': (
            'resilience/voltron'
            if prot_axis['over_served']
            else ('susceptible' if prot_axis['under_served'] else 'baseline')
        ),
    }

    # ---- T5 A: core_combo facts + elevation
    def _core_combo(c: dict, kind: str) -> dict:
        pieces = sorted(c['card_names'])
        orphan = sorted(
            {cards[i]['name'] for nn in c['norm_names'] for i in name2idx.get(nn, []) if i not in identity_cards}
        )
        return {
            'variant_id': c['variant_id'],
            'label': combo_label(c['result']),
            'result': c['result'],
            'requires': pieces,
            'n_pieces': len(pieces),
            'win': c['win'],
            'win_kind': kind,
            'elevated_orphan_pieces': orphan,
        }

    core_combos = [_core_combo(c, walk_win_kind(c.get('_walk_verdict', {}))) for c in win_combos] + [
        _core_combo(c, 'non-win') for c in detected if c not in win_combos
    ]
    core_combos.sort(key=lambda d: (not d['win'], d['win_kind'], d['label'], d['variant_id']))
    elevated_cards = sorted({cards[i]['name'] for i in combo_piece_idx if i not in identity_cards})
    answered_by = _compute_answered_by(cards, strategies)

    facts = {
        'attributes_present': attributes_present,
        'density_signals': density_signals,
        'strategies': strategies,
        'core_combos': core_combos,
        'combo_elevation': {
            'n_combo_pieces': len(combo_piece_idx),
            'n_elevated_orphans': len(elevated_cards),
            'elevated_cards': elevated_cards,
            'note': 'combo pieces never density-drop; elevated_cards had NO identity archetype '
            '(orphan noise) but are surfaced because they complete a detected combo',
        },
        'answered_by': answered_by,
        'support_quantities': support_quantities,
        'degree_axes': degree_axes,
        'power_profile': power,
        'protection_degree': protection_degree,
        'engines': {
            'convergent_event_components': comps[:4],
            'modularity_communities': engine_communities[:5],
            # engine_units is computed AFTER ctx (below): the ARCHETYPE-grain
            # election gate (ADR 0021 B6) consults each archetype's
            # ``Requirement.satisfied`` verdict, which reads ctx.
            'engine_units': None,
            'note': 'engines/communities are STRUCTURE-ONLY; they do not decide the label',
        },
        'edges': {
            'n_cooc': len(cooc_edges),
            'n_event': len(event_edges),
            'top_cooc': sorted(cooc_edges, key=lambda e: (-e[2], e[0], e[1]))[:8],
        },
        'lands_enabler': {
            'signal_present': lands_enabler['signal_present'],
            'n_land_tutors': lands_enabler['n_candidates'],
            'base_lands_wq': lands_enabler['base_wq'],
            'enabler_added': sorted(lands_enabler['added']),
        },
        'tribes': {
            'top': tribe_info['top'],
            'ranked': tribe_ranked[:8],
            'concentrated_tribes': tribe_info['concentrated_tribes'],
            'dominant_tribes': sorted(dominant),
            'typal_fired_by': typal_fired_by,
            'concentration_applied': bool(typal_conc_members),
            'otag_typal_registered': otag_typal_registered,
            'note': 'tribe = 3-source (subtype+oracle-text+typal-otag) commander-weighted vote; '
            'typal archetype fires on subtype concentration independent of typal-* otags',
        },
        'coverage': round(coverage, 3),
        'n_nodes': len(nodes),
    }

    demoted_archetypes: list[str] = []  # populated post-ctx (satisfied() reads ctx)

    arch_has_cmd = {arch: any(cards[i].get('role') == 'commander' for i in mem) for arch, mem in arch_members.items()}

    ctx = FactsContext(
        deck=deck,
        cards=cards,
        n_nonland=n_nonland,
        assets=assets,
        cfg=cfg,
        facts=facts,
        strategies=strategies,
        facts_only=facts_only,
        arch_members=dict(arch_members),
        arch_has_cmd=arch_has_cmd,
        nodes=nodes,
        support=assets.support,
        attr_weight=attr_weight,
        card_w=card_w,
        identity_mass=identity_mass,
        coverage=coverage,
        demoted_archetypes=demoted_archetypes,
        cs_axis=degree_axes['counterspells'],
        stax_axis=degree_axes['stax'],
        ramp_axis=degree_axes['ramp'],
        protection_degree=protection_degree,
        power=power,
        tribe_info=tribe_info,
        density_signals=density_signals,
        cmd_attrs=cmd_attrs,
        detected=detected,
        win_combos=win_combos,
        predicate_wins=predicate_wins,
        mana_sink_wins=mana_sink_wins,
        mana_sinks_present=mana_sinks_present,
    )

    from pipeline.transforms.strategy.determine_strategy_substantiveness import requirement_for

    # ARCHETYPE-grain election gate (ADR 0021 B6): the set of this deck's archetypes
    # that clear their substantiveness bar, via the ONE named determiner
    # (``requirement_for(arch).satisfied``, ADR 0001/0002) — the SAME verdict the
    # naming layer consults. ``satisfied`` reads ctx, so this runs post-ctx; the
    # resulting engine_units is then placed into the (shared) facts dict.
    earned_archetypes = {arch for arch, st in strategies.items() if requirement_for(arch, cfg).satisfied(st, ctx, arch)}
    # The 7 resource-dependent combo-certified families (ETB/death, mana-refund, cast/copy,
    # draw-wheel, untap-mana, counter, landfall) are DEFERRED to #90 (ADR 0026 §4): three
    # adversarial reviews found every corpus false-positive concentrated in that family layer
    # (executability / quantity / role / event-mapping bugs), so no combo-certified family edge
    # is promoted into the SCC walk. ``combo_event_edges`` stays ``None`` here; the generic
    # injection seam (``elect_engine_units``' optional arg) is retained for the #90 re-add. Only
    # the Phase-1 framework and the Phase-2 lifetrade catalog relays close loops in this view.
    facts['engines']['engine_units'] = elect_engine_units(
        nodes, event_edges, attr_weight, assets, cards, earned_archetypes, None
    )

    # demoted_archetypes (ADR 0002, GENERIC -- no archetype-name branch): an archetype
    # that clears the presence bar (`requires_met`) but fails its fuller
    # `Requirement.satisfied()` (a density/axis bar) is present-but-not-substantive, so
    # it is demoted. Driven entirely by the per-archetype Requirement DATA: for default
    # `member_count` archetypes satisfied() == requires_met, so ONLY archetypes with a
    # stricter Requirement (graveyard `attr_density`, control `axis_density`) can appear.
    # Reuses `earned_archetypes` (= the satisfied() set, computed just above) so
    # satisfied() is not re-determined. Replaces the hardcoded graveyard `gy_demoted`
    # branch (and the dead `spell_demoted` flag).
    ctx.demoted_archetypes.extend(
        sorted(arch for arch, st in strategies.items() if st.get('requires_met') and arch not in earned_archetypes)
    )
    return ctx
