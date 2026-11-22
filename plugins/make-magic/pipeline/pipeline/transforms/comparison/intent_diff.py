"""Layer ④ — surgical intent diff: which sub-engine each deck pours its best cards into.

Identity/engine/execution all flatten the sharpest signal the user named
(``FINDINGS.md`` §"THE SURGICAL LAYER"): *which specific sub-engine a deck invests its
best cards in* — World Reclaimer is about the land-SAC loop (not just "landfall");
Wakanda is about powerful ≥4-MV artifact payoffs feeding T'Challa (not just "artifacts").

The spike's hardened ``intent_diff_v2`` fixed both v1 wrinkles and PASSED a blind test, so
this re-homes v2's two design decisions:

1. **Stable, event-anchored unit.** Intents are keyed to the fixed **event catalog**
   (``engine['events']`` — one vocabulary for every deck), not per-deck modularity
   communities, so A and B are always comparable event-for-event. Per-event participation
   is the DISCOVERY mechanism: a card participates in an event when its derived attributes
   overlap that event's producer/consumer attrs.
2. **Signature cards joined by oracle_id.** The cards carrying a divergent event are joined
   to their attributes by **oracle_id** — never by name, so split / MDFC / ``|MSC|`` naming
   never mis-joins.

**Ranking is the Phase-2 aid, not the discovery.** The spike ranked signature cards by an
ad-hoc engine-participation heuristic; here that heuristic only DISCOVERS participants
(count / mass), and the Phase-2 :func:`~pipeline.transforms.comparison.strength.strength`
value-density scorer *ranks* the discovered signature cards (a ranking aid passed in, never
the gate). With no ranker the cards fall back to participation order.

Pure functions over two **intent surfaces** — ``{engine, card_attributes}`` as a
:class:`~pipeline.transforms.comparison.cache.DeckDump` carries them; nothing here calls
the engine or the lake (a ranker callable, if any, owns its own data at the caller's edge).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: A per-event |A-B| participation-value gap at/above this is a *divergent* event (the
#: spike's threshold); the deck that invests more carries the intent.
_DIVERGENCE_THRESHOLD = 6

#: How many signature cards to keep per divergent event.
_SIGNATURE_LIMIT = 8

#: The ≥N-MV payoff lens (the "powerful high-MV bench" the user named for Wakanda).
_HIGH_MV = 4.0

#: A ranker maps an intent card record → a sortable strength scalar (higher = stronger).
#: ``None`` ⇒ discovery order (participation value) is used.
Ranker = Callable[[dict[str, Any]], float]


def _events(engine: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``event -> {producers:[attrs], consumers:[attrs], ...}`` — the stable anchor."""
    return engine.get('events') or {}


def _recursive_events(engine: dict[str, Any]) -> set[str]:
    return {u['event'] for u in (engine.get('ranked_units') or []) if u.get('recursive')}


def _relay_hubs(engine: dict[str, Any]) -> set[str]:
    return set(engine.get('relay_hubs') or [])


@dataclass(frozen=True)
class IntentCard:
    """One card's participation in one event (the signature-card unit).

    Attributes:
        oracle_id: the join key (never name).
        name: display name.
        mv: converted mana cost (the high-MV bench lens reads this).
        produces / consumes: whether the card's attrs produce / consume the event.
        participation: structural value — 1 per produce/consume, +2 loop participant
            (both), +2 relay hub, +1 repeatable, +1 commander (the spike's v2 weight).
            This DISCOVERS and orders by default; a ranker overrides the order.
        carried_attrs: the event's attrs this card carries (the evidence).
        strength: the Phase-2 ranking scalar when a ranker is supplied, else None.
    """

    oracle_id: str
    name: str
    mv: float
    produces: bool
    consumes: bool
    participation: int
    carried_attrs: list[str]
    strength: float | None = None


def _participation(
    card_attrs: set[str], producers: set[str], consumers: set[str], relay: set[str], *, commander: bool
) -> int:
    prod, cons = bool(card_attrs & producers), bool(card_attrs & consumers)
    val = (1 if prod else 0) + (1 if cons else 0)
    if prod and cons:
        val += 2  # loop participant on this event
    if card_attrs & relay:
        val += 2  # named relay hub
    if any(a.startswith('repeatable-') for a in card_attrs):
        val += 1
    if commander:
        val += 1
    return val


