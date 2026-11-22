"""Within-card edge relay-node derivation (Phase 2).

For each curated edge-category attribute in the deck's ``nodes`` set, this
module injects the attribute as a **relay node** in the event graph:

- **consumer** of its ``trigger_event`` (the event the card's trigger ability
  fires on)
- **producer** of its ``produced_event`` (the event the card's effect ability
  generates)

This reuses the EXISTING ``produced_by x triggers -> event_edges``
construction from :func:`.strategy_graph.compute_facts` — no new edge type.  Within-card
edges now let loops compose across cards through the event layer
``elect_engine_units`` already walks.

Separation invariant
--------------------
The relay injection is a **SEPARATE code path** from the default
``compute_facts`` / ``resolve()`` path.  The default path stays byte-identical
(the P0 oracle's engine SHA and resolve SHA are unchanged).  The augmented path
is invoked only by the forthcoming on-demand engine-identification flow.

Example: Queza + Lich's Mastery
-------------------------------
Queza matches two curated edge attrs:

- ``draw-to-drain``: trigger=``draw-a-card``, effect=``life-loss-drain``
- ``draw-to-gain``:  trigger=``draw-a-card``, effect=``lifegain``

Lich's Mastery matches:

- ``gain-to-draw``: trigger=``lifegain``, effect=``draw-a-card``

After relay injection the event graph contains:

- ``draw-a-card``:     producer=``gain-to-draw``,
                       consumers={``draw-to-drain``, ``draw-to-gain``}
- ``lifegain``:        producer=``draw-to-gain``,  consumer=``gain-to-draw``
- ``life-loss-drain``: producer=``draw-to-drain``   (payoff endpoint, no consumers)

The cycle ``gain-to-draw -> draw-to-gain -> gain-to-draw`` (through events
``draw-a-card`` and ``lifegain``) closes — this is the Queza engine.

Provisional-edge channel (ADR 0026 — the one unified walk)
---------------------------------------------------------
Under the unified event graph (issue #83), ``elect_engine_units`` detects loops
with ONE sorted-Tarjan SCC pass over the CardAttribute | Event graph. This module
is the **provisional-edge channel** that feeds that one walk: it augments the
catalog's ``event_edges`` with the within-card edge attrs sourced from the curated
registry (``edge_roles.py``) AND the runtime :class:`ProvisionalAttributeStore`
(ADR-0017 spellbook/haiku edges). Because the SCC pass consumes the
``event_edges`` argument, those injected within-card edges participate in cycle
detection automatically — ``engine_report``'s RESOLVED/PROVISIONAL label and
``combo_edges`` keep working, now with true-SCC recursion instead of the retired
``producers & consumers`` heuristic. Catalog ``RelayEntry`` event→event edges are
read straight from ``assets.events`` inside the walk (Phase 1 authors none).
"""

from __future__ import annotations

from collections import defaultdict

from pipeline.transforms.strategy.assets import StrategyAssets
from pipeline.transforms.strategy.derive_card_events import RelayEntry
from pipeline.transforms.strategy.edge_roles import EventEffect, get_role
from pipeline.transforms.strategy.machine_vocab import ProvisionalAttributeStore
from pipeline.transforms.strategy.strategy_graph import elect_engine_units


def _get_role_with_store(
    attr_name: str,
    store: ProvisionalAttributeStore | None,
    matched_store_attrs: frozenset[str] | None = None,
) -> EventEffect | None:
    """Look up an edge role from curated roles first, then the machine store.

    The curated registry (:func:`get_role`) is checked first.  If it returns
    ``None`` AND a non-empty *store* is provided, the store's attributes are
    checked for a matching name with ``trigger_event``/``produced_event`` set,
    and a synthetic :class:`EventEffect` is constructed on the fly.

    A store entry's role is injected only when the entry's query actually matched
    a card in THIS deck. ``matched_store_attrs`` names the store entries whose
    query matched (computed by :func:`.engine_report.identify_engines`, the caller
    that evaluates store queries against the deck). A store name can be in ``nodes``
    for a DIFFERENT reason — a native CardAttribute of the same slug put it there —
    so without this gate a zero-match store entry would hijack that native node's
    role and silently rewrite the named cycle. ``None`` (the structural callers that
    pass no match map) keeps the ungated two-tier waterfall.

    With an empty or ``None`` store this is identical to ``get_role(attr_name)``.
    """
    curated = get_role(attr_name)
    if curated is not None:
        return curated
    if store is None or store.is_empty:
        return None
    if matched_store_attrs is not None and attr_name not in matched_store_attrs:
        return None
    machine_attr = store.get(attr_name)
    if machine_attr is None:
        return None
    if not machine_attr.trigger_event or not machine_attr.produced_event:
        return None
    return EventEffect(
        trigger_event=machine_attr.trigger_event,
        produced_event=machine_attr.produced_event,
        provenance=machine_attr.provenance,
    )


