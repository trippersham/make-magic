"""Layer ② — engine diff: how two decks GENERATE and CLOSE, regime-switched.

Identity (layer ①) says *what a deck is*; this layer says *how its engine runs*.
The spike (``research/spike/compare.py`` + ``FINDINGS.md`` §10) proved the engine
primitive is **regime-dependent**, so one diff for all decks is wrong:

* **loop / convergent regime** — a *grind engine* (World Reclaimer: sacrifice a land →
  recur it → re-trigger landfall). Its signal is the ``convergent_event_components``
  multi-event strongly-connected cycle (which events/attrs close, how heavy) plus the
  ``commander_loop.closes`` probe and the ``relay_hubs``. A grind engine reads here as
  **"a convergent component + a closing commander loop"**, NOT as a ``recursive:true``
  certification — that certified-infinite flag is #90's deferred layer
  (``wr-framework-admission.md``). This module reports the honest structural surface and
  never waits for (or synthesises) that flag.
* **mass / bipartite regime** — a *goodstuff pile* (Wakanda / Nick Fury vs their precons:
  every unit ``bipartite`` / non-recursive). "Engine" degrades to a payoff-mass
  distribution, so the signal is the ``ranked_units`` compared by event / mass / share /
  ``commander_coupled``.

A deck's regime is read off the engine surface — a multi-event convergent cycle ⇒ the
loop regime (the grind engine), otherwise the mass regime. Both readings are always
computed into the structured :class:`EngineDiff`; the short human :attr:`EngineDiff.reading`
is what branches on the two decks' regimes.

Pure functions over two **engine surfaces** (see :func:`diff_engine`). An engine surface
is the normalized ``facts['engines']`` dict the resolve bridge materializes onto a
:class:`~pipeline.transforms.comparison.cache.DeckDump` (``convergent_event_components`` /
``ranked_units`` / ``relay_hubs`` / ``commander_loop`` / ``forward_chains`` / ``events``);
nothing here calls the engine or the lake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Minimum distinct events for a convergent component to count as a true multi-event
#: cycle (the loop-regime signal). A single-event component is a lone payoff, not a grind.
_MIN_CYCLE_EVENTS = 2

#: Rounding for reported masses/weights (matches the engine surface's own precision).
_MASS_DP = 1

#: Regime labels.
REGIME_LOOP = 'loop'
REGIME_MASS = 'mass'


def _components(engine: dict[str, Any]) -> list[dict[str, Any]]:
    return engine.get('convergent_event_components') or []


def _ranked_units(engine: dict[str, Any]) -> list[dict[str, Any]]:
    return engine.get('ranked_units') or []


def _commander_loop(engine: dict[str, Any]) -> dict[str, Any]:
    return engine.get('commander_loop') or {}


def heaviest_cycle(engine: dict[str, Any]) -> dict[str, Any] | None:
    """The heaviest *multi-event* convergent component (the grind cycle), or None.

    ``convergent_event_components`` is already sorted heaviest/most-eventful first by
    the engine; this returns the first one with ≥ :data:`_MIN_CYCLE_EVENTS` distinct
    events — the strongly-connected engine cycle — or None when the deck has no
    multi-event cycle (a goodstuff pile).
    """
    for comp in _components(engine):
        if int(comp.get('n_events', 0)) >= _MIN_CYCLE_EVENTS:
            return comp
    return None


def classify_regime(engine: dict[str, Any]) -> str:
    """The engine regime of one deck: :data:`REGIME_LOOP` or :data:`REGIME_MASS`.

    A deck is in the **loop** regime when it has a true multi-event convergent cycle
    (a grind engine that closes across events, e.g. World Reclaimer's land-sac loop);
    otherwise the **mass** regime (a bipartite payoff distribution — goodstuff). The
    ``commander_loop.closes`` probe is a *supporting* signal (a goodstuff commander can
    still soft-close a loop), so the multi-event cycle — not the commander loop — is the
    regime discriminator, exactly as ``wr-framework-admission.md`` prescribes.
    """
    return REGIME_LOOP if heaviest_cycle(engine) is not None else REGIME_MASS


def is_certified_infinite(engine: dict[str, Any]) -> bool:
    """Whether ANY ranked unit carries the ``recursive:true`` certification (#90).

    The framework represents a grind loop as *structure* (convergent component +
    closing commander loop) WITHOUT claiming it is unbounded. ``recursive:true`` is the
    certified-infinite flag #90 would set; on the current base every grind unit is
    ``recursive:false`` (a value grind that closes). This reads the flag honestly and
    never fabricates it.
    """
    return any(bool(u.get('recursive')) for u in _ranked_units(engine))


@dataclass(frozen=True)
class UnitDelta:
    """Per-event ranked-unit delta (A → B) — the mass-regime signal.

    Attributes:
        event: the event the unit elects on.
        a_mass / b_mass: node-weighted event-neighborhood mass (None when the side
            has no unit on this event).
        delta_mass: ``b_mass - a_mass`` (None when either side lacks the unit).
        a_share / b_share: the unit's share of total elected mass.
        a_formation_mode / b_formation_mode: ``bipartite`` / ``terminal-producer`` /
            ``exogenous-environmental`` — the anchor mode (the regime switch per unit).
        a_commander_coupled / b_commander_coupled: whether the unit touches a commander attr.
        a_recursive / b_recursive: the per-unit ``recursive`` flag (grind certification; #90).
        tag: ``shared`` / ``only_A`` / ``only_B`` / ``shifted`` (shared but the
            formation mode or recursive flag moved).
    """

    event: str
    a_mass: float | None
    b_mass: float | None
    delta_mass: float | None
    a_share: float | None
    b_share: float | None
    a_formation_mode: str | None
    b_formation_mode: str | None
    a_commander_coupled: bool | None
    b_commander_coupled: bool | None
    a_recursive: bool | None
    b_recursive: bool | None
    tag: str


@dataclass(frozen=True)
class EngineDiff:
    """The engine (generation/closure) delta between two decks (A → B).

    The structured diff always carries BOTH the loop-regime surface (convergent cycle,
    commander loop, relay hubs) and the mass-regime surface (per-event ranked-unit
    deltas); :attr:`reading` is the short human summary, branched on the two regimes.

    Attributes:
        regime_a / regime_b: :data:`REGIME_LOOP` or :data:`REGIME_MASS` per deck.
        a_cycle_events / b_cycle_events: events in each deck's heaviest convergent cycle.
        a_cycle_weight / b_cycle_weight: that cycle's combined attr weight (None if none).
        shared_cycle_events / added_cycle_events / dropped_cycle_events: set algebra over
            the two cycles' events (added = in B's cycle, not A's; dropped = the reverse).
        a_loop_closes / b_loop_closes: the ``commander_loop.closes`` probe per deck.
        a_reaches_back / b_reaches_back: the commander loop's ``reaches_back_to`` attrs.
        a_relay_hubs / b_relay_hubs: the weight-ordered relay hubs per deck.
        shared_relay_hubs: relay hubs common to both.
        a_certified_infinite / b_certified_infinite: the honest ``recursive:true`` read (#90).
        unit_deltas: per-event ranked-unit deltas, heaviest combined mass first.
        reading: the short, regime-switched human summary.
    """

    regime_a: str
    regime_b: str
    a_cycle_events: list[str]
    b_cycle_events: list[str]
    a_cycle_weight: float | None
    b_cycle_weight: float | None
    shared_cycle_events: list[str]
    added_cycle_events: list[str]
    dropped_cycle_events: list[str]
    a_loop_closes: bool
    b_loop_closes: bool
    a_reaches_back: list[str]
    b_reaches_back: list[str]
    a_relay_hubs: list[str]
    b_relay_hubs: list[str]
    shared_relay_hubs: list[str]
    a_certified_infinite: bool
    b_certified_infinite: bool
    unit_deltas: list[UnitDelta]
    reading: str


def _units_by_event(engine: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {u['event']: u for u in _ranked_units(engine)}


def _round(x: Any) -> float | None:
    return None if x is None else round(float(x), _MASS_DP)


def unit_deltas(a_engine: dict[str, Any], b_engine: dict[str, Any]) -> list[UnitDelta]:
    """Per-event ranked-unit deltas (A → B), heaviest combined mass first.

    Tags a shared event ``shifted`` when its formation mode or recursive flag moved
    between the decks (a structural change even at equal mass), else ``shared``;
    events only one side elects are ``only_A`` / ``only_B``.
    """
    ua, ub = _units_by_event(a_engine), _units_by_event(b_engine)
    events = set(ua) | set(ub)
    rows: list[UnitDelta] = []
    for ev in events:
        x, y = ua.get(ev), ub.get(ev)
        a_mass = _round(x['mass']) if x else None
        b_mass = _round(y['mass']) if y else None
        delta = None if a_mass is None or b_mass is None else round(b_mass - a_mass, _MASS_DP)
        if x and y:
            shifted = x.get('formation_mode') != y.get('formation_mode') or bool(x.get('recursive')) != bool(
                y.get('recursive')
            )
            tag = 'shifted' if shifted else 'shared'
        else:
            tag = 'only_A' if x else 'only_B'
        rows.append(
            UnitDelta(
                event=ev,
                a_mass=a_mass,
                b_mass=b_mass,
                delta_mass=delta,
                a_share=(x or {}).get('share') if x else None,
                b_share=(y or {}).get('share') if y else None,
                a_formation_mode=(x or {}).get('formation_mode') if x else None,
                b_formation_mode=(y or {}).get('formation_mode') if y else None,
                a_commander_coupled=bool(x.get('commander_coupled')) if x else None,
                b_commander_coupled=bool(y.get('commander_coupled')) if y else None,
                a_recursive=bool(x.get('recursive')) if x else None,
                b_recursive=bool(y.get('recursive')) if y else None,
                tag=tag,
            )
        )
    rows.sort(key=lambda r: (-((r.a_mass or 0.0) + (r.b_mass or 0.0)), r.event))
    return rows


def _cycle_events(engine: dict[str, Any]) -> list[str]:
    cyc = heaviest_cycle(engine)
    return list(cyc.get('events', [])) if cyc else []


def _cycle_weight(engine: dict[str, Any]) -> float | None:
    cyc = heaviest_cycle(engine)
    return _round(cyc.get('weight')) if cyc else None


def _loop_reading(name: str, engine: dict[str, Any]) -> str:
    """One deck's loop-regime reading: the convergent cycle + closing commander loop."""
    cyc = heaviest_cycle(engine)
    cl = _commander_loop(engine)
    evs = ', '.join(cyc.get('events', [])) if cyc else '—'
    weight = cyc.get('weight') if cyc else None
    hubs = ', '.join(engine.get('relay_hubs') or []) or 'none'
    closes = cl.get('closes')
    back = ', '.join(cl.get('reaches_back_to') or []) or 'none'
    certified = (
        ' (certified-infinite)'
        if is_certified_infinite(engine)
        else ' — a value grind that closes, not a certified infinite'
    )
    loop = f'commander loop closes → {back}' if closes else 'commander loop open'
    return f'{name}: convergent cycle over {{{evs}}} (weight {weight}); {loop}; relay hubs: {hubs}{certified}'


def _mass_reading(name: str, engine: dict[str, Any]) -> str:
    """One deck's mass-regime reading: the top ranked payoff units by mass."""
    units = sorted(_ranked_units(engine), key=lambda u: -float(u.get('mass', 0.0)))[:3]
    if not units:
        return f'{name}: no elected engine units (flat/loopless pile)'
    top = ', '.join(f'{u["event"]} (mass {round(float(u["mass"]), _MASS_DP)})' for u in units)
    return f'{name}: bipartite payoff mass led by {top}'


def _reading(
    name_a: str, a_engine: dict[str, Any], name_b: str, b_engine: dict[str, Any], regime_a: str, regime_b: str
) -> str:
    """The short, regime-switched human reading for the pair."""
    read_a = _loop_reading(name_a, a_engine) if regime_a == REGIME_LOOP else _mass_reading(name_a, a_engine)
    read_b = _loop_reading(name_b, b_engine) if regime_b == REGIME_LOOP else _mass_reading(name_b, b_engine)
    if regime_a == regime_b:
        headline = f'both decks are in the {regime_a} regime'
    else:
        headline = f'regime shift: A={regime_a}, B={regime_b} (one closes an engine, the other is a payoff pile)'
    return f'{headline}\n  {read_a}\n  {read_b}'


def diff_engine(
    a_engine: dict[str, Any],
    b_engine: dict[str, Any],
    *,
    a_name: str = 'A',
    b_name: str = 'B',
) -> EngineDiff:
    """Diff two engine surfaces (A → B), regime-switched. Pure.

    Each surface is the normalized ``facts['engines']`` dict a
    :class:`~pipeline.transforms.comparison.cache.DeckDump` carries. The structured
    result holds both regimes' surfaces; :attr:`EngineDiff.reading` branches on them.
    """
    regime_a, regime_b = classify_regime(a_engine), classify_regime(b_engine)
    a_evs, b_evs = _cycle_events(a_engine), _cycle_events(b_engine)
    a_set, b_set = set(a_evs), set(b_evs)
    a_cl, b_cl = _commander_loop(a_engine), _commander_loop(b_engine)
    a_hubs = list(a_engine.get('relay_hubs') or [])
    b_hubs = list(b_engine.get('relay_hubs') or [])
    return EngineDiff(
        regime_a=regime_a,
        regime_b=regime_b,
        a_cycle_events=a_evs,
        b_cycle_events=b_evs,
        a_cycle_weight=_cycle_weight(a_engine),
        b_cycle_weight=_cycle_weight(b_engine),
        shared_cycle_events=sorted(a_set & b_set),
        added_cycle_events=sorted(b_set - a_set),
        dropped_cycle_events=sorted(a_set - b_set),
        a_loop_closes=bool(a_cl.get('closes')),
        b_loop_closes=bool(b_cl.get('closes')),
        a_reaches_back=list(a_cl.get('reaches_back_to') or []),
        b_reaches_back=list(b_cl.get('reaches_back_to') or []),
        a_relay_hubs=a_hubs,
        b_relay_hubs=b_hubs,
        shared_relay_hubs=[h for h in a_hubs if h in set(b_hubs)],
        a_certified_infinite=is_certified_infinite(a_engine),
        b_certified_infinite=is_certified_infinite(b_engine),
        unit_deltas=unit_deltas(a_engine, b_engine),
        reading=_reading(a_name, a_engine, b_name, b_engine, regime_a, regime_b),
    )
