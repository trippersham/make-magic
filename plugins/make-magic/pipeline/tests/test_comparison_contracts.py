"""TDD tests for the shared strength contract (comparison/contracts.py).

This is the single artifact the card-recommendation sibling is blocked on, so
the tests pin the *shape*: construction, round-trip, the UNKNOWN-rank path, the
promote-only flag semantics, JSON serialization, and the honest-limit markers.

No network, no scoring logic — a pure data contract. The module is imported by
its full path (`pipeline.transforms.comparison.contracts`) so these tests do not
depend on the package `__init__` exports (a parallel agent owns that file).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from pipeline.transforms.comparison.contracts import (
    MarginalParticipation,
    StrengthAgnostic,
    StrengthRecord,
    StrengthRelative,
)

# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def test_defaults_construct_a_valid_record() -> None:
    """An all-default record is valid — a partially-computed reading is a contract."""
    record = StrengthRecord()
    assert isinstance(record.strength_relative, StrengthRelative)
    assert isinstance(record.strength_agnostic, StrengthAgnostic)
    assert record.confidence == 'low'
    assert record.basis == 'popularity-not-winrate'
    assert record.proxies_may_disagree is True
    # No edhrec_rank by default -> UNKNOWN, not weak.
    assert record.rank_known is False


def test_full_record_constructs() -> None:
    """A fully-populated record over both axes constructs and reads back."""
    record = StrengthRecord(
        strength_relative=StrengthRelative(
            anchor_event='draw-a-card',
            engine_participation=0.82,
            edhrec_synergy=0.31,
        ),
        strength_agnostic=StrengthAgnostic(
            edhrec_rank=147,
            game_changer=True,
            combo=False,
            value_density=1.9,
        ),
        confidence='high',
    )
    assert record.strength_relative.anchor_event == 'draw-a-card'
    assert record.strength_agnostic.edhrec_rank == 147
    assert record.rank_known is True


# --------------------------------------------------------------------------- #
# Round-trip (model_dump / model_validate)
# --------------------------------------------------------------------------- #


def test_dict_round_trip_is_identity() -> None:
    """model_validate(model_dump()) reconstructs an equal record (byte-stable seam)."""
    record = StrengthRecord(
        strength_relative=StrengthRelative(anchor_event='sacrifice', engine_participation=0.4),
        strength_agnostic=StrengthAgnostic(edhrec_rank=20, combo=True),
        confidence='medium',
    )
    assert StrengthRecord.model_validate(record.model_dump()) == record


def test_round_trip_preserves_unknown_rank() -> None:
    """A rank-blind record round-trips with rank_known False preserved."""
    record = StrengthRecord(strength_agnostic=StrengthAgnostic(edhrec_rank=None))
    restored = StrengthRecord.model_validate(record.model_dump())
    assert restored.rank_known is False
    assert restored == record


# --------------------------------------------------------------------------- #
# UNKNOWN-rank path (missing edhrec_rank => UNKNOWN, not weak)
# --------------------------------------------------------------------------- #


def test_missing_rank_is_unknown_not_weak() -> None:
    """edhrec_rank None => rank_known False; the card is unranked, not low-strength."""
    record = StrengthRecord(strength_agnostic=StrengthAgnostic(edhrec_rank=None))
    assert record.strength_agnostic.edhrec_rank is None
    assert record.rank_known is False


def test_present_rank_is_known() -> None:
    """A present edhrec_rank derives rank_known True."""
    record = StrengthRecord(strength_agnostic=StrengthAgnostic(edhrec_rank=1))
    assert record.rank_known is True


def test_rank_known_is_derived_not_trusted_from_caller() -> None:
    """rank_known is derived from the rank, so a caller cannot assert a false UNKNOWN."""
    # Caller lies: claims rank_known True while leaving the rank unknown.
    record = StrengthRecord(
        strength_agnostic=StrengthAgnostic(edhrec_rank=None),
        rank_known=True,
    )
    assert record.rank_known is False  # corrected to honest value
    # Caller lies the other way: claims UNKNOWN while a rank is present.
    record2 = StrengthRecord(
        strength_agnostic=StrengthAgnostic(edhrec_rank=500),
        rank_known=False,
    )
    assert record2.rank_known is True


def test_edhrec_rank_must_be_positive() -> None:
    """Ranks are 1-based; a non-positive rank is rejected, not silently kept."""
    with pytest.raises(ValidationError):
        StrengthAgnostic(edhrec_rank=0)


# --------------------------------------------------------------------------- #
# Promote-only flag semantics (asymmetric: present => promote; absent => no signal)
# --------------------------------------------------------------------------- #


def test_promote_flags_default_absent() -> None:
    """game_changer / combo default False (absent), never asserting a card is weak."""
    agnostic = StrengthAgnostic()
    assert agnostic.game_changer is False
    assert agnostic.combo is False


def test_promote_flags_are_independent_booleans() -> None:
    """The two promote-only flags set independently."""
    only_gc = StrengthAgnostic(game_changer=True)
    only_combo = StrengthAgnostic(combo=True)
    assert (only_gc.game_changer, only_gc.combo) == (True, False)
    assert (only_combo.game_changer, only_combo.combo) == (False, True)


# --------------------------------------------------------------------------- #
# Honest-limit markers live on the serialized record
# --------------------------------------------------------------------------- #


def test_honest_limit_markers_are_serialized_fields() -> None:
    """basis / proxies_may_disagree / rank_known travel with the data, not a docstring."""
    dumped = StrengthRecord(strength_agnostic=StrengthAgnostic(edhrec_rank=10)).model_dump()
    assert dumped['basis'] == 'popularity-not-winrate'
    assert dumped['proxies_may_disagree'] is True
    assert dumped['rank_known'] is True


def test_no_single_aggregate_power_score() -> None:
    """There is deliberately no scalar 'power'/'score' field (proxies can disagree)."""
    fields = set(StrengthRecord.model_fields)
    assert not (fields & {'score', 'power', 'strength', 'total', 'rank'})


# --------------------------------------------------------------------------- #
# Axis-A value semantics
# --------------------------------------------------------------------------- #


def test_engine_participation_is_non_negative() -> None:
    """Deck-relative participation is a non-negative signal; a negative is rejected."""
    with pytest.raises(ValidationError):
        StrengthRelative(engine_participation=-0.1)


def test_edhrec_synergy_may_be_negative() -> None:
    """EDHREC synergy/lift is relative and can be negative (anti-synergy)."""
    rel = StrengthRelative(edhrec_synergy=-0.5)
    assert rel.edhrec_synergy == -0.5


# --------------------------------------------------------------------------- #
# JSON serialization (the sibling serializes this across MCP)
# --------------------------------------------------------------------------- #


def test_json_round_trip() -> None:
    """model_dump_json / model_validate_json reconstructs an equal record."""
    record = StrengthRecord(
        strength_relative=StrengthRelative(anchor_event='treasure', engine_participation=0.6),
        strength_agnostic=StrengthAgnostic(edhrec_rank=333, game_changer=True, value_density=0.9),
        confidence='very-high',
    )
    assert StrengthRecord.model_validate_json(record.model_dump_json()) == record


def test_json_is_plain_data() -> None:
    """The JSON payload is plain JSON-native data (what crosses the MCP boundary)."""
    payload = json.loads(StrengthRecord(strength_agnostic=StrengthAgnostic(edhrec_rank=5)).model_dump_json())
    assert payload['strength_agnostic']['edhrec_rank'] == 5
    assert payload['basis'] == 'popularity-not-winrate'
    assert payload['rank_known'] is True


# --------------------------------------------------------------------------- #
# extra='forbid' — schema drift is loud
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('model', [StrengthRecord, StrengthRelative, StrengthAgnostic])
def test_unknown_key_is_rejected(model: type) -> None:
    """An unknown key is a loud error on every model (no silent schema drift)."""
    with pytest.raises(ValidationError):
        model.model_validate({'definitely_not_a_field': 1})


def test_bad_confidence_rejected() -> None:
    """confidence is a closed vocabulary."""
    with pytest.raises(ValidationError):
        StrengthRecord(confidence='stellar')  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# MarginalParticipation — the prospective-add engine delta (entry point 2)
# --------------------------------------------------------------------------- #


def test_marginal_participation_defaults_are_a_null_delta() -> None:
    """An all-default MarginalParticipation is a valid 'no effect' reading."""
    mp = MarginalParticipation()
    assert mp.anchor_event is None
    assert mp.delta_support == 0.0
    assert mp.delta_connectivity == 0.0
    assert mp.closes_loop is False
    assert mp.adds_payoff_axis is False
    assert mp.becomes_relay is False
    assert mp.commander_coupled is False


def test_marginal_participation_has_exactly_the_contracted_fields() -> None:
    """The six named deltas + the anchor echo — the shape the sibling consumes."""
    assert set(MarginalParticipation.model_fields) == {
        'anchor_event',
        'delta_support',
        'delta_connectivity',
        'closes_loop',
        'adds_payoff_axis',
        'becomes_relay',
        'commander_coupled',
    }


def test_marginal_participation_round_trips() -> None:
    """model_validate(model_dump()) reconstructs an equal delta (shared seam)."""
    mp = MarginalParticipation(
        anchor_event='permanent-sacrificed',
        delta_support=12.0,
        delta_connectivity=1.0,
        closes_loop=True,
        adds_payoff_axis=True,
        becomes_relay=True,
        commander_coupled=True,
    )
    assert MarginalParticipation.model_validate(mp.model_dump()) == mp


def test_marginal_participation_rejects_unknown_key() -> None:
    """extra='forbid' — no silent schema drift on the delta contract."""
    with pytest.raises(ValidationError):
        MarginalParticipation.model_validate({'nope': 1})
