"""Offline fail-open for the otag taxonomy loader (BLOCKER-2 regression).

``derive_card_events.load_taxonomy`` reads the hydrated
``$MAKE_MAGIC_DATA_DIR/raw/oracle_tags.parquet``. On a fresh/unhydrated install
that parquet is absent; the loader must FAIL-OPEN to the committed offline
snapshot (``data/snapshots/oracle_tags.json.gz``, full tag DAG) rather than
raise ``FileNotFoundError``. Without this, the taxonomy-gated derivation never
runs offline and the SEED_MAP lifetrade relays (relocated there in #83 Phase 2)
are lost, so the recursive lifetrade loop no longer closes with no lake.

These tests force the no-lake path with an empty ``MAKE_MAGIC_DATA_DIR`` and the
loader's ``lru_cache`` cleared, so they fail without the snapshot fail-open and
need no hydrated lake to run (they are part of the default offline suite).
"""

from __future__ import annotations

import pytest

from pipeline.transforms.strategy import derive_card_events as dce
from pipeline.transforms.strategy.derive_card_events import (
    SEED_MAP,
    descendant_closure,
    load_taxonomy,
)


@pytest.fixture()
def empty_lake(tmp_path, monkeypatch):
    """Point the taxonomy loader at an empty data dir (no parquet) + clear caches.

    Yields the taxonomy loaded from the bundled snapshot. The ``load_taxonomy``
    cache is cleared before and after so neither a hydrated lake loaded by an
    earlier test nor this empty-lake view leaks across tests.
    """
    monkeypatch.setenv('MAKE_MAGIC_DATA_DIR', str(tmp_path))
    assert not (tmp_path / 'raw' / 'oracle_tags.parquet').exists()
    load_taxonomy.cache_clear()
    try:
        yield load_taxonomy()
    finally:
        load_taxonomy.cache_clear()


class TestLoadTaxonomyFailsOpenToSnapshot:
    def test_no_parquet_does_not_raise(self, empty_lake) -> None:
        """An absent parquet returns the snapshot DAG instead of FileNotFoundError."""
        assert isinstance(empty_lake, dict)
        assert empty_lake  # non-empty

    def test_full_dag_present(self, empty_lake) -> None:
        """The snapshot carries the full tag DAG (thousands of slugs)."""
        # The committed snapshot keeps all 4,499 tags; assert the structure is
        # the real DAG, not a thin slice.
        assert len(empty_lake) > 4000

    def test_known_tag_children_and_parents(self, empty_lake) -> None:
        """A known tag resolves its parent/child UUIDs back to slugs."""
        # ``tutor-land`` is a stable tag: parent ``tutor``, children include
        # ``tutor-land-basic``.
        entry = empty_lake['tutor-land']
        assert 'tutor' in entry['parents']
        assert 'tutor-land-basic' in entry['children']
        assert entry['description']  # non-empty description carried through

    def test_closure_structure_usable(self, empty_lake) -> None:
        """Descendant closure works over the snapshot taxonomy."""
        closure = descendant_closure('tutor-land', empty_lake)
        assert 'tutor-land' in closure
        assert 'tutor-land-basic' in closure


class TestDeriveAppliesRelaysOffline:
    """``derive_card_events`` applies the 6 lifetrade relays with no lake."""

    def test_six_lifetrade_relays_survive(self, empty_lake) -> None:
        # Build a minimal events dict from the relay-carrying SEED_MAP entries and
        # derive it under the empty-lake taxonomy (the ``empty_lake`` fixture has
        # already pointed the loader at the snapshot and cleared the cache).
        events = {name: {'produced_by': [], 'triggers': []} for name in ('lifegain', 'life-loss-drain', 'draw-a-card')}
        derived = dce.derive_card_events(events)

        found: set[tuple[str, str, str]] = set()
        for owning in events:
            for relay in derived[owning]['produced_by']:
                if isinstance(relay, dce.RelayEntry):
                    found.add((owning, relay.from_event, relay.gate))

        expected = set()
        for owning in events:
            for relay in SEED_MAP[owning]['produced_by'].get('relays', []):
                expected.add((owning, relay.from_event, relay.gate))
        assert len(expected) == 6  # the 6 lifetrade relays
        assert found == expected


class TestQuezaRelayClosesOffline:
    """Behavioral: the Queza recursive lifetrade SCC closes with no lake."""

    def test_recursive_lifetrade_loop_closes(self, empty_lake) -> None:
        from pipeline.transforms.strategy.strategy_graph import recursive_event_set

        # Minimal events dict carrying only the lifetrade relays (produced_by).
        events: dict = {}
        for name in ('lifegain', 'life-loss-drain', 'draw-a-card'):
            relays = SEED_MAP[name]['produced_by'].get('relays', [])
            events[name] = {'produced_by': list(relays), 'triggers': []}

        # Both enabler gates present (Sanguine Bond + Exquisite Blood): the
        # lifegain <-> life-loss-drain SCC must close offline.
        present = {'gain-to-drain', 'drain-to-gain', 'lifegain', 'life-loss-matters'}
        rec = recursive_event_set([], events, set(), present)
        assert {'lifegain', 'life-loss-drain'} <= rec
