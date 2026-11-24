"""Tests for Phase-4a judgment (enrichment + hard gates + degraded rationale).

The default suite is OFFLINE: every test injects a fixture ``EnrichFn`` over a
small synthetic ``oracle_id -> facts`` map, so no lake and no network are touched.
Judgment is a pure function over a :class:`RecommendationResult` + the deck dict,
so most tests build a tiny result by hand (fast, deterministic) rather than
re-running Phase-3 generation; one end-to-end test chains the real offline
generation into judgment. A single real-lake enricher test is marked
``integration`` and deselected by the default suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.transforms.recommend import judgment as judgment_mod
from pipeline.transforms.recommend.contracts import (
    Candidate,
    CardRef,
    FactSnapshot,
    Rationale,
    RecommendationResult,
    StrategyGraphMembership,
)
from pipeline.transforms.recommend.generate import generate_candidates
from pipeline.transforms.recommend.judgment import judge, make_lake_enricher
from pipeline.transforms.strategy.engine_hydration import make_fixture_search_client

from .conftest import LAKE_AVAILABLE

WORLD_RECLAIMER = 'owned__world-reclaimer'


# --------------------------------------------------------------------------- #
# Builders — a hand-made result so the gate logic is tested in isolation.
# --------------------------------------------------------------------------- #


def _sgm(target_ref: str = 'land-enters-battlefield') -> StrategyGraphMembership:
    return StrategyGraphMembership(
        proposed_membership_ref=f't:{target_ref}',
        target_kind='event_side',
        target_ref=target_ref,
        direction='producer',
        fact_snapshot=FactSnapshot(
            matched_attributes=['recursion-land'],
            path=['candidate', 'land-enters-battlefield/producer'],
            support_before=20,
            support_after=21,
            connectivity_before=23,
            connectivity_after=23,
        ),
    )


def _candidate(oracle_id: str, name: str, *, n_anchors: int = 1) -> Candidate:
    refs = ['land-enters-battlefield', 'graveyard-recursion', 'ramp'][:n_anchors]
    return Candidate(card=CardRef(oracle_id=oracle_id, name=name), anchors=[_sgm(r) for r in refs])


def _result(candidates: list[Candidate], *, notes: list[str] | None = None) -> RecommendationResult:
    return RecommendationResult(
        deck_id='owned__world-reclaimer',
        target_ref='land-enters-battlefield',
        ordering='degraded_structural',
        candidates=candidates,
        notes=notes if notes is not None else ['some note'],
    )


def _enricher(mapping: dict[str, dict]):
    """A fixture ``EnrichFn`` over a static ``oracle_id -> facts`` map (offline)."""

    def enrich(oracle_ids: list[str]) -> dict[str, dict]:
        return {oid: mapping[oid] for oid in oracle_ids if oid in mapping}

    return enrich


def _by_id(result: RecommendationResult, oracle_id: str) -> Candidate | None:
    hits = [c for c in result.candidates if c.card.oracle_id == oracle_id]
    return hits[0] if hits else None


def _rejected_by_id(result: RecommendationResult, oracle_id: str) -> Candidate | None:
    hits = [c for c in result.rejected if c.card.oracle_id == oracle_id]
    return hits[0] if hits else None


def _rationale(candidate: Candidate | None) -> Rationale:
    """Assert the candidate exists and carries a rationale, then return it (type-narrowing)."""
    assert candidate is not None
    assert candidate.rationale is not None
    return candidate.rationale


def _flags(candidate: Candidate | None) -> dict[str, str]:
    return _rationale(candidate).context_flags


def _deck_bgr(load_deck) -> dict:
    """The committed B/G/R golden deck — a real color identity for the CI gate."""
    return load_deck(WORLD_RECLAIMER)


# --------------------------------------------------------------------------- #
# Legality / ban-list gate.
# --------------------------------------------------------------------------- #


def test_banned_candidate_rejected_legal_survives(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-banned', 'Black Lotus'), _candidate('oid-legal', 'Sol Ring')])
    enrich = _enricher(
        {
            'oid-banned': {'color_identity': [], 'legalities': {'commander': 'banned'}},
            'oid-legal': {'color_identity': [], 'legalities': {'commander': 'legal'}},
        }
    )

    judged = judge(result, deck, enrich=enrich)

    assert _by_id(judged, 'oid-banned') is None  # banned card is not proposed
    rej = _rejected_by_id(judged, 'oid-banned')
    assert rej is not None
    assert rej.rationale is not None
    assert rej.rationale.verdict == 'pass'
    assert 'legality' in rej.rationale.context_flags
    survivor = _by_id(judged, 'oid-legal')
    assert survivor is not None
    assert survivor.rationale is not None
    assert survivor.rationale.verdict != 'pass'


def test_legality_unknown_allows_and_flags(load_deck) -> None:
    # The default lake projection has no `legalities`; an unknown verdict must
    # allow the card (never silently drop) but record the gap as a soft flag.
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-x', 'Mystery Card')])
    enrich = _enricher({'oid-x': {'color_identity': []}})  # CI known, legalities absent

    judged = judge(result, deck, enrich=enrich)

    cand = _by_id(judged, 'oid-x')
    assert cand is not None
    assert cand.rationale is not None
    assert 'unknown' in cand.rationale.context_flags.get('legality', '')


# --------------------------------------------------------------------------- #
# Color-identity gate (the filter Phase 3 could only skip).
# --------------------------------------------------------------------------- #


def test_out_of_identity_candidate_dropped_and_ci_note_removed(load_deck) -> None:
    deck = _deck_bgr(load_deck)  # B/G/R
    ci_skip_note = 'color-identity filter skipped for 1 retrieved card(s) lacking a color_identity field'
    result = _result(
        [_candidate('oid-blue', 'Blue Thing'), _candidate('oid-green', 'Green Thing')],
        notes=['keep me', ci_skip_note],
    )
    enrich = _enricher(
        {
            'oid-blue': {'color_identity': ['U']},  # outside B/G/R
            'oid-green': {'color_identity': ['G']},  # inside
        }
    )

    judged = judge(result, deck, enrich=enrich)

    assert _by_id(judged, 'oid-blue') is None
    assert 'color-identity' in _flags(_rejected_by_id(judged, 'oid-blue'))
    assert _by_id(judged, 'oid-green') is not None
    # The stale Phase-3 ci-skip note is gone now that CI is known & enforced.
    assert not any(n.startswith('color-identity filter skipped') for n in judged.notes)
    assert 'keep me' in judged.notes


def test_colorless_candidate_admitted(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-rock', 'Colorless Rock')])
    enrich = _enricher({'oid-rock': {'color_identity': [], 'legalities': {'commander': 'legal'}}})

    judged = judge(result, deck, enrich=enrich)
    assert _by_id(judged, 'oid-rock') is not None


def test_sideboard_card_does_not_widen_color_identity() -> None:
    """A green commander's deck has identity {G}; an off-color (blue) sideboard /
    considering card must NOT widen it to admit a blue candidate. Unioning every deck
    row (the former rule) let a sideboard card open the gate to off-identity cards (#119)."""
    deck = {
        'format': 'Commander',
        'cards': [
            {'oracle_id': 'cmd', 'name': 'Green Cmdr', 'role': 'commander', 'color_identity': ['G']},
            {'oracle_id': 'sb', 'name': 'Blue Sideboard', 'role': 'sideboard', 'color_identity': ['U']},
            {'oracle_id': 'land', 'name': 'Forest', 'role': 'library', 'color_identity': []},
        ],
    }
    result = _result([_candidate('oid-blue', 'Counterspell')])
    enrich = _enricher({'oid-blue': {'color_identity': ['U'], 'legalities': {'commander': 'legal'}}})

    judged = judge(result, deck, enrich=enrich)
    assert _by_id(judged, 'oid-blue') is None  # U is not within the commander's {G}
    assert _rejected_by_id(judged, 'oid-blue') is not None


def test_commander_identity_beats_maindeck_union() -> None:
    """Identity is the commander's, not the maindeck union: a mono-green commander with
    an off-color card already in the 99 still gates candidates to {G}, so a rogue row
    can't quietly license off-identity suggestions (#119)."""
    deck = {
        'format': 'Commander',
        'cards': [
            {'oracle_id': 'cmd', 'name': 'Green Cmdr', 'role': 'commander', 'color_identity': ['G']},
            {'oracle_id': 'rogue', 'name': 'Blue Intruder', 'role': 'library', 'color_identity': ['U']},
        ],
    }
    result = _result([_candidate('oid-blue', 'Brainstorm')])
    enrich = _enricher({'oid-blue': {'color_identity': ['U'], 'legalities': {'commander': 'legal'}}})

    judged = judge(result, deck, enrich=enrich)
    assert _by_id(judged, 'oid-blue') is None


# --------------------------------------------------------------------------- #
# does-not-want gate (Strategy's "what doesn't fit" hard pre-filter).
# --------------------------------------------------------------------------- #


def test_does_not_want_term_filters_matching_candidate(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-gob', 'Goblin King'), _candidate('oid-keep', 'Eternal Witness')])
    enrich = _enricher(
        {
            'oid-gob': {'color_identity': ['R'], 'legalities': {'commander': 'legal'}},
            'oid-keep': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}},
        }
    )

    judged = judge(result, deck, enrich=enrich, does_not_want=['goblin'])

    assert _by_id(judged, 'oid-gob') is None
    assert 'does-not-want' in _flags(_rejected_by_id(judged, 'oid-gob'))
    assert _by_id(judged, 'oid-keep') is not None


