"""Tests for the internal engine⟷combo reconciliation metric (ADR 0026 §5).

The hermetic block drives the metric with hand-built resolve()-shaped records so
the honest-diff logic is pinned offline. The lake-gated block reproduces the hand
census (``research/class-a-census.md`` in the #83 plan dir) PLUS the 8 decks the
Phase-3 combo-certified families had reconciled: with the 7 families deferred to #90,
those 8 return to the "loops to author" queue, so Class A == 34 and Class B == 42.
"""

from __future__ import annotations

import json
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
# Pinned-lake census — reproduce the families-deferred census (Class A == 34)
# ---------------------------------------------------------------------------

#: The families-deferred Class-A census (gauntlet, 170 decks): 32 distinct slugs, 34
#: total (``azorius__shorikai`` and ``rakdos__prosper`` each carry two builds). This is
#: the hand census (`research/class-a-census.md`, 23 named + the 3 self-mill/GY decks
#: Bruvac/Phenax/Kess) PLUS the 8 decks the Phase-3 combo-certified families reconciled
#: and that return to Class A now that the 7 families are deferred to #90: Karador,
#: Derevi, Sharuum, Xenagos, Veyran, Selvala, Xyris, and the second Prosper build.
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
        # (c) self-mill/GY decks reconciled by P3b, Class A again after the revert — 3
        'dimir__phenax-god-of-deception',
        'grixis__kess-dissident-mage',
        'mono-blue__bruvac-the-grandiloquent',
        # (d) decks the Phase-3 combo-certified families reconciled, returned to Class A
        #     by the families-deferred scope reduction (#90) — 7 distinct slugs
        #     (the 8th reconciled deck is the second ``rakdos__prosper`` build above).
        'abzan__karador-ghost-chieftain',
        'bant__derevi-empyrial-tactician',
        'esper__sharuum-the-hegemon',
        'gruul__xenagos',
        'izzet__veyran',
        'mono-green__selvala-heart-of-the-wilds',
        'temur__xyris-the-writhing-storm',
    }
)


#: The subprocess that computes the census inside the isolated pinned lake. It prints
#: exactly one ``CENSUS <json>`` line so the parent reads the counts unambiguously past
#: any adjacency/warning chatter on stdout.
_CENSUS_RUNNER = (
    'import json, sys\n'
    'from pipeline.transforms.strategy.run import recalc_corpus\n'
    'from pipeline.transforms.strategy.reconciliation import reconciliation_metric\n'
    'm = reconciliation_metric(recalc_corpus())\n'
    'payload = {\n'
    '    "n_a": m.n_class_a,\n'
    '    "n_b": m.n_class_b,\n'
    '    "members": sorted(set(m.class_a)),\n'
    '    "dupes": len(m.class_a) - len(set(m.class_a)),\n'
    '}\n'
    'sys.stdout.write("CENSUS " + json.dumps(payload) + "\\n")\n'
)


class TestReconciliationCensus:
    """The metric reproduces the families-deferred census: Class A == 34, Class B == 42.

    Bound to the committed ``tests/repro/pinned_lake`` fixture (all FOUR extraction
    inputs; ADR 0003/0022) and run in a FRESH subprocess with ``MAKE_MAGIC_DATA_DIR``
    seeded from it — the same isolation the reproducibility harness uses. This makes
    the counts reproducible on a clean checkout: the in-process path reads the
    developer's live lake (dev-only, and it falls into the ADR-0022 enumerated-
    adjacency fallback when the lake lacks ``normalized/combo.parquet`` /
    ``raw/oracle_tags.parquet``), while the pinned fixture pins the coherent lake the
    re-baselined composites are valid against. The pipeline memoizes assets and
    lru-caches adjacency, so the data dir is swapped in a subprocess, not in-process.
    """

    def test_class_a_count_and_membership(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        from pipeline.transforms.strategy.tests.repro import harness

        # Root of trust: the committed fixture must match its manifest before use.
        harness.verify_pinned_fixture()
        lake = tmp_path / 'lake'
        harness.seed_lake(lake, harness.SEED_FILES)

        env = dict(os.environ)
        env['MAKE_MAGIC_DATA_DIR'] = str(lake)
        cp = subprocess.run(
            [sys.executable, '-c', _CENSUS_RUNNER],
            cwd=str(harness.pipeline_root()),
            env=env,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        assert cp.returncode == 0, f'census subprocess failed (rc={cp.returncode}):\n{cp.stderr[-3000:]}'
        # A lake missing raw/oracle_tags silently switches to a DIFFERENT adjacency
        # algorithm (ADR 0022); the four-file seed must prevent it.
        blob = (cp.stdout or '') + (cp.stderr or '')
        assert harness.FALLBACK_SIGNATURE not in blob, (
            'ADR-0022 enumerated-adjacency fallback fired — lake under-seeded'
        )

        census_lines = [ln for ln in cp.stdout.splitlines() if ln.startswith('CENSUS ')]
        assert len(census_lines) == 1, f'expected one CENSUS line, got {census_lines!r}\nstdout:\n{cp.stdout[-2000:]}'
        result = json.loads(census_lines[0][len('CENSUS ') :])

        assert result['n_a'] == 34, f'Class A drift: expected 34, got {result["n_a"]}: {result["members"]}'
        assert result['n_b'] == 42, f'Class B drift: expected 42, got {result["n_b"]}'
        members = set(result['members'])
        assert members == EXPECTED_CLASS_A, (
            f'Class-A membership drift. '
            f'unexpected={sorted(members - EXPECTED_CLASS_A)} '
            f'missing={sorted(EXPECTED_CLASS_A - members)}'
        )
        # Two duplicated slugs (the two Shorikai builds and the two Prosper builds), so
        # the 34 count exceeds the 32 distinct slugs by exactly two.
        assert result['dupes'] == 2
