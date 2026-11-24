"""Fixtures for the ``recommend`` suite — reuse the strategy engine's golden decks.

Anchor enumeration consumes the strategy engine over a resolved deck, so the
tests run on the same committed, fully-attached golden deck fixtures the strategy
suite uses (``transforms/strategy/tests/fixtures/decks/*.json``). Those decks
carry resolved ``otags`` on every card, so ``compute_facts`` runs offline — no
lake, no network — exactly as the default suite requires.

``LAKE_AVAILABLE`` mirrors the strategy conftest gate: event adjacency is DERIVED
from the lake taxonomy when present and falls back to the committed enumerated
adjacency offline (ADR 0022). Card-level support/connectivity is stable across
both, but a test that asserts a lake-only derived shape gates on this flag.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

#: The strategy suite's committed golden decks (resolved + meta-attached).
_STRATEGY_DECKS = Path(__file__).resolve().parents[2] / 'strategy' / 'tests' / 'fixtures' / 'decks'


def _check_lake() -> bool:
    """True when the lake taxonomy parquet is present (derived adjacency active)."""
    base = os.environ.get('MAKE_MAGIC_DATA_DIR')
    root = Path(base) if base else (Path.home() / '.local' / 'share' / 'make-magic')
    return (root / 'raw' / 'oracle_tags.parquet').exists()


#: ``False`` on offline CI; ``True`` locally with a hydrated lake.
LAKE_AVAILABLE = _check_lake()


@pytest.fixture
def load_deck():
    """Return a loader ``load_deck(slug)`` for a committed golden deck fixture."""

    def _load(slug: str) -> dict:
        return json.loads((_STRATEGY_DECKS / f'{slug}.json').read_text())

    return _load