# --------------------------------------------------------------------------- #
# Singleton gate (defensive — Phase 3 already drops in-deck cards).
# --------------------------------------------------------------------------- #


def test_singleton_silently_excludes_in_deck_nonmultiples_card(load_deck) -> None:
    # An already-in-deck card (not explicit-multiples) is SILENTLY excluded: not
    # accepted, and NOT in `rejected` either — "already in your deck" is not a useful
    # rejection to surface (#119, product decision).
    deck = _deck_bgr(load_deck)
    in_deck_oid = deck['cards'][0]['oracle_id']
    result = _result([_candidate(in_deck_oid, 'Already In Deck')])
    enrich = _enricher({in_deck_oid: {'color_identity': [], 'legalities': {'commander': 'legal'}}})

    judged = judge(result, deck, enrich=enrich)

    assert _by_id(judged, in_deck_oid) is None  # not proposed
    assert _rejected_by_id(judged, in_deck_oid) is None  # and not surfaced as a rejection


def test_singleton_allows_explicit_multiples_card(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    in_deck_oid = deck['cards'][0]['oracle_id']
    result = _result([_candidate(in_deck_oid, 'Persistent Petitioners')])
    enrich = _enricher(
        {
            in_deck_oid: {
                'color_identity': ['U'],
                'legalities': {'commander': 'legal'},
                'oracle_text': 'A deck can have any number of cards named Persistent Petitioners.',
            }
        }
    )
    # Deck CI must allow it for the explicit-multiples path to be the deciding gate;
    # use a deck whose identity includes U by patching the fixture deck's cards is
    # overkill — instead assert the singleton gate specifically did not reject it.
    judged = judge(result, deck, enrich=enrich)
    # It may still be dropped by the CI gate (U ⊄ B/G/R); the point is the singleton
    # gate itself recognised the explicit-multiples exemption.
    assert not any(
        'singleton' in (c.rationale.context_flags if c.rationale else {})
        for c in judged.rejected
        if c.card.oracle_id == in_deck_oid
    )


def test_explicit_multiples_in_deck_is_accepted() -> None:
    # The whole point of the exemption: an any-number-of-copies card already in the deck
    # stays recommendable (accepted), not silently excluded like a plain singleton (#119).
    deck = {
        'format': 'Commander',
        'cards': [
            {'oracle_id': 'cmd', 'name': 'Mono-U Cmdr', 'role': 'commander', 'color_identity': ['U']},
            {'oracle_id': 'apostle', 'name': 'Shadowborn Apostle', 'role': 'library', 'color_identity': ['U']},
        ],
    }
    result = _result([_candidate('apostle', 'Shadowborn Apostle')])
    enrich = _enricher(
        {
            'apostle': {
                'color_identity': ['U'],
                'legalities': {'commander': 'legal'},
                'oracle_text': 'A deck can have any number of cards named Shadowborn Apostle.',
            }
        }
    )
    judged = judge(result, deck, enrich=enrich)
    assert _by_id(judged, 'apostle') is not None  # accepted despite already being in the deck
    assert _rejected_by_id(judged, 'apostle') is None


# --------------------------------------------------------------------------- #
# Budget gate (only when budget + price_lookup are injected).
# --------------------------------------------------------------------------- #


def test_budget_gate_filters_over_ceiling(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-pricey', 'Expensive'), _candidate('oid-cheap', 'Affordable')])
    enrich = _enricher(
        {
            'oid-pricey': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}},
            'oid-cheap': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}},
        }
    )
    prices = {'oid-pricey': 50.0, 'oid-cheap': 2.0}

    judged = judge(result, deck, enrich=enrich, budget=10.0, price_lookup=prices.get)

    assert _by_id(judged, 'oid-pricey') is None
    assert 'budget' in _flags(_rejected_by_id(judged, 'oid-pricey'))
    assert _by_id(judged, 'oid-cheap') is not None


