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


# --------------------------------------------------------------------------- #
# R-ROOT — marginal_participation compared RAW otags against DERIVED engine
# objects. The candidate below couples to the commander and the draw engine ONLY
# through derivation (raw otags share nothing with either); the fix derives
# cand_attrs via _derive_attrs, exactly as strength() already does.
# --------------------------------------------------------------------------- #

# A card whose RAW otags intersect NEITHER the command-zone attrs nor the
# draw-a-card unit's members, but whose DERIVED attrs do: `lifegain-matters`
# derives to `lifegain` (Queza carries it), and the gain->draw oracle text
# synthesises the `gain-to-draw` relay edge (a draw-unit member).
GAIN_TO_DRAW_CAND = {
    'name': 'Gain-to-Draw Engine',
    'otags': ['lifegain-matters', 'draw'],
    'type_line': 'Enchantment',
    'cmc': 2.0,
    'oracle_text': 'Whenever you gain life, draw a card.',
}


def test_commander_coupled_is_derived_not_raw(deck) -> None:
    """R-ROOT/A1: `lifegain-matters` derives to `lifegain`, a Queza commander attr.

    RAW otag overlap with the command zone is empty, so the pre-fix code reported
    commander_coupled False (a false-negative). Deriving both sides reports True.
    """
    mp = marginal_participation(deck, 'oid-g2d', candidate_card=GAIN_TO_DRAW_CAND)
    assert mp.commander_coupled is True


def test_anchorless_delta_counts_derived_edge_mass(deck) -> None:
    """R-ROOT/A2: the draw-a-card unit's mass is only reachable via a DERIVED edge.

    With RAW cand_attrs the `gain-to-draw` draw-unit member is dropped and the
    anchorless delta under-counts at 3.0; deriving cand_attrs recovers the +1 the
    candidate pours into `draw-a-card`, for the correct 4.0.
    """
    mp = marginal_participation(deck, 'oid-g2d', candidate_card=GAIN_TO_DRAW_CAND)
    assert mp.anchor_event is None
    assert mp.delta_support == 4.0


# --------------------------------------------------------------------------- #
# R-B — idempotence: a candidate already in the deck adds nothing (Commander is
# singleton). Re-injecting it must return an all-zero / all-False delta, not a
# bogus positive from double-counting its attributes.
# --------------------------------------------------------------------------- #


def test_already_in_deck_candidate_adds_nothing(deck) -> None:
    """R-B: re-injecting a card already in the deck returns an all-zero delta."""
    in_deck = {
        'name': 'Already Here',
        'role': None,
        'otags': ['lifegain-matters', 'draw', 'draw-engine', 'repeatable-draw'],
        'type_line': 'Enchantment',
        'cmc': 2.0,
        'oracle_id': 'oid-in-deck',
    }
    deck = {**deck, 'cards': [*deck['cards'], in_deck]}
    mp = marginal_participation(deck, 'oid-in-deck', candidate_card=in_deck, anchor_event='draw-a-card')
    assert mp.delta_support == 0.0
    assert mp.delta_connectivity == 0.0
    assert mp.closes_loop is False
    assert mp.adds_payoff_axis is False
    assert mp.becomes_relay is False
    assert mp.commander_coupled is False