def build_relay_augmented_event_edges(
    nodes: set[str],
    events: dict[str, dict],
    *,
    store: ProvisionalAttributeStore | None = None,
    matched_store_attrs: frozenset[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Build event-edges with relay-node injection from within-card edge attrs.

    Identical to the ``compute_facts`` event-edge construction, EXCEPT: for each
    curated edge-category attribute in *nodes*, the attribute is injected as:

    - a **consumer** of its ``trigger_event`` (appended to ``triggers``)
    - a **producer** of its ``produced_event`` (appended to ``produced_by``)

    This makes within-card edges visible to the event graph so loops can compose
    across cards (Queza consumes draw, produces drain+gain; a gain-to-draw card
    consumes gain, produces draw -> cycle closes).

    Parameters
    ----------
    nodes
        The active attribute nodes from ``compute_facts`` (attrs with >= 3
        support cards or commander attrs).
    events
        ``assets.events`` — the event catalog dict (keys = event slugs,
        values have ``produced_by`` and ``triggers`` lists).
    matched_store_attrs
        The store entries whose query matched a card in this deck; a store role is
        injected only for a matched entry (see :func:`_get_role_with_store`). ``None``
        keeps the ungated waterfall.

    Returns
    -------
    list[tuple[str, str, str]]
        Event edges as ``(producer_attr, consumer_attr, event_name)`` triples,
        same shape as the default ``event_edges`` in ``compute_facts``.
    """
    # Copy the catalog's producer/consumer lists so we never mutate the originals.
    # The catalog lists may carry RelayEntry objects (ADR 0026 catalog relays); keep
    # only the string refs here — the relays are injected as attr legs just below, and
    # ``refs_to_nodes`` would drop the RelayEntry objects anyway.
    augmented_produced: dict[str, list[str]] = {}
    augmented_triggered: dict[str, list[str]] = {}
    for evname, ev in events.items():
        augmented_produced[evname] = [r for r in ev['produced_by'] if isinstance(r, str)]
        augmented_triggered[evname] = [r for r in ev['triggers'] if isinstance(r, str)]

    def _inject(attr: str, tev: str, eev: str) -> None:
        """Inject *attr* as a consumer of *tev* and a producer of *eev* (relay node)."""
        if tev not in augmented_triggered or eev not in augmented_produced:
            return
        if attr not in augmented_triggered[tev]:
            augmented_triggered[tev].append(attr)
        if attr not in augmented_produced[eev]:
            augmented_produced[eev].append(attr)

    # (1) Catalog relay edges (ADR 0026): a RelayEntry on ``<owning>.produced_by`` names
    # ``(from_event, gate)`` — the gate CardAttribute consumes ``from_event`` and produces
    # the owning event. This is the within-card edge that was the curated ``edge_roles``
    # sidecar before Phase 2; it is now authored in the catalog, so the relay-augmented
    # engine_report reads it here (keeping the named cycle + relay hubs) while the catalog
    # also feeds the one SCC walk's event→event relay edges directly. A relay is injected
    # when its gate attr is an active node.
    for evname, ev in events.items():
        for ref in ev.get('produced_by', ()):
            if isinstance(ref, RelayEntry) and ref.gate in nodes:
                _inject(ref.gate, ref.from_event, evname)

    # (2) Provisional store / curated edge roles from attrs present in nodes.
    # Sorted iteration for determinism (the injection order does not affect the
    # cross-product, but the produced_by/triggers append order is stable).
    for attr in sorted(nodes):
        role = _get_role_with_store(attr, store, matched_store_attrs)
        if role is None:
            continue
        _inject(attr, role.trigger_event, role.produced_event)

    # Build event_edges from augmented sets — same cross-product as compute_facts.
    def refs_to_nodes(refs: list[str]) -> list[str]:
        return [r for r in refs if r in nodes]

    event_edges: list[tuple[str, str, str]] = []
    for evname in events:
        for p in refs_to_nodes(augmented_produced[evname]):
            for c_ in refs_to_nodes(augmented_triggered[evname]):
                if p != c_:
                    event_edges.append((p, c_, evname))

    return event_edges


def elect_engine_units_with_relay(
    nodes: set[str],
    events: dict[str, dict],
    attr_weight: defaultdict[str, float],
    assets: StrategyAssets,
    cards: list[dict],
    *,
    store: ProvisionalAttributeStore | None = None,
    earned_archetypes: set[str] | None = None,
    combo_event_edges: list[tuple[str, str]] | None = None,
    matched_store_attrs: frozenset[str] | None = None,
) -> dict:
    """Run ``elect_engine_units`` over the relay-augmented event graph.

    This is the on-demand engine-identification entry point.  It builds event
    edges **with** relay-node injection (within-card edges visible) and feeds
    them to the SAME ``elect_engine_units`` function the default path uses.

    The default ``compute_facts`` path is NOT affected — it builds event edges
    from the catalog alone, without relay injection.

    Superset-of-resolve contract (#83 MAJOR-4)
    ------------------------------------------
    The on-demand report must report at least what ``resolve()`` reports: its
    election runs over the relay-augmented store edges under the SAME
    archetype-substantiveness gate ``resolve()`` applies. The gate is threaded
    straight through to the inner ``elect_engine_units`` exactly as
    ``strategy_graph.compute_facts`` passes it (``augmented_edges`` plays the role
    of ``event_edges``, a superset of resolve's catalog cross-product). The caller
    (:func:`.engine_report.identify_engines`) computes it the same way ``resolve()``
    does and passes ``combo_event_edges=None`` in both views (the combo-certified
    families are deferred — see ``combo_event_edges`` below), so the report's
    recursive set is exactly a superset of resolve's.

    Parameters
    ----------
    nodes, attr_weight, assets, cards
        Same as passed to ``elect_engine_units`` by ``compute_facts``.
    events
        ``assets.events`` — the event catalog dict.
    store
        Optional :class:`ProvisionalAttributeStore` for machine-authored edge roles.
        When non-empty, machine-authored attributes' trigger/effect roles
        are injected into the relay graph alongside curated roles.
    earned_archetypes
        The deck's archetypes that clear their ``Requirement.satisfied`` bar, used
        for the ARCHETYPE-substantiveness election gate (ADR 0021 B6). ``None`` (the
        default) disables that grain — the backward-compatible behavior for callers
        that only want the structure-only relay walk.
    combo_event_edges
        Optional per-deck event→event injection seam for the 7 combo-certified
        families (ADR 0026 §4), which are DEFERRED to #90. The shipped engine passes
        ``None`` (the default), adding no combo edges; the seam is retained — and
        exercised by ``test_relay`` — so #90 re-adds a vetted family layer without
        re-plumbing.
    matched_store_attrs
        The store entries whose query actually matched a card in this deck (computed
        by :func:`.engine_report.identify_engines`). A store entry's role is injected
        only when its name is in this set, so a zero-match store query never rewrites
        the named cycle of a same-slug native node. ``None`` (the default) keeps the
        ungated waterfall for structural callers that do not evaluate store queries.

    Returns
    -------
    dict
        The engine-isolation dict, same shape as ``elect_engine_units`` output.
    """
    augmented_edges = build_relay_augmented_event_edges(
        nodes, events, store=store, matched_store_attrs=matched_store_attrs
    )
    return elect_engine_units(nodes, augmented_edges, attr_weight, assets, cards, earned_archetypes, combo_event_edges)