def test_budget_gate_off_without_budget(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-pricey', 'Expensive')])
    enrich = _enricher({'oid-pricey': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}}})

    judged = judge(result, deck, enrich=enrich)  # no budget, no price_lookup
    assert _by_id(judged, 'oid-pricey') is not None


# --------------------------------------------------------------------------- #
# Verdict / confidence vocab + degraded ordering.
# --------------------------------------------------------------------------- #


def test_survivor_carries_valid_degraded_rationale(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-multi', 'Multi', n_anchors=3)])
    enrich = _enricher({'oid-multi': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}}})

    judged = judge(result, deck, enrich=enrich)
    cand = _by_id(judged, 'oid-multi')
    assert cand is not None
    assert cand.rationale is not None
    assert cand.rationale.verdict in {'consider', 'marginal'}
    assert cand.rationale.confidence in {'very-high', 'high', 'medium', 'low'}
    assert cand.rationale.swap_out is None  # swap-pairing is refining-decks' job (v1)
    assert 'strength' in cand.rationale.prose.lower() or 'pending' in cand.rationale.prose.lower()
    # Ordering stays the transparent degraded label; v1 never claims a strength rank.
    assert judged.ordering == 'degraded_structural'


def test_verdict_consider_for_many_anchors_marginal_for_one(load_deck) -> None:
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-many', 'Many', n_anchors=3), _candidate('oid-one', 'One', n_anchors=1)])
    enrich = _enricher(
        {
            'oid-many': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}},
            'oid-one': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}},
        }
    )

    judged = judge(result, deck, enrich=enrich)
    assert _rationale(_by_id(judged, 'oid-many')).verdict == 'consider'
    assert _rationale(_by_id(judged, 'oid-one')).verdict == 'marginal'


