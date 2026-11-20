"""Tests for Phase 2A task 1: EdgeRole→EventEffect rename + additive guard scaffolding.

TDD red-phase: these tests import the RENAMED class/fields and assert the NEW
defaulted fields.  They MUST fail before the rename (ImportError / AttributeError)
and pass after.

Parity gate: the rename + additive fields must NOT alter any serialized/hashed
representation.  A separate parity-oracle run (not duplicated here) gates that.
"""

from __future__ import annotations

import pytest

from pipeline.transforms.strategy.machine_vocab import Provenance

# ────────────────────────────────────────────────────────────────────────────
# 1. EventEffect import + curated round-trip (renamed class + field)
# ────────────────────────────────────────────────────────────────────────────


class TestEventEffectRename:
    """The renamed EventEffect class exists and curated entries round-trip."""

    def test_eventeffect_importable(self) -> None:
        """EventEffect is importable from edge_roles."""
        from pipeline.transforms.strategy.edge_roles import EventEffect

        assert EventEffect is not None

    def test_curated_registry_empty_after_relocation(self) -> None:
        """CURATED_EDGE_ROLES is empty: lifetrade moved to SEED_MAP relays (ADR 0026 Ph2)."""
        from pipeline.transforms.strategy.edge_roles import CURATED_EDGE_ROLES

        assert CURATED_EDGE_ROLES == {}

    def test_produced_event_field_exists(self) -> None:
        """EventEffect has the renamed field 'produced_event' (was 'effect_event')."""
        from pipeline.transforms.strategy.edge_roles import EventEffect

        ee = EventEffect(
            trigger_event='draw-a-card',
            produced_event='life-loss-drain',
            provenance=Provenance(source='curated'),
        )
        assert ee.produced_event == 'life-loss-drain'

    @pytest.mark.parametrize(
        'owning_event,from_event,gate',
        [
            ('life-loss-drain', 'draw-a-card', 'draw-to-drain'),
            ('lifegain', 'draw-a-card', 'draw-to-gain'),
            ('life-loss-drain', 'lifegain', 'gain-to-drain'),
            ('lifegain', 'life-loss-drain', 'drain-to-gain'),
            ('draw-a-card', 'lifegain', 'gain-to-draw'),
            ('draw-a-card', 'life-loss-drain', 'drain-to-draw'),
        ],
        ids=['draw-to-drain', 'draw-to-gain', 'gain-to-drain', 'drain-to-gain', 'gain-to-draw', 'drain-to-draw'],
    )
    def test_lifetrade_relocated_to_seed_map_relay(
        self,
        owning_event: str,
        from_event: str,
        gate: str,
    ) -> None:
        """Each former curated role is now a SEED_MAP RelayEntry on the produced event."""
        from pipeline.transforms.strategy.derive_card_events import SEED_MAP, RelayEntry

        relays = SEED_MAP[owning_event]['produced_by'].get('relays', [])
        assert RelayEntry(from_event=from_event, gate=gate) in relays

    def test_get_role_returns_none_after_relocation(self) -> None:
        """get_role no longer returns a curated lifetrade role (relocated to SEED_MAP)."""
        from pipeline.transforms.strategy.edge_roles import get_role

        assert get_role('draw-to-drain') is None


# ────────────────────────────────────────────────────────────────────────────
# 2. Additive defaulted fields on EventEffect (scaffolding — inert)
# ────────────────────────────────────────────────────────────────────────────


class TestEventEffectDefaults:
    """New fields have inert defaults when only old fields are set."""

    def test_old_fields_only_gives_inert_defaults(self) -> None:
        """Constructing with only trigger/produced/provenance gives all defaults."""
        from pipeline.transforms.strategy.edge_roles import EventEffect

        ee = EventEffect(
            trigger_event='draw-a-card',
            produced_event='life-loss-drain',
            provenance=Provenance(source='curated'),
        )
        # Guard scaffolding — all at inert defaults
        assert ee.provider == 'card'
        assert ee.guard_characteristic_ref is None
        assert ee.guard_trigger_scope == 'any'
        assert ee.guard_identity == 'any'
        assert ee.guard_event_reachable_ref is None
        assert ee.guard_unresolved is False
        assert ee.magnitude is None
        assert ee.ability_key is None

    def test_eventeffect_is_frozen(self) -> None:
        """EventEffect remains a frozen dataclass."""
        from pipeline.transforms.strategy.edge_roles import EventEffect

        ee = EventEffect(
            trigger_event='draw-a-card',
            produced_event='life-loss-drain',
            provenance=Provenance(source='curated'),
        )
        with pytest.raises(AttributeError):
            ee.provider = 'rule'  # type: ignore[misc]


# ────────────────────────────────────────────────────────────────────────────
# 3. Provenance.lifecycle field
# ────────────────────────────────────────────────────────────────────────────


class TestProvenanceLifecycle:
    """Provenance carries a 'lifecycle' field (provisional|verified)."""

    def test_lifecycle_field_exists(self) -> None:
        """Provenance has a 'lifecycle' attribute."""
        prov = Provenance(source='curated')
        assert hasattr(prov, 'lifecycle')

    def test_curated_default_is_verified(self) -> None:
        """Default lifecycle is 'verified' (curated provenance)."""
        prov = Provenance(source='curated')
        assert prov.lifecycle == 'verified'

    def test_haiku_default_lifecycle_is_provisional(self) -> None:
        """CardAttribute's default provenance carries lifecycle='provisional'."""
        from pipeline.transforms.strategy.machine_vocab import CardAttribute

        ma = CardAttribute(name='test', query='otag:test')
        assert ma.provenance.lifecycle == 'provisional'

    def test_explicit_lifecycle_overrides_default(self) -> None:
        """Lifecycle can be set explicitly."""
        prov = Provenance(source='haiku', lifecycle='verified')
        assert prov.lifecycle == 'verified'


# ────────────────────────────────────────────────────────────────────────────
# 4. ProvisionalAttributeStore inertness (empty store behavior unchanged)
# ────────────────────────────────────────────────────────────────────────────


class TestProvisionalAttributeStoreInertness:
    """An empty ProvisionalAttributeStore is still inert after the scaffolding additions."""

    def test_empty_store_is_empty(self) -> None:
        from pipeline.transforms.strategy.machine_vocab import empty_store

        store = empty_store()
        assert store.is_empty is True

    def test_empty_store_names_empty(self) -> None:
        from pipeline.transforms.strategy.machine_vocab import empty_store

        assert empty_store().names() == frozenset()

    def test_empty_store_edge_queries_empty(self) -> None:
        from pipeline.transforms.strategy.machine_vocab import empty_store

        assert empty_store().edge_queries() == []

    def test_empty_store_get_returns_none(self) -> None:
        from pipeline.transforms.strategy.machine_vocab import empty_store

        assert empty_store().get('anything') is None


# ────────────────────────────────────────────────────────────────────────────
# 5. CardAttribute uses produced_event field name
# ────────────────────────────────────────────────────────────────────────────


class TestCardAttributeProducedEvent:
    """CardAttribute uses the renamed 'produced_event' field."""

    def test_produced_event_field(self) -> None:
        from pipeline.transforms.strategy.machine_vocab import CardAttribute

        ma = CardAttribute(
            name='test-edge',
            query='otag:test',
            trigger_event='draw-a-card',
            produced_event='life-loss-drain',
        )
        assert ma.produced_event == 'life-loss-drain'
        assert ma.trigger_event == 'draw-a-card'
