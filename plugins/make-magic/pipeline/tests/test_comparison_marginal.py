"""TDD tests for marginal_participation() — the prospective-add engine delta.

Hermetic: the Queza relay fixture carries inline otags, so the engine runs offline (no
lake) and the candidate is injected via ``candidate_card``. The delta is read from the
REAL engine (``identify_engines`` → ``elect_engine_units_with_relay``) re-elected with the
candidate added — never a duplicated walk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.transforms.comparison.contracts import MarginalParticipation
from pipeline.transforms.comparison.strength import marginal_participation

RELAY = (
    Path(__file__).resolve().parents[0].parent
    / 'pipeline'
    / 'transforms'
    / 'strategy'
    / 'tests'
    / 'fixtures'
    / 'relay_decks'
    / 'relay__queza-lichs-mastery.json'
)


@pytest.fixture()
def deck() -> dict:
    return json.loads(RELAY.read_text())


def test_filler_draw_engine_returns_sane_positive_delta(deck) -> None:
    """A draw engine added to the draw-matters Queza deck raises the draw-role mass."""
    cand = {
        'name': 'Mystic Remora',
        'otags': ['draw', 'draw-engine', 'pure-draw', 'repeatable-draw', 'repeatable-pure-draw', 'draw-matters'],
        'type_line': 'Enchantment',
        'cmc': 1.0,
    }
    mp = marginal_participation(deck, 'oid-remora', candidate_card=cand, anchor_event='draw-a-card')
    assert isinstance(mp, MarginalParticipation)
    assert mp.anchor_event == 'draw-a-card'
    assert mp.delta_support > 0.0  # it pours more into the draw role
    assert mp.commander_coupled is True  # draw-matters is a Queza commander attribute


def test_off_strategy_card_is_not_commander_coupled(deck) -> None:
    """A card sharing no attribute with the command zone reads commander_coupled False."""
    cand = {'name': 'Random Goblin', 'otags': ['aggro', 'haste'], 'type_line': 'Creature', 'cmc': 2.0}
    mp = marginal_participation(deck, 'oid-goblin', candidate_card=cand)
    assert mp.commander_coupled is False


def test_delta_is_a_clean_round_trip(deck) -> None:
    cand = {
        'name': 'Test Drain',
        'otags': ['lifegain', 'drain-life', 'opponent-loses-life'],
        'type_line': 'Enchantment',
        'cmc': 3.0,
    }
    mp = marginal_participation(deck, 'oid-drain', candidate_card=cand)
    assert MarginalParticipation.model_validate(mp.model_dump()) == mp


def test_whole_deck_reading_when_no_anchor(deck) -> None:
    """With no anchor_event the delta is a whole-deck reading (anchor echo is None)."""
    cand = {
        'name': 'Test Drain',
        'otags': ['lifegain', 'drain-life', 'opponent-loses-life'],
        'type_line': 'Enchantment',
        'cmc': 3.0,
    }
    mp = marginal_participation(deck, 'oid-drain', candidate_card=cand)
    assert mp.anchor_event is None
    assert mp.delta_support >= 0.0
