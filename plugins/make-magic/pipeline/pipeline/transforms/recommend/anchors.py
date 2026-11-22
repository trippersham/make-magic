"""Anchor enumeration + saturation over a deck's existing strategy outputs (Phase 2).

An **anchor** is an existing graph instance in a deck's induced subgraph that a
recommendation can attach to — the directed side of an event, a slot in an
elected engine, a strategy, or a bare card-attribute (design §"mechanism"). This
module turns a resolved deck into its anchors and measures each one's
**saturation** so later phases know whether an anchor wants *more of the same*
(``augment``) or wants *its thin side completed* (``complement``).

Read-only consumption of the strategy engine
---------------------------------------------
Everything here reads :func:`~pipeline.transforms.strategy.strategy_graph.compute_facts`
output and the committed catalogs; nothing recomputes classification and nothing
mutates the :class:`~pipeline.transforms.strategy.strategy_graph.FactsContext`.
``resolve(deck)`` / ``archetype_identity`` stay byte-identical before and after an
enumeration (asserted in the tests). The per-attribute card incidence the engine
builds internally (``support_cards``) is not exposed on ``FactsContext``, so it is
reconstructed here through the SAME one-named derivation the engine uses
(:func:`~pipeline.transforms.strategy.derive_card_attributes.derive_card_attributes`
+ :func:`~pipeline.transforms.strategy.distill_strategies.synth_attrs_for`) — a
read-only re-derivation over already-resolved cards, not a parallel rule (ADR 0001
one-named-rule), so it cannot drift from the engine's own incidence.

Saturation (the deck-side "before" state)
------------------------------------------
For an anchor side:

* **support** — distinct non-land cards feeding that side.
* **connectivity** — distinct non-land cards on the opposing side (the cards this
  side structurally serves / is served by).

Both are card counts, so they are directly comparable. A side is a
``complement`` anchor when it is the thin half of a lopsided edge — its support is
below the opposing side it connects to — and that opposing side is itself
substantive (clears the presence floor). Otherwise it is an ``augment`` anchor
(already well-supplied; adding more deepens the same role). The worked example:
in a payoff-rich / producer-thin landfall deck the producer side of
``land-enters-battlefield`` has fewer enabler cards than the payoff cards it
feeds, so it is a ``complement`` anchor; its consumer (payoff) side is ``augment``.

v1 scope: the four STRUCTURAL anchor kinds (``event_side``, ``engine_slot``,
``card_attribute``, ``strategy``). ``combo`` and ``degree_axis`` anchors are
deferred (design resolution #4); degree axes remain available as a saturation
input for that later kind but are not emitted here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from pipeline.transforms.recommend.contracts import AnchorKind, Direction, FactSnapshot
from pipeline.transforms.strategy.assets import StrategyAssets, is_land, load_assets
from pipeline.transforms.strategy.config import StrategyConfig
from pipeline.transforms.strategy.derive_card_attributes import derive_card_attributes
from pipeline.transforms.strategy.distill_strategies import synth_attrs_for
from pipeline.transforms.strategy.strategy_graph import FactsContext, compute_facts

#: The two saturation verdicts. ``complement`` = the thin side of a lopsided edge
#: (worth completing); ``augment`` = an already-supplied side (worth deepening).
AnchorMode = Literal['complement', 'augment']

#: The v1 structural anchor kinds this module emits. ``combo`` / ``degree_axis``
#: (the two members of :data:`~pipeline.transforms.recommend.contracts.AnchorKind`
#: absent here) are deferred, so an emitted anchor's ``kind`` is always one of
#: these four.
V1_ANCHOR_KINDS: frozenset[AnchorKind] = frozenset({'event_side', 'engine_slot', 'card_attribute', 'strategy'})


@dataclass(frozen=True)
class SaturationConfig:
    """Thresholds for the ``complement`` vs ``augment`` verdict — DATA, not a code branch.

    Kept as one config object (no per-strategy ``if`` ladder, per the engine
    principle) so the whole saturation policy is inspectable in one place.

    Attributes
    ----------
    complement_ratio_t
        A side is thin when ``support < complement_ratio_t * connectivity``.
        ``1.0`` means "a side is thin when it feeds more cards than feed it" — the
        structurally lopsided edge. Raising it is stricter (flags more sides thin).
    substantive_side_floor
        The opposing side must have at least this many cards to count as "rich
        enough to be worth complementing". Mirrors the ADR-0004 presence gate
        (``determine_node_eligible``'s ``support >= 3`` payoff floor): below it a
        side is not substantive, so the thin half is noise, not a real hole.
    """

    complement_ratio_t: float = 1.0
    substantive_side_floor: int = 3


#: The default saturation policy, reused by :func:`enumerate_anchors`.
DEFAULT_SATURATION = SaturationConfig()


def classify_saturation(support: int, connectivity: int, cfg: SaturationConfig = DEFAULT_SATURATION) -> AnchorMode:
    """Verdict for one anchor side from its ``(support, connectivity)`` pair.

    ``complement`` iff the opposing side is substantive (``connectivity`` clears
    the presence floor) AND this side is the thinner half
    (``support < complement_ratio_t * connectivity``); otherwise ``augment``. A
    side with nothing substantive on the other end is always ``augment`` — there
    is no rich counterpart to complete.
    """
    if connectivity >= cfg.substantive_side_floor and support < cfg.complement_ratio_t * connectivity:
        return 'complement'
    return 'augment'


@dataclass(frozen=True)
class Anchor:
    """One enumerated anchor + its deck-side saturation (the "before" state).

    ``ref`` is the stable catalog id (event name / strategy name / attribute name);
    ``direction`` is the side within a directed anchor (``producer`` / ``consumer``
    for an event, ``hub`` / ``payoff`` for an engine slot) or ``None`` for an
    undirected node anchor. ``matched_attributes`` are the admitted node attributes
    that define this side's demand set (the Phase-3 query is built from them);
    ``path`` is the directed edge descriptor that makes the side structural.
    """

    kind: AnchorKind
    ref: str
    direction: Direction
    support: int
    connectivity: int
    mode: AnchorMode
    matched_attributes: tuple[str, ...] = ()
    #: The attributes on the FAR side of this anchor's structural edge (the consumers
    #: for a producer side, the producers for a consumer side, the opposite-event
    #: attrs for an undirected node / strategy). ``connectivity`` is the distinct
    #: non-land card count over these; a candidate that ALSO matches one of them adds a
    #: new neighbour, which is how Phase 3 computes the connectivity marginal.
    opposite_attributes: tuple[str, ...] = ()
    path: tuple[str, ...] = ()

    def to_fact_snapshot(self) -> FactSnapshot:
        """Project the deck-side "before" state onto a Phase-1 :class:`FactSnapshot`.

        Fills the ``*_before`` support/connectivity and the ``matched_attributes`` /
        ``path`` the anchor already knows. The ``*_after`` deltas stay zero: the
        candidate-add "after" state is Phase 3's to compute.
        """
        return FactSnapshot(
            matched_attributes=list(self.matched_attributes),
            path=list(self.path),
            support_before=self.support,
            connectivity_before=self.connectivity,
        )


@dataclass
class _Incidence:
    """Card-level incidence for a resolved deck, reconstructed read-only.

    ``by_attr`` maps each admitted node attribute to the set of non-land card
    indices that carry it — the engine's internal ``support_cards`` for admitted
    nodes, rebuilt through the shared derivation. ``opposite`` maps each attribute
    to the attributes sitting across an incident event edge, so an undirected node
    anchor can measure the cards it structurally connects to.
    """

    by_attr: dict[str, frozenset[int]]
    opposite: dict[str, frozenset[str]] = field(default_factory=dict)

    def support(self, attrs: Iterable[str]) -> int:
        """Distinct non-land cards feeding ``attrs`` (the union of their incidence)."""
        cards: set[int] = set()
        for a in attrs:
            cards |= self.by_attr.get(a, frozenset())
        return len(cards)


def _build_incidence(ctx: FactsContext) -> _Incidence:
    """Reconstruct the admitted-node card incidence + opposite-side map for a deck.

    Read-only: it re-derives each non-land card's attributes through the engine's
    own one-named derivation and keeps only admitted nodes, then walks the event
    catalog's producer/consumer sides (restricted to present nodes) to record, per
    attribute, the attributes on the far side of every incident event edge.
    """
    assets = ctx.assets
    by_attr: dict[str, set[int]] = defaultdict(set)
    for i, card in enumerate(ctx.cards):
        if is_land(card):
            continue
        closure = {t.lower() for t in card.get('otags', [])}
        attrs = derive_card_attributes(closure, assets) | synth_attrs_for(card, assets.synth_predicates)
        for a in attrs:
            if a in ctx.nodes:
                by_attr[a].add(i)

    opposite: dict[str, set[str]] = defaultdict(set)
    for ev in assets.events.values():
        producers = [a for a in ev['produced_by'] if a in ctx.nodes]
        consumers = [a for a in ev['triggers'] if a in ctx.nodes]
        if not (producers and consumers):
            continue
        for p in producers:
            opposite[p].update(consumers)
        for c in consumers:
            opposite[c].update(producers)

    return _Incidence(
        by_attr={a: frozenset(s) for a, s in by_attr.items()},
        opposite={a: frozenset(s) for a, s in opposite.items()},
    )


def _present_event_sides(ctx: FactsContext) -> dict[str, tuple[list[str], list[str]]]:
    """Events in the induced subgraph → their present ``(producers, consumers)`` nodes.

    An event is in the subgraph when BOTH a producer node and a consumer node are
    present (the condition under which the engine forms its directed event edges),
    so both sides can be measured against each other.
    """
    out: dict[str, tuple[list[str], list[str]]] = {}
    for name, ev in ctx.assets.events.items():
        producers = sorted(a for a in ev['produced_by'] if a in ctx.nodes)
        consumers = sorted(a for a in ev['triggers'] if a in ctx.nodes)
        if producers and consumers:
            out[name] = (producers, consumers)
    return out


def _event_side_anchors(ctx: FactsContext, inc: _Incidence, cfg: SaturationConfig) -> list[Anchor]:
    """Producer + consumer anchors for every event in the deck's induced subgraph."""
    anchors: list[Anchor] = []
    for event, (producers, consumers) in _present_event_sides(ctx).items():
        prod_support, cons_support = inc.support(producers), inc.support(consumers)
        anchors.append(
            Anchor(
                kind='event_side',
                ref=event,
                direction='producer',
                support=prod_support,
                connectivity=cons_support,
                mode=classify_saturation(prod_support, cons_support, cfg),
                matched_attributes=tuple(producers),
                opposite_attributes=tuple(consumers),
                path=(f'{event}/producer', f'{event}/consumer'),
            )
        )
        anchors.append(
            Anchor(
                kind='event_side',
                ref=event,
                direction='consumer',
                support=cons_support,
                connectivity=prod_support,
                mode=classify_saturation(cons_support, prod_support, cfg),
                matched_attributes=tuple(consumers),
                opposite_attributes=tuple(producers),
                path=(f'{event}/consumer', f'{event}/producer'),
            )
        )
    return anchors


def _engine_slot_anchors(ctx: FactsContext, inc: _Incidence, cfg: SaturationConfig) -> list[Anchor]:
    """Hub + payoff slot anchors for each ELECTED engine unit.

    Reads the native election already on ``facts['engines']['engine_units']`` (no
    machine-store load, no second engine pass). Each ranked unit is keyed by its
    driving event; the hub slot is the unit's relay hub attribute (absent for a
    static payoff) and the payoff slot is its consumer side. Distinct from
    ``event_side``: only the deck's AUTHORITATIVE engines get slots, not every
    directed edge.
    """
    engine_units = ctx.facts['engines']['engine_units']
    by_event = {e['event']: e for e in engine_units['engines']}
    anchors: list[Anchor] = []
    for unit in engine_units['ranked_units']:
        event = unit['event']
        engine = by_event.get(event)
        if engine is None:
            continue
        producers = sorted(a for a in engine['producers'] if a in ctx.nodes)
        consumers = sorted(a for a in engine['consumers'] if a in ctx.nodes)
        prod_support, cons_support = inc.support(producers), inc.support(consumers)
        if consumers:
            anchors.append(
                Anchor(
                    kind='engine_slot',
                    ref=event,
                    direction='payoff',
                    support=cons_support,
                    connectivity=prod_support,
                    mode=classify_saturation(cons_support, prod_support, cfg),
                    matched_attributes=tuple(consumers),
                    opposite_attributes=tuple(producers),
                    path=(f'{event}/payoff', f'{event}/producer'),
                )
            )
        hub = unit['interior_node']
        if hub and hub in ctx.nodes:
            hub_support = inc.support([hub])
            anchors.append(
                Anchor(
                    kind='engine_slot',
                    ref=event,
                    direction='hub',
                    support=hub_support,
                    connectivity=cons_support,
                    mode=classify_saturation(hub_support, cons_support, cfg),
                    matched_attributes=(hub,),
                    opposite_attributes=tuple(consumers),
                    path=(f'{event}/hub', f'{event}/consumer'),
                )
            )
    return anchors


def _card_attribute_anchors(ctx: FactsContext, inc: _Incidence, cfg: SaturationConfig) -> list[Anchor]:
    """One undirected anchor per admitted node attribute.

    ``support`` is the attribute's own card count; ``connectivity`` is the distinct
    cards on the far side of every event edge the attribute touches (zero when it
    sits on no event edge, so a lone node is ``augment`` — nothing structural to
    complete).
    """
    anchors: list[Anchor] = []
    for attr in sorted(ctx.nodes):
        support = inc.support([attr])
        opposite = tuple(sorted(inc.opposite.get(attr, frozenset())))
        connectivity = inc.support(opposite)
        anchors.append(
            Anchor(
                kind='card_attribute',
                ref=attr,
                direction=None,
                support=support,
                connectivity=connectivity,
                mode=classify_saturation(support, connectivity, cfg),
                matched_attributes=(attr,),
                opposite_attributes=opposite,
                path=(attr,),
            )
        )
    return anchors


def _strategy_anchors(ctx: FactsContext, inc: _Incidence, cfg: SaturationConfig) -> list[Anchor]:
    """One undirected anchor per distilled strategy present on the deck.

    ``support`` reuses the engine's own ``n_member_cards`` (non-land members);
    ``connectivity`` is the distinct cards reachable across event edges incident to
    the strategy's distill-source (``contributing_attrs``).
    """
    anchors: list[Anchor] = []
    for arch in sorted(ctx.strategies):
        st = ctx.strategies[arch]
        contributing = tuple(st['contributing_attrs'])
        neighbours: set[str] = set()
        for a in contributing:
            neighbours |= inc.opposite.get(a, frozenset())
        support = int(st['n_member_cards'])
        connectivity = inc.support(neighbours)
        anchors.append(
            Anchor(
                kind='strategy',
                ref=arch,
                direction=None,
                support=support,
                connectivity=connectivity,
                mode=classify_saturation(support, connectivity, cfg),
                matched_attributes=contributing,
                opposite_attributes=tuple(sorted(neighbours)),
                path=(arch,),
            )
        )
    return anchors


def _anchor_sort_key(a: Anchor) -> tuple[str, str, str]:
    """Deterministic ordering key — ``None`` direction sorts as the empty string."""
    return (a.kind, a.ref, a.direction or '')


def enumerate_anchors(
    deck: dict,
    *,
    combos: list[dict] | None = None,
    assets: StrategyAssets | None = None,
    cfg: StrategyConfig | None = None,
    saturation: SaturationConfig = DEFAULT_SATURATION,
) -> list[Anchor]:
    """Enumerate a resolved deck's v1 structural anchors with their saturation.

    Resolves the deck's facts once (read-only; classification is left byte-stable),
    reconstructs the card incidence, and emits the four structural anchor kinds —
    ``event_side`` (producer + consumer per induced-subgraph event), ``engine_slot``
    (hub + payoff per elected engine), ``card_attribute`` (per admitted node), and
    ``strategy`` (per distilled strategy). ``combo`` / ``degree_axis`` are deferred
    and never emitted. The returned list is deterministically ordered by
    ``(kind, ref, direction)``.
    """
    if assets is None:
        assets = load_assets(cfg)
    elif cfg is not None and assets.cfg is not cfg:
        raise ValueError('cfg and assets.cfg disagree; pass only one')
    ctx = compute_facts(deck, combos or [], assets)
    inc = _build_incidence(ctx)
    anchors = [
        *_event_side_anchors(ctx, inc, saturation),
        *_engine_slot_anchors(ctx, inc, saturation),
        *_card_attribute_anchors(ctx, inc, saturation),
        *_strategy_anchors(ctx, inc, saturation),
    ]
    anchors.sort(key=_anchor_sort_key)
    return anchors
