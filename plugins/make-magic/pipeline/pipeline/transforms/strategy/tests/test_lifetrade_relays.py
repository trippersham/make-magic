"""Lifetrade gate-edges relocated to catalog relays (ADR 0026 Phase 2).

The 6 lifetrade EventEffects (the directed draw/gain/drain pairs) moved from the
dissolved ``edge_roles.py`` sidecar into ``derive_card_events.SEED_MAP`` as
``RelayEntry(from_event, gate)`` edges on the PRODUCED event's ``produced_by``.
These tests pin:

* the 6 relays exist in SEED_MAP (produced_by-only) with the correct
  (from_event, owning-event, gate) and gate queries BYTE-IDENTICAL to the held
  ``SYNTH_ALWAYS_ATTRIBUTES``;
* the one unified SCC walk closes the recursive lifegain↔life-loss-drain loop on
  the DEFAULT path (hub = the enabler cards: Sanguine Bond's ``gain-to-drain`` +
  Exquisite Blood's ``drain-to-gain``), even when each gate is a sub-threshold
  attr (relay-gate liveness is PRESENCE, not identity admission);
* a one-sided deck (only one gate present) does NOT get a lifetrade recursion.
"""

from __future__ import annotations

from pipeline.transforms.strategy.derive_card_events import SEED_MAP, RelayEntry, relay_gate_attrs
from pipeline.transforms.strategy.distill_strategies import SYNTH_ALWAYS_ATTRIBUTES
from pipeline.transforms.strategy.strategy_graph import recursive_event_set

# The 6 lifetrade relays as (owning-event, from_event, gate).
EXPECTED_RELAYS = {
    ('lifegain', 'draw-a-card', 'draw-to-gain'),
    ('lifegain', 'life-loss-drain', 'drain-to-gain'),
    ('life-loss-drain', 'draw-a-card', 'draw-to-drain'),
    ('life-loss-drain', 'lifegain', 'gain-to-drain'),
    ('draw-a-card', 'life-loss-drain', 'drain-to-draw'),
    ('draw-a-card', 'lifegain', 'gain-to-draw'),
}

# The held byte-for-byte gate queries (from SYNTH_ALWAYS_ATTRIBUTES, pre-relocation).
EXPECTED_GATE_QUERIES = {
    'draw-to-drain': 'oracle:/draw[^.]*los/ AND otag:draw-matters AND (otag:opponent-loses-life OR otag:drain-life)',
    'draw-to-gain': 'oracle:/draw[^.]*gain/ AND otag:draw-matters AND otag:lifegain',
    'gain-to-drain': 'oracle:/gain[^.]*los/ AND otag:lifegain-matters AND (otag:opponent-loses-life OR otag:drain-life)',  # noqa: E501
    'drain-to-gain': 'oracle:/opponent[^.]*los[a-z]*[^.]*gain/ AND otag:life-loss-matters AND otag:lifegain',
    'gain-to-draw': 'oracle:/gain[^.]*life[^.]*draw/ AND otag:lifegain-matters',
    'drain-to-draw': 'oracle:/opponent[^.]*los[a-z]*[^.]*draw/ AND otag:life-loss-matters',
}


class TestLifetradeRelaysInSeedMap:
    def test_six_relays_present_produced_by_only(self) -> None:
        found = set()
        for owning, entry in SEED_MAP.items():
            # produced_by-only invariant: no relays on the triggers side.
            assert not entry['triggers'].get('relays'), f'{owning}.triggers must not carry relays'
            for relay in entry['produced_by'].get('relays', []):
                assert isinstance(relay, RelayEntry)
                found.add((owning, relay.from_event, relay.gate))
        assert found == EXPECTED_RELAYS

    def test_relay_gate_attrs_are_the_six(self) -> None:
        assert relay_gate_attrs() == frozenset(EXPECTED_GATE_QUERIES)

    def test_gate_queries_byte_identical_to_synth_always(self) -> None:
        """The relocated gates reference the SAME synth queries, byte-for-byte."""
        synth = dict(SYNTH_ALWAYS_ATTRIBUTES)
        for gate, query in EXPECTED_GATE_QUERIES.items():
            assert synth[gate] == query, f'gate {gate} query drifted from the held SYNTH_ALWAYS value'


