"""EventEffect type + the (now store-only) within-card edge-role registry.

The 6 curated lifetrade couplings that once lived here (the directed draw/gain/
drain pairs — ``draw-to-drain``, ``draw-to-gain``, ``gain-to-drain``,
``drain-to-gain``, ``gain-to-draw``, ``drain-to-draw``) were **relocated into
``derive_card_events.SEED_MAP`` as catalog** :class:`~pipeline.transforms.strategy.derive_card_events.RelayEntry`
**edges** (ADR 0026 Phase 2): a relay on the produced event's ``produced_by`` side
names the source event (``from_event``) and the gate CardAttribute whose carriers
enable the edge. The one unified SCC walk reads those relays natively, so the
former sidecar role for lifetrade is **gone** — :data:`CURATED_EDGE_ROLES` no
longer carries them (ADR 0011 superseded for the curated within-card edge).

What remains here
-----------------
This module stays the home of the :class:`EventEffect` TYPE and the curated-role
lookup, which the **provisional-edge channel** still uses: :mod:`.relay`'s
``_get_role_with_store`` checks :func:`get_role` (curated) first, then the runtime
:class:`~pipeline.transforms.strategy.machine_vocab.ProvisionalAttributeStore`
(ADR-0017 haiku / spellbook combo edges), constructing an :class:`EventEffect` on
the fly from a machine attribute's ``trigger_event``/``produced_event``. With
:data:`CURATED_EDGE_ROLES` empty the curated tier is a clean miss and the store
tier carries every within-card edge that is not a catalog relay — the two-tier
waterfall (ADR 0017) is intact for any future curated family.

Types reused
------------
- :class:`.machine_vocab.Provenance` — shared provenance dataclass.
- :class:`EventEffect` — a frozen dataclass defined here that composes the above.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.transforms.strategy.machine_vocab import Provenance

# ---------------------------------------------------------------------------
# EventEffect: the guarded directed edge (trigger_event → produced_event)
#
# Still constructed by the provisional-edge channel (relay.py) from a machine
# store attribute. Guard scaffolding fields are ADDITIVE and DEFAULTED.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventEffect:
    """A guarded directed edge in the event multigraph (design-3 §3.2).

    ``trigger_event → produced_event`` with provenance and guard scaffolding.
    Constructed by :mod:`.relay` from a :class:`~pipeline.transforms.strategy.machine_vocab.ProvisionalAttributeStore`
    attribute (haiku / spellbook); the curated lifetrade family that once
    populated :data:`CURATED_EDGE_ROLES` now lives as catalog relays in
    ``derive_card_events.SEED_MAP`` (ADR 0026).

    Parameters
    ----------
    trigger_event
        The event this edge fires on (e.g. ``'draw-a-card'``).
        Must be a slug in ``data/event_catalog.json``.
    produced_event
        The event this edge produces (e.g. ``'life-loss-drain'``).
        Must be a slug in ``data/event_catalog.json``.
    provenance
        Origin metadata (store-authored: ``source='haiku'``/``'spellbook'``).

    Guard scaffolding (Phase 2A — INERT defaults, evaluation in 2B)
    ---------------------------------------------------------------
    provider
        ``'card'`` | ``'CardAttribute'`` | ``'rule'``. Default ``'card'``.
    guard_characteristic_ref
        CardAttribute slug the guard references (nullable).
    guard_trigger_scope
        ``'self'`` | ``'opponent'`` | ``'any'``. Default ``'any'``.
    guard_identity
        ``'any'`` | ``'another'`` | ``'self'``. Default ``'any'``.
    guard_event_reachable_ref
        Another Event that must be in the deck's reached set (nullable).
    guard_unresolved
        Deferred/warn bucket for unbounded counting / sim-only. Default ``False``.
    magnitude
        Local contribution: ``'infinite'`` | ``'near-infinite'`` | ``'scaled'``
        | ``'finite'`` | ``None``. Default ``None``.
    ability_key
        Per trigger-ability identifier (nullable). Default ``None``.
    """

    trigger_event: str
    produced_event: str
    provenance: Provenance

    # --- Guard scaffolding (additive, defaulted, inert) --------------------
    provider: str = 'card'
    guard_characteristic_ref: str | None = None
    guard_trigger_scope: str = 'any'
    guard_identity: str = 'any'
    guard_event_reachable_ref: str | None = None
    guard_unresolved: bool = False
    magnitude: str | None = None
    ability_key: str | None = None


# ---------------------------------------------------------------------------
# The registry: attribute name -> EventEffect
#
# EMPTY as of ADR 0026 Phase 2: the 6 lifetrade couplings were relocated to
# ``derive_card_events.SEED_MAP`` as catalog RelayEntry edges. No curated
# within-card edge role remains; the provisional store (haiku/spellbook) carries
# every within-card edge that is not a catalog relay.
# ---------------------------------------------------------------------------

#: Curated edge-role registry (EMPTY — see module docstring). Retained as the
#: curated tier of the ADR-0017 two-tier waterfall; :func:`get_role` is a clean
#: miss until a future curated family is registered here.
CURATED_EDGE_ROLES: dict[str, EventEffect] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_role(attr_name: str) -> EventEffect | None:
    """Look up the curated edge role for an attribute name.

    Returns ``None`` for every attr now that :data:`CURATED_EDGE_ROLES` is empty
    (the lifetrade family moved to catalog relays — ADR 0026). :mod:`.relay` calls
    this as the curated tier before falling through to the provisional store.
    """
    return CURATED_EDGE_ROLES.get(attr_name)


def all_edge_attr_names() -> frozenset[str]:
    """The frozenset of all curated edge-category attribute names (now empty).

    The authoritative list of within-card edge-gating attrs is
    :func:`.derive_card_events.relay_gate_attrs` (the SEED_MAP relays).
    """
    return frozenset(CURATED_EDGE_ROLES.keys())