def test_v1_never_emits_strong_verdict(load_deck) -> None:
    # Strong implies a strength judgment the degraded layer does not have.
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-a', 'A', n_anchors=3), _candidate('oid-b', 'B', n_anchors=2)])
    enrich = _enricher(
        {
            'oid-a': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}},
            'oid-b': {'color_identity': ['G'], 'legalities': {'commander': 'legal'}},
        }
    )
    judged = judge(result, deck, enrich=enrich)
    assert all(_rationale(c).verdict != 'strong' for c in judged.candidates)


def test_hard_gate_rejection_is_high_confidence(load_deck) -> None:
    # A hard-gate reject is a *certain* pass (we know it's illegal / out of identity);
    # degraded fit, by contrast, is low-confidence.
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-banned', 'Banned')])
    enrich = _enricher({'oid-banned': {'color_identity': [], 'legalities': {'commander': 'banned'}}})
    judged = judge(result, deck, enrich=enrich)
    assert _rationale(_rejected_by_id(judged, 'oid-banned')).confidence == 'high'


# --------------------------------------------------------------------------- #
# Enrichment default-path precondition (mirrors Phase-3's loud lake failure).
# --------------------------------------------------------------------------- #


def test_enrich_absent_lake_on_default_path_fails_loud(load_deck, monkeypatch) -> None:
    def _absent_enricher():
        def enrich(_oids: list[str]) -> dict[str, dict]:
            raise FileNotFoundError('Lake data not found')

        return enrich

    monkeypatch.setattr(judgment_mod, 'make_lake_enricher', _absent_enricher)
    result = _result([_candidate('oid-x', 'X')])
    with pytest.raises(RuntimeError, match='hydrate-lake'):
        judge(result, load_deck(WORLD_RECLAIMER))  # enrich=None -> default lake path


