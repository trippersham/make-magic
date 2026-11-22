"""The curated EventEffect sidecar is dead for lifetrade (ADR 0026 Phase 2).

The 6 lifetrade couplings that once populated ``CURATED_EDGE_ROLES`` were
relocated into ``derive_card_events.SEED_MAP`` as catalog ``RelayEntry`` edges.
These tests pin that the sidecar registry is now EMPTY (no lingering lifetrade
roles) while the :class:`EventEffect` TYPE + the curated-tier lookup survive for
the provisional (haiku/spellbook) store channel.
"""

from __future__ import annotations

from pipeline.transforms.strategy.machine_vocab import Provenance

# The 6 former lifetrade edge attr names — must NO LONGER be curated roles.
FORMER_LIFETRADE_ROLES = frozenset(
    {'draw-to-drain', 'draw-to-gain', 'gain-to-drain', 'drain-to-gain', 'gain-to-draw', 'drain-to-draw'}
)


class TestSidecarLifetradeRemoved:
    """The dead lifetrade sidecar path is gone."""

    def test_registry_is_empty(self) -> None:
        from pipeline.transforms.strategy.edge_roles import CURATED_EDGE_ROLES

        assert CURATED_EDGE_ROLES == {}

    def test_no_lifetrade_role_remains(self) -> None:
        from pipeline.transforms.strategy.edge_roles import get_role

        for name in sorted(FORMER_LIFETRADE_ROLES):
            assert get_role(name) is None, f'{name} should no longer be a curated role'

    def test_all_edge_attr_names_empty(self) -> None:
        from pipeline.transforms.strategy.edge_roles import all_edge_attr_names

        assert all_edge_attr_names() == frozenset()


class TestEventEffectTypeSurvives:
    """The EventEffect TYPE stays for the provisional-store channel (relay.py)."""

    def test_eventeffect_constructs_with_provenance(self) -> None:
        from pipeline.transforms.strategy.edge_roles import EventEffect

        ee = EventEffect(
            trigger_event='draw-a-card',
            produced_event='lifegain',
            provenance=Provenance(source='haiku', lifecycle='provisional'),
        )
        assert ee.trigger_event == 'draw-a-card'
        assert ee.produced_event == 'lifegain'
        assert ee.provenance.source == 'haiku'
        # Guard scaffolding defaults stay inert.
        assert ee.provider == 'card'
        assert ee.guard_characteristic_ref is None
        assert ee.magnitude is None
