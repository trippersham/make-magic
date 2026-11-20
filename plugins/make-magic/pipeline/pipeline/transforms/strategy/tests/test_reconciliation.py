"""Tests for the internal engine⟷combo reconciliation metric (ADR 0026 §5).

The hermetic block drives the metric with hand-built resolve()-shaped records so
the honest-diff logic is pinned offline. The lake-gated block reproduces the hand
census (``research/class-a-census.md`` in the #83 plan dir): Class A == 26 at the
Phase-3 tip, the SAME 26 decks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pipeline.transforms.strategy.reconciliation import (
    ReconciliationMetric,
    reconciliation_metric,
)


def _record(
    name: str,
    *,
    n_win_combos: int = 0,
    recursive_units: int = 0,
    static_units: int = 0,
    loop_closes: bool = False,
    commanders: list[str] | None = None,
) -> dict:
    """Build a minimal resolve()-shaped record with just the fields the metric reads."""
    engines = [{'kind': 'recursive-engine'} for _ in range(recursive_units)]
    engines += [{'kind': 'static-payoff'} for _ in range(static_units)]
    units = {
        'engines': engines,
        'commander_loop': {'closes': loop_closes} if (loop_closes or commanders) else None,
    }
    rec: dict = {
        'name': name,
        'n_win_combos': n_win_combos,
        'facts': {'engines': {'engine_units': units}},
    }
    if commanders is not None:
        rec['commanders'] = commanders
    return rec


# ---------------------------------------------------------------------------
# Hermetic — the honest diff over synthetic records
# ---------------------------------------------------------------------------


class TestReconciliationHonestDiff:
    def test_class_a_win_without_loop(self):
        """Combo win + no recursive unit + no closing loop => Class A."""
        recs = [_record('combo-only-win', n_win_combos=2, static_units=3)]
        m = reconciliation_metric(recs)
        assert m.class_a == ('combo-only-win',)
        assert m.class_b == ()

    def test_class_b_loop_without_win(self):
        """Recursive unit + no win => Class B."""
        recs = [_record('engine-only-loop', n_win_combos=0, recursive_units=1)]
        m = reconciliation_metric(recs)
        assert m.class_b == ('engine-only-loop',)
        assert m.class_a == ()

    def test_closing_commander_loop_counts_as_engine_loop(self):
        """A closing commander loop is an engine loop: win + loop => reconciled (neither)."""
        recs = [_record('cmd-loop-win', n_win_combos=1, loop_closes=True)]
        m = reconciliation_metric(recs)
        assert m.class_a == ()
        assert m.class_b == ()

    def test_closing_commander_loop_no_win_is_class_b(self):
        recs = [_record('cmd-loop-no-win', n_win_combos=0, loop_closes=True)]
        m = reconciliation_metric(recs)
        assert m.class_b == ('cmd-loop-no-win',)

    def test_win_with_recursive_engine_is_reconciled(self):
        """Win AND recursive engine => agreement, in neither class."""
        recs = [_record('reconciled', n_win_combos=1, recursive_units=1)]
        m = reconciliation_metric(recs)
        assert m.class_a == ()
        assert m.class_b == ()

    def test_no_win_no_loop_is_neither(self):
        recs = [_record('quiet', n_win_combos=0, static_units=2)]
        m = reconciliation_metric(recs)
        assert m.class_a == ()
        assert m.class_b == ()

    def test_counts_and_sorting(self):
        recs = [
            _record('zed-win', n_win_combos=1),
            _record('alpha-win', n_win_combos=1),
            _record('beta-loop', recursive_units=1),
        ]
        m = reconciliation_metric(recs)
        assert m.n_decks == 3
        assert m.n_class_a == 2
        assert m.n_class_b == 1
        assert m.class_a == ('alpha-win', 'zed-win')  # sorted
        assert m.class_b == ('beta-loop',)

    def test_duplicate_named_builds_both_counted(self):
        """Two distinct builds share a slug (e.g. the two Shorikai builds); both count."""
        recs = [
            _record('azorius__shorikai', n_win_combos=1),
            _record('azorius__shorikai', n_win_combos=1),
        ]
        m = reconciliation_metric(recs)
        assert m.class_a == ('azorius__shorikai', 'azorius__shorikai')
        assert m.n_class_a == 2

    def test_deck_label_falls_back_to_commander(self):
        rec = _record('', n_win_combos=1, commanders=['Kess, Dissident Mage'])
        rec['name'] = None
        m = reconciliation_metric([rec])
        assert m.class_a == ('Kess, Dissident Mage',)

    def test_missing_engine_units_treated_as_no_loop(self):
        """A record with no engine_units payload is robustly read as no-loop."""
        rec = {'name': 'bare', 'n_win_combos': 1, 'facts': {'engines': {'engine_units': None}}}
        m = reconciliation_metric([rec])
        assert m.class_a == ('bare',)

    def test_frozen_and_properties(self):
        m = ReconciliationMetric(class_a=('x',), class_b=(), n_decks=1)
        assert m.n_class_a == 1
        assert m.n_class_b == 0
        with pytest.raises((AttributeError, TypeError)):
            m.class_a = ('y',)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Lake-gated — reproduce the hand census (Class A == 26, named)
# ---------------------------------------------------------------------------


def _check_lake() -> bool:
    base = os.environ.get('MAKE_MAGIC_DATA_DIR')
    root = Path(base) if base else (Path.home() / '.local/share/make-magic')
    return (root / 'raw' / 'oracle_cards.parquet').exists() and (root / 'normalized' / 'card_otag.parquet').exists()


_LAKE_AVAILABLE = _check_lake()

#: The hand census (`research/class-a-census.md`) at the Phase-3 tip = 23 named
#: Class-A decks; the 3 self-mill/GY decks Phase-3b reconciled (Bruvac/Phenax/Kess)
#: are Class A again after the 3b revert (`883baff`), for 26 total. All gauntlet.
EXPECTED_CLASS_A: frozenset[str] = frozenset(
    {
        # (a) genuinely-terminal — 7 (+ the mid Shorikai build shares the slug)
        'azorius__grand-arbiter-augustin-iv',
        'azorius__shorikai',  # two builds share this slug (mid = cat a, cedh = cat b)
        'boros__winota-joiner-of-forces',
        'grixis__nekusar-the-mindrazer',
        'mardu__kaalia-of-the-vast',
        'mardu__vihaan-goldwaker',
        'mono-red__torbran-thane-of-red-fell',
        # (b) real-loop-miss — 16
        'abzan__nethroi-apex-of-death',
        'azorius__shorikai-genesis-engine',
        'esper__aminatou-the-fateshifter',
        'esper__aminatou-veil-piercer',
        'gruul__etali-etali-primal-sickness',
        'gruul__zilortha',
        'izzet__jhoira',
        'jeskai__elsha-of-the-infinite',
        'jeskai__kykar-wind-s-fury',
        'jeskai__shiko-and-narset-unified',
        'mono-green__azusa-lost-but-seeking',
        'naya__gishath-sun-s-avatar',
        'orzhov__ketramose-the-new-dawn',
        'rakdos__prosper',
        'selesnya__hamza-guardian-of-arashin',
        # +3 self-mill/GY decks reconciled by P3b, Class A again after the revert
        'dimir__phenax-god-of-deception',
        'grixis__kess-dissident-mage',
        'mono-blue__bruvac-the-grandiloquent',
    }
)


@pytest.mark.skipif(not _LAKE_AVAILABLE, reason='lake parquets not available')
class TestReconciliationCensus:
    """The metric reproduces the hand census: Class A == 26, the same decks."""

    def test_class_a_count_and_membership(self):
        from pipeline.transforms.strategy.run import recalc_corpus

        m = reconciliation_metric(recalc_corpus())
        assert m.n_class_a == 26, f'Class A drift: expected 26, got {m.n_class_a}: {sorted(m.class_a)}'
        # The two Shorikai builds collapse to one slug in the membership set.
        assert set(m.class_a) == EXPECTED_CLASS_A, (
            f'Class-A membership drift. '
            f'unexpected={sorted(set(m.class_a) - EXPECTED_CLASS_A)} '
            f'missing={sorted(EXPECTED_CLASS_A - set(m.class_a))}'
        )
        # Exactly one duplicated slug (the two Shorikai builds), so the 26 count
        # exceeds the 25 distinct slugs by exactly one.
        assert len(m.class_a) - len(set(m.class_a)) == 1