def test_injected_enricher_errors_propagate_unwrapped(load_deck) -> None:
    def _boom(_oids: list[str]) -> dict[str, dict]:
        raise FileNotFoundError('fixture-specific IO error')

    result = _result([_candidate('oid-x', 'X')])
    with pytest.raises(FileNotFoundError, match='fixture-specific'):
        judge(result, load_deck(WORLD_RECLAIMER), enrich=_boom)


# --------------------------------------------------------------------------- #
# End-to-end — Phase-3 generation chained into judgment, fully offline.
# --------------------------------------------------------------------------- #


def test_generate_then_judge_offline_end_to_end(load_deck) -> None:
    deck = load_deck(WORLD_RECLAIMER)
    cards = [
        {
            'oracle_id': 'oid-recur',
            'name': 'Fixture Recursion',
            'otags': ['recursion-land'],
            'type_line': 'Sorcery',
            'oracle_text': '',
            'mana_cost': '',
            'color_identity': ['B'],
        }
    ]
    generated = generate_candidates(deck, search_client=make_fixture_search_client(cards))
    enrich = _enricher({'oid-recur': {'color_identity': ['B'], 'legalities': {'commander': 'legal'}}})

    judged = judge(generated, deck, enrich=enrich)
    cand = _by_id(judged, 'oid-recur')
    assert cand is not None
    assert cand.rationale is not None
    assert cand.rationale.verdict in {'consider', 'marginal'}
    assert judged.ordering == 'degraded_structural'


# --------------------------------------------------------------------------- #
# Lake enricher over a fixture parquet — the guarded legality column (OFFLINE).
#
# These write a tiny parquet and point the enricher's `lake_root` at it (no
# network, no real lake), proving BOTH ingest vintages: a fresh lake WITH the
# `legality_commander` column drives the gate decisively, and an old/pinned lake
# WITHOUT it degrades to allow + flag.
# --------------------------------------------------------------------------- #


def _write_oracle_parquet(dir_: Path, rows: list[dict], *, with_legality: bool) -> None:
    """Write a minimal ``raw/oracle_cards.parquet`` fixture under ``dir_``.

    ``with_legality`` toggles the additive ``legality_commander`` column so one
    fixture mimics a freshly-ingested lake and the other an older lake (and the
    pinned repro fixture) that predates the column.
    """
    import duckdb

    raw = dir_ / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    cols = 'oracle_id VARCHAR, color_identity VARCHAR[], oracle_text VARCHAR, type_line VARCHAR'
    select = 'oracle_id, color_identity, oracle_text, type_line'
    if with_legality:
        cols += ', legality_commander VARCHAR'
        select += ', legality_commander'
    con = duckdb.connect(':memory:')
    try:
        con.execute(f'CREATE TABLE t ({cols})')
        for r in rows:
            values = [r['oracle_id'], r.get('color_identity', []), r.get('oracle_text', ''), r.get('type_line', '')]
            if with_legality:
                values.append(r.get('legality_commander'))
            con.execute(f'INSERT INTO t ({select}) VALUES ({", ".join("?" for _ in values)})', values)
        con.execute(f"COPY t TO '{raw / 'oracle_cards.parquet'}' (FORMAT PARQUET)")
    finally:
        con.close()