def _signature_cards(
    event: str,
    engine: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    ranker: Ranker | None,
) -> list[IntentCard]:
    """Cards that carry ``event`` (attrs overlap its producers/consumers), ranked.

    Discovery = participation (oracle_id-joined). Ordering = the Phase-2 ``ranker`` when
    supplied (strength / value-density), else participation value then MV.
    """
    mem = _events(engine).get(event, {})
    producers, consumers = set(mem.get('producers') or []), set(mem.get('consumers') or [])
    relay = _relay_hubs(engine)
    out: list[IntentCard] = []
    for c in cards:
        attrs = set(c.get('attrs') or [])
        prod, cons = bool(attrs & producers), bool(attrs & consumers)
        if not (prod or cons):
            continue
        rec = {
            'oracle_id': c.get('oracle_id'),
            'name': c.get('name'),
            'mv': float(c.get('mv') or 0.0),
            'attrs': attrs,
            'commander': bool(c.get('commander')),
        }
        strength = ranker(rec) if ranker is not None else None
        out.append(
            IntentCard(
                oracle_id=str(c.get('oracle_id')),
                name=str(c.get('name')),
                mv=float(c.get('mv') or 0.0),
                produces=prod,
                consumes=cons,
                participation=_participation(attrs, producers, consumers, relay, commander=bool(c.get('commander'))),
                carried_attrs=sorted(attrs & (producers | consumers)),
                strength=strength,
            )
        )
    if ranker is not None:
        out.sort(key=lambda ic: (-(ic.strength or 0.0), -ic.participation, -ic.mv, ic.name))
    else:
        out.sort(key=lambda ic: (-ic.participation, -ic.mv, ic.name))
    return out


