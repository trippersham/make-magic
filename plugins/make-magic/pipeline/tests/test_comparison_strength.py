"""TDD tests for strength() — the external-pool scorer (comparison/strength.py).

Offline + deterministic: candidates are injected (``candidate_cards``) and strength
signals are injected (``strength_data``), so no lake and no network are needed. The
acceptance is the popularity-bias correction: role-scoped value-density ranks cheap-
premium draw (Mystic Remora / Phyrexian Arena) ABOVE the most-PLAYED-but-mediocre
Commander's Sphere, and a rank-blind crossover card reads UNKNOWN (not weak).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.transforms.comparison.contracts import StrengthRecord
from pipeline.transforms.comparison.strength import strength
from pipeline.transforms.comparison.strength_fetch import StrengthData

FIX = Path(__file__).resolve().parents[0]
RELAY = (
    FIX.parent
    / 'pipeline'
    / 'transforms'
    / 'strategy'
    / 'tests'
    / 'fixtures'
    / 'relay_decks'
    / 'relay__queza-lichs-mastery.json'
)
DRAW5 = ['draw', 'draw-engine', 'pure-draw', 'repeatable-draw', 'repeatable-pure-draw']


@pytest.fixture()
def deck() -> dict:
    return json.loads(RELAY.read_text())


@pytest.fixture()
def draw_pool() -> dict[str, dict]:
    return {
        'remora': {'name': 'Mystic Remora', 'otags': DRAW5, 'type_line': 'Enchantment', 'cmc': 1.0},
        'arena': {'name': 'Phyrexian Arena', 'otags': DRAW5, 'type_line': 'Enchantment', 'cmc': 3.0},
        'sphere': {
            'name': "Commander's Sphere",
            'otags': ['draw', 'pure-draw', 'mana-rock', 'ramp', 'utility-mana-rock'],
            'type_line': 'Artifact',
            'cmc': 3.0,
        },
        'crossover': {'name': 'New Set MDFC', 'otags': DRAW5, 'type_line': 'Creature', 'cmc': 2.0},
    }


@pytest.fixture()
def draw_data() -> dict[str, StrengthData]:
    # Commander's Sphere is the MOST played (lowest rank) — the popularity trap.
    # The crossover card is absent entirely ⇒ UNKNOWN, not weak.
    return {
        'remora': StrengthData(edhrec_rank=103, game_changer=False, usd=1.2),
        'arena': StrengthData(edhrec_rank=106, game_changer=False, usd=3.0),
        'sphere': StrengthData(edhrec_rank=57, game_changer=False, usd=0.5),
    }


def test_returns_a_record_per_candidate(deck, draw_pool, draw_data) -> None:
    recs = strength(
        deck, list(draw_pool), anchor_event='draw-a-card', strength_data=draw_data, candidate_cards=draw_pool
    )
    assert set(recs) == set(draw_pool)
    assert all(isinstance(r, StrengthRecord) for r in recs.values())


def test_value_density_corrects_popularity_bias(deck, draw_pool, draw_data) -> None:
    """Role-scoped value-density ranks cheap-premium draw above ubiquitous filler."""
    recs = strength(
        deck, list(draw_pool), anchor_event='draw-a-card', strength_data=draw_data, candidate_cards=draw_pool
    )
    vd = {k: recs[k].strength_agnostic.value_density for k in recs}
    # Commander's Sphere is the MOST played but ranks LAST by value-density.
    assert vd['remora'] > vd['sphere']
    assert vd['arena'] > vd['sphere']
    # The cheaper premium engine out-densities the pricier one.
    assert vd['remora'] > vd['arena']


def test_crossover_is_unknown_not_weak(deck, draw_pool, draw_data) -> None:
    """A rank-blind crossover card reads UNKNOWN (rank None) yet keeps real value-density."""
    recs = strength(
        deck, list(draw_pool), anchor_event='draw-a-card', strength_data=draw_data, candidate_cards=draw_pool
    )
    cross = recs['crossover']
    assert cross.strength_agnostic.edhrec_rank is None
    assert cross.rank_known is False
    # Not weak: it has a healthy value-density and out-ranks the popular-but-mediocre filler.
    assert cross.strength_agnostic.value_density > recs['sphere'].strength_agnostic.value_density


def test_scripted_draw_slot_ranking_order(deck, draw_pool, draw_data) -> None:
    """The full corrected order, UNKNOWN card included (the acceptance transcript)."""
    recs = strength(
        deck, list(draw_pool), anchor_event='draw-a-card', strength_data=draw_data, candidate_cards=draw_pool
    )
    order = sorted(recs, key=lambda k: -recs[k].strength_agnostic.value_density)
    assert order == ['remora', 'crossover', 'arena', 'sphere']


def test_promote_flags_and_basis_travel(deck, draw_pool) -> None:
    """game_changer / combo promote-only flags surface; basis is always popularity-not-winrate."""
    data = {'remora': StrengthData(edhrec_rank=103, game_changer=True)}
    recs = strength(
        deck,
        ['remora', 'arena'],
        anchor_event='draw-a-card',
        strength_data=data,
        combo_oracle_ids={'arena'},
        candidate_cards=draw_pool,
    )
    assert recs['remora'].strength_agnostic.game_changer is True
    assert recs['arena'].strength_agnostic.combo is True
    assert recs['remora'].basis == 'popularity-not-winrate'
    assert recs['remora'].proxies_may_disagree is True


def test_commander_coupling_lifts_engine_participation(deck, draw_pool, draw_data) -> None:
    """Axis A engine_participation is non-negative and higher for a role-fitting candidate."""
    recs = strength(
        deck, list(draw_pool), anchor_event='draw-a-card', strength_data=draw_data, candidate_cards=draw_pool
    )
    # A full draw engine fits the draw role better than the mostly-off-role mana rock.
    assert recs['remora'].strength_relative.engine_participation > recs['sphere'].strength_relative.engine_participation
    assert recs['remora'].strength_relative.engine_participation >= 0.0


def test_edhrec_synergy_is_none_offline(deck, draw_pool, draw_data) -> None:
    """Commander-conditioned EDHREC synergy is unavailable offline — None, not 0."""
    recs = strength(deck, ['remora'], anchor_event='draw-a-card', strength_data=draw_data, candidate_cards=draw_pool)
    assert recs['remora'].strength_relative.edhrec_synergy is None


def test_round_trips_through_json(deck, draw_pool, draw_data) -> None:
    """Every returned record is a clean JSON round-trip (the MCP seam)."""
    recs = strength(
        deck, list(draw_pool), anchor_event='draw-a-card', strength_data=draw_data, candidate_cards=draw_pool
    )
    for r in recs.values():
        assert StrengthRecord.model_validate_json(r.model_dump_json()) == r