def test_lake_enricher_with_legality_column_drops_banned(load_deck, tmp_path, monkeypatch) -> None:
    _write_oracle_parquet(
        tmp_path,
        [
            {'oracle_id': 'oid-banned', 'type_line': 'Artifact', 'legality_commander': 'banned'},
            {'oracle_id': 'oid-legal', 'type_line': 'Artifact', 'legality_commander': 'legal'},
        ],
        with_legality=True,
    )
    monkeypatch.setattr(judgment_mod, 'lake_root', lambda: tmp_path)

    enrich = make_lake_enricher()
    facts = enrich(['oid-banned', 'oid-legal'])
    assert facts['oid-banned']['legalities']['commander'] == 'banned'
    assert facts['oid-legal']['legalities']['commander'] == 'legal'

    # End-to-end: the populated legality makes the Phase-4a gate decisive.
    deck = _deck_bgr(load_deck)
    result = _result([_candidate('oid-banned', 'Black Lotus'), _candidate('oid-legal', 'Sol Ring')])
    judged = judge(result, deck)  # enrich=None -> default (fixture-pointed) lake path

    assert _by_id(judged, 'oid-banned') is None
    rej = _rejected_by_id(judged, 'oid-banned')
    assert rej is not None and rej.rationale is not None
    assert rej.rationale.verdict == 'pass'
    assert 'legality' in rej.rationale.context_flags
    assert _by_id(judged, 'oid-legal') is not None


def test_lake_enricher_without_legality_column_degrades(load_deck, tmp_path, monkeypatch) -> None:
    # Regression guard: an old/pinned lake lacking `legality_commander` must still
    # enrich (facts omit legalities) and the gate must degrade to allow + flag.
    _write_oracle_parquet(
        tmp_path,
        [{'oracle_id': 'oid-x', 'type_line': 'Artifact'}],
        with_legality=False,
    )
    monkeypatch.setattr(judgment_mod, 'lake_root', lambda: tmp_path)

    enrich = make_lake_enricher()
    facts = enrich(['oid-x'])
    assert 'legalities' not in facts['oid-x']  # column absent -> omitted, no crash

    deck = _deck_bgr(load_deck)
    judged = judge(_result([_candidate('oid-x', 'Mystery Card')]), deck)
    cand = _by_id(judged, 'oid-x')
    assert cand is not None and cand.rationale is not None
    assert 'unknown' in cand.rationale.context_flags.get('legality', '')


def test_lake_enricher_null_legality_degrades(load_deck, tmp_path, monkeypatch) -> None:
    # The column is present but a card's commander status is NULL (card carried no
    # legalities at ingest): treat it as unknown, not a drop.
    _write_oracle_parquet(
        tmp_path,
        [{'oracle_id': 'oid-null', 'type_line': 'Artifact', 'legality_commander': None}],
        with_legality=True,
    )
    monkeypatch.setattr(judgment_mod, 'lake_root', lambda: tmp_path)

    facts = make_lake_enricher()(['oid-null'])
    assert 'legalities' not in facts['oid-null']

    judged = judge(_result([_candidate('oid-null', 'Null Legality')]), _deck_bgr(load_deck))
    cand = _by_id(judged, 'oid-null')
    assert cand is not None and cand.rationale is not None
    assert 'unknown' in cand.rationale.context_flags.get('legality', '')


# --------------------------------------------------------------------------- #
# Real-lake enricher — OPTIONAL, integration-only (deselected by default).
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.skipif(not LAKE_AVAILABLE, reason='requires a hydrated lake')
def test_real_lake_enricher_supplies_color_identity() -> None:
    # Counterspell's oracle_id — a stable, well-known U card in the bulk data.
    counterspell = 'cc187110-1148-4090-bbb8-e205694a39f5'
    sol_ring = '6ad8011d-3471-4369-9d68-b264cc027487'
    enrich = make_lake_enricher()
    facts = enrich([counterspell, sol_ring])
    assert facts[counterspell]['color_identity'] == ['U']
    assert facts[sol_ring]['color_identity'] == []
    # Legality is read GUARDED: a lake ingested with `legality_commander` supplies it
    # (Counterspell is commander-legal); an older lake omits it. Both are valid.
    if 'legalities' in facts[counterspell]:
        assert facts[counterspell]['legalities']['commander'] == 'legal'