class TestLifetradeLoopClosure:
    """The recursive lifegain↔life-loss-drain loop closes on PRESENCE of both gates."""

    def test_both_gates_present_closes_loop(self) -> None:
        # Sanguine Bond (gain-to-drain) + Exquisite Blood (drain-to-gain) present.
        present = {'gain-to-drain', 'drain-to-gain', 'lifegain', 'life-loss-matters'}
        rec = recursive_event_set([], SEED_MAP_EVENTS(), set(), present)
        assert {'lifegain', 'life-loss-drain'} <= rec

    def test_one_sided_gain_only_no_recursion(self) -> None:
        # Only Sanguine Bond (gain-to-drain); no drain-to-gain return edge.
        present = {'gain-to-drain', 'lifegain', 'life-loss-matters'}
        rec = recursive_event_set([], SEED_MAP_EVENTS(), set(), present)
        assert 'lifegain' not in rec and 'life-loss-drain' not in rec

    def test_one_sided_drain_only_no_recursion(self) -> None:
        # Only Exquisite Blood (drain-to-gain); no gain-to-drain forward edge.
        present = {'drain-to-gain', 'lifegain', 'life-loss-matters'}
        rec = recursive_event_set([], SEED_MAP_EVENTS(), set(), present)
        assert 'lifegain' not in rec and 'life-loss-drain' not in rec

    def test_gate_absent_relay_dead(self) -> None:
        # Neither gate present -> no relay edges -> no lifetrade recursion.
        present = {'lifegain', 'life-loss-matters'}
        rec = recursive_event_set([], SEED_MAP_EVENTS(), set(), present)
        assert not ({'lifegain', 'life-loss-drain'} & rec)


def SEED_MAP_EVENTS() -> dict:
    """A minimal events dict carrying only the lifetrade relays (from SEED_MAP).

    Built from SEED_MAP's lifegain / life-loss-drain / draw-a-card produced_by
    relays so the test is self-contained and does not need the lake-derived
    string closures (string legs are gated on an empty ``nodes`` set, isolating
    the relay edges under test).
    """
    events: dict = {}
    for name in ('lifegain', 'life-loss-drain', 'draw-a-card'):
        relays = SEED_MAP[name]['produced_by'].get('relays', [])
        events[name] = {'produced_by': list(relays), 'triggers': []}
    return events


class TestMinimalRelayGraph:
    """Unit-level: a RelayEntry is live under presence, not node admission."""

    def test_presence_closes_without_admission(self) -> None:
        events = {
            'E1': {'produced_by': [RelayEntry('E2', 'g2')], 'triggers': []},
            'E2': {'produced_by': [RelayEntry('E1', 'g1')], 'triggers': []},
        }
        # Gates present but NOT admitted nodes (nodes empty) -> loop still closes.
        rec = recursive_event_set([], events, set(), {'g1', 'g2'})
        assert rec == {'E1', 'E2'}

    def test_absent_gate_leaves_loop_open(self) -> None:
        events = {
            'E1': {'produced_by': [RelayEntry('E2', 'g2')], 'triggers': []},
            'E2': {'produced_by': [RelayEntry('E1', 'g1')], 'triggers': []},
        }
        rec = recursive_event_set([], events, set(), {'g1'})  # g2 absent
        assert rec == set()

    def test_defaults_to_nodes_when_carriers_none(self) -> None:
        # Backward-compat: relay_gate_carriers=None falls back to `nodes`.
        events = {
            'E1': {'produced_by': [RelayEntry('E2', 'g2')], 'triggers': []},
            'E2': {'produced_by': [RelayEntry('E1', 'g1')], 'triggers': []},
        }
        assert recursive_event_set([], events, {'g1', 'g2'}) == {'E1', 'E2'}
        assert recursive_event_set([], events, {'g1'}) == set()
