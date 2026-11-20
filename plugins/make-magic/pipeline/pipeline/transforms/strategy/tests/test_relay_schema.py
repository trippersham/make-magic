"""ADR 0026 schema: the ``{from_event, gate}`` relay entry (structure only).

Phase 0 of the unified event graph (issue #83) lays the relay-entry SCHEMA
through the derivation + validators WITHOUT adding any relay families — the real
catalog gains none, so the engine output stays byte-identical (the P0 SHA triple
is unchanged). These tests exercise the schema with a SYNTHETIC relay entry:

* a relay entry is a :class:`RelayEntry` (``from_event``, ``gate``) that may sit
  on a ``produced_by``/``triggers`` side ALONGSIDE the attribute-string entries;
* ``derive_card_events`` carries it through the descendant-closure derivation
  un-clobbered (derive OVERWRITES the side from the SEED_MAP closure at load, so
  the relay must survive that overwrite);
* ``validate_seed_map`` accepts a well-formed relay and rejects a malformed one,
  WITHOUT weakening the mechanic-exclusivity checks.
"""

from __future__ import annotations

from typing import Any

from pipeline.transforms.strategy.derive_card_events import (
    RelayEntry,
    derive_card_events,
    validate_seed_map,
)

# ────────────────────────────────────────────────────────────────────────────
# A minimal synthetic event catalog + SEED_MAP for the schema (no real data).
# Two real event slugs are reused so ``from_event`` references a known event.
# ────────────────────────────────────────────────────────────────────────────


def _synthetic_events() -> dict[str, dict[str, Any]]:
    """A 2-event catalog with the committed-event field shape (rule/pb/triggers)."""
    return {
        'lifegain': {'rule': 'r', 'produced_by': [], 'triggers': []},
        'draw-a-card': {'rule': 'r', 'produced_by': [], 'triggers': []},
    }


def _synthetic_seed_map(relays: list[RelayEntry]) -> dict[str, dict[str, Any]]:
    """A SEED_MAP whose ``lifegain.produced_by`` carries *relays* beside a seed."""
    return {
        'lifegain': {
            'produced_by': {'seeds': [], 'grade': 'empty', 'relays': relays},
            'triggers': {'seeds': [], 'grade': 'empty'},
        },
        'draw-a-card': {
            'produced_by': {'seeds': [], 'grade': 'empty'},
            'triggers': {'seeds': [], 'grade': 'empty'},
        },
    }


# ────────────────────────────────────────────────────────────────────────────
# 1. RelayEntry shape
# ────────────────────────────────────────────────────────────────────────────


class TestRelayEntryShape:
    def test_fields(self) -> None:
        r = RelayEntry(from_event='draw-a-card', gate='draw-to-drain')
        assert r.from_event == 'draw-a-card'
        assert r.gate == 'draw-to-drain'

    def test_is_hashable_and_sortable(self) -> None:
        """A relay entry is hashable (safe in the ``r in nodes`` consumer path)
        and sorts deterministically by ``(from_event, gate)``."""
        a = RelayEntry(from_event='lifegain', gate='gain-to-draw')
        b = RelayEntry(from_event='draw-a-card', gate='draw-to-gain')
        assert {a, b} == {b, a}
        assert sorted([a, b]) == [b, a]  # 'draw-a-card' < 'lifegain'


# ────────────────────────────────────────────────────────────────────────────
# 2. Derivation carries the relay through un-clobbered
# ────────────────────────────────────────────────────────────────────────────


class TestDeriveCarriesRelay:
    def test_relay_survives_derivation(self) -> None:
        """derive overwrites the side from the closure; the authored relay must
        still be present afterward (un-clobbered)."""
        relay = RelayEntry(from_event='draw-a-card', gate='draw-to-drain')
        events = _synthetic_events()
        derived = derive_card_events(events, seed_map=_synthetic_seed_map([relay]))
        assert relay in derived['lifegain']['produced_by']

    def test_relay_coexists_with_attribute_strings(self) -> None:
        """A side with both seeds and a relay yields string attrs AND the relay;
        strings come first (sorted), relays after (sorted) — deterministic."""
        relay = RelayEntry(from_event='draw-a-card', gate='draw-to-drain')
        events = _synthetic_events()
        seed_map = _synthetic_seed_map([relay])
        # Give lifegain a real seed so the derived list has string entries too.
        seed_map['lifegain']['produced_by']['seeds'] = ['lifegain']
        seed_map['lifegain']['produced_by']['grade'] = 'M'
        derived = derive_card_events(events, seed_map=seed_map)['lifegain']['produced_by']
        strings = [e for e in derived if isinstance(e, str)]
        relays = [e for e in derived if isinstance(e, RelayEntry)]
        assert strings == sorted(strings), 'string entries must be sorted'
        assert relays == [relay]
        # strings precede relays (stable, deterministic ordering)
        assert all(isinstance(e, str) for e in derived[: len(strings)])
        assert derived[len(strings) :] == relays

    def test_no_relay_is_byte_identical_list(self) -> None:
        """A side with no relays derives exactly ``sorted(closure)`` — the relay
        plumbing adds nothing when unused (so the real catalog is unchanged)."""
        events = _synthetic_events()
        seed_map = _synthetic_seed_map([])
        seed_map['lifegain']['produced_by']['seeds'] = ['lifegain']
        derived = derive_card_events(events, seed_map=seed_map)['lifegain']['produced_by']
        assert all(isinstance(e, str) for e in derived)

    def test_relays_sorted_deterministically(self) -> None:
        r1 = RelayEntry(from_event='lifegain', gate='gain-to-draw')
        r2 = RelayEntry(from_event='draw-a-card', gate='draw-to-gain')
        events = _synthetic_events()
        derived = derive_card_events(events, seed_map=_synthetic_seed_map([r1, r2]))
        carried = [e for e in derived['lifegain']['produced_by'] if isinstance(e, RelayEntry)]
        assert carried == [r2, r1]  # sorted by (from_event, gate)


# ────────────────────────────────────────────────────────────────────────────
# 3. Validator accepts a well-formed relay, rejects a malformed one
# ────────────────────────────────────────────────────────────────────────────


class TestValidatorAcceptsRelay:
    def test_wellformed_relay_is_clean(self) -> None:
        """A relay whose ``from_event`` is a known event and ``gate`` is a
        non-empty string adds zero violations."""
        relay = RelayEntry(from_event='draw-a-card', gate='draw-to-drain')
        violations = validate_seed_map(seed_map=_synthetic_seed_map([relay]))
        assert violations == [], violations

    def test_relay_unknown_from_event_flagged(self) -> None:
        relay = RelayEntry(from_event='no-such-event', gate='draw-to-drain')
        violations = validate_seed_map(seed_map=_synthetic_seed_map([relay]))
        assert any('no-such-event' in v for v in violations), violations

    def test_relay_empty_gate_flagged(self) -> None:
        relay = RelayEntry(from_event='draw-a-card', gate='')
        violations = validate_seed_map(seed_map=_synthetic_seed_map([relay]))
        assert any('gate' in v for v in violations), violations