def event_investment(engine: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-event participation profile: ``event -> {total, n_cards, recursive}``.

    ``total`` sums each participating card's participation value; ``n_cards`` counts the
    participants. This is the stable, event-anchored investment vector both decks share.
    """
    relay = _relay_hubs(engine)
    recursive = _recursive_events(engine)
    profile: dict[str, dict[str, Any]] = {}
    for ev, mem in _events(engine).items():
        producers, consumers = set(mem.get('producers') or []), set(mem.get('consumers') or [])
        total = 0
        n = 0
        for c in cards:
            attrs = set(c.get('attrs') or [])
            if not (attrs & producers or attrs & consumers):
                continue
            total += _participation(attrs, producers, consumers, relay, commander=bool(c.get('commander')))
            n += 1
        profile[ev] = {'total': total, 'n_cards': n, 'recursive': ev in recursive}
    return profile


@dataclass(frozen=True)
class EventDivergence:
    """One event's A→B investment divergence + the signature cards carrying it.

    Attributes:
        event: the event (stable anchor).
        a_total / b_total: per-event participation value on each side.
        delta: ``b_total - a_total`` (positive ⇒ B invests more).
        a_n_cards / b_n_cards: participant counts.
        recursive: whether either deck closes a recursive loop on this event (#90-honest).
        divergent: whether ``|delta|`` ≥ the threshold (a real lean, not noise).
        leader: ``'A'`` / ``'B'`` / ``'tie'`` — who pours more into this event.
        a_signature / b_signature: the signature cards per side (ranked; oracle_id-joined).
        a_high_mv / b_high_mv: the ≥4-MV payoff bench carrying this intent per side.
    """

    event: str
    a_total: int
    b_total: int
    delta: int
    a_n_cards: int
    b_n_cards: int
    recursive: bool
    divergent: bool
    leader: str
    a_signature: list[IntentCard]
    b_signature: list[IntentCard]
    a_high_mv: list[IntentCard]
    b_high_mv: list[IntentCard]


@dataclass(frozen=True)
class IntentDiff:
    """The surgical intent delta between two decks (A → B).

    Attributes:
        events: every event either deck invests in, by combined investment descending —
            each an :class:`EventDivergence`.
        divergent_events: the subset whose ``|delta|`` clears the threshold (the leans).
        reading: a short human summary of the top divergences.
    """

    events: list[EventDivergence]
    divergent_events: list[EventDivergence]
    reading: str


def _high_mv(sig: list[IntentCard]) -> list[IntentCard]:
    return sorted((ic for ic in sig if ic.mv >= _HIGH_MV), key=lambda ic: (-ic.mv, ic.name))


def _reading(divergent: list[EventDivergence], a_name: str, b_name: str) -> str:
    if not divergent:
        return f'{a_name} and {b_name} invest alike across events (no event clears the divergence threshold).'
    lines: list[str] = []
    for d in divergent[:4]:
        leader_name = a_name if d.leader == 'A' else b_name
        sig = d.a_signature if d.leader == 'A' else d.b_signature
        carriers = ', '.join(f'{ic.name}' for ic in sig[:3]) or '—'
        loop = ' [LOOP]' if d.recursive else ''
        lines.append(f'{d.event}{loop}: {leader_name} +{abs(d.delta)} (carried by {carriers})')
    return f'{a_name} vs {b_name} — surgical intent divergence:\n  ' + '\n  '.join(lines)


def diff_intent(
    a_surface: dict[str, Any],
    b_surface: dict[str, Any],
    *,
    a_name: str = 'A',
    b_name: str = 'B',
    a_ranker: Ranker | None = None,
    b_ranker: Ranker | None = None,
) -> IntentDiff:
    """Diff two intent surfaces (A → B), event-anchored. Pure.

    Each surface is ``{engine, card_attributes}`` (as a
    :class:`~pipeline.transforms.comparison.cache.DeckDump` carries it): ``engine`` is the
    normalized engine surface (for the per-event producer/consumer attrs + relay hubs +
    recursive flags), ``card_attributes`` the oracle_id-keyed per-card derived attrs.
    ``a_ranker`` / ``b_ranker`` are the optional Phase-2 strength ranking aid for each
    deck's signature cards; omit them for participation-order discovery.
    """
    a_engine = a_surface.get('engine') or {}
    b_engine = b_surface.get('engine') or {}
    a_cards = a_surface.get('card_attributes') or []
    b_cards = b_surface.get('card_attributes') or []

    a_prof = event_investment(a_engine, a_cards)
    b_prof = event_investment(b_engine, b_cards)

    events = sorted(
        set(a_prof) | set(b_prof),
        key=lambda e: (-(a_prof.get(e, {}).get('total', 0) + b_prof.get(e, {}).get('total', 0)), e),
    )
    rows: list[EventDivergence] = []
    for ev in events:
        a_total = a_prof.get(ev, {}).get('total', 0)
        b_total = b_prof.get(ev, {}).get('total', 0)
        if a_total == 0 and b_total == 0:
            continue
        delta = b_total - a_total
        a_sig = _signature_cards(ev, a_engine, a_cards, ranker=a_ranker)
        b_sig = _signature_cards(ev, b_engine, b_cards, ranker=b_ranker)
        recursive = bool(a_prof.get(ev, {}).get('recursive') or b_prof.get(ev, {}).get('recursive'))
        leader = 'tie' if delta == 0 else ('B' if delta > 0 else 'A')
        rows.append(
            EventDivergence(
                event=ev,
                a_total=a_total,
                b_total=b_total,
                delta=delta,
                a_n_cards=a_prof.get(ev, {}).get('n_cards', 0),
                b_n_cards=b_prof.get(ev, {}).get('n_cards', 0),
                recursive=recursive,
                divergent=abs(delta) >= _DIVERGENCE_THRESHOLD,
                leader=leader,
                a_signature=a_sig[:_SIGNATURE_LIMIT],
                b_signature=b_sig[:_SIGNATURE_LIMIT],
                a_high_mv=_high_mv(a_sig),
                b_high_mv=_high_mv(b_sig),
            )
        )
    divergent = [r for r in rows if r.divergent]
    divergent.sort(key=lambda r: (-abs(r.delta), r.event))
    return IntentDiff(events=rows, divergent_events=divergent, reading=_reading(divergent, a_name, b_name))
