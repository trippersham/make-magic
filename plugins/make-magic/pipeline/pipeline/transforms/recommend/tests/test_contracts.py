"""Contract tests for the ``recommend`` DTOs — validation, round-trip, guards.

Phase 1 is DTOs only: these tests pin the shape of the return contract the
recommendation primitive will produce (no enumeration / query / scoring / I/O).
They assert valid construction, ``model_dump`` / ``model_validate`` round-trips,
closed-vocabulary rejection, and the one invariant the model enforces:
``rationale`` may only ride on a ``proposed`` ``Membership``.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from pipeline.transforms.comparison.contracts import Confidence as ComparisonConfidence
from pipeline.transforms.comparison.contracts import StrengthAgnostic, StrengthRecord, StrengthRelative
from pipeline.transforms.recommend.contracts import (
    Candidate,
    CardRef,
    FactSnapshot,
    Membership,
    Rationale,
    RecommendationResult,
    StrategyGraphMembership,
)
from pipeline.transforms.recommend.contracts import Confidence as RecommendConfidence

# --------------------------------------------------------------------------- #
# Small builders so each test states only what it exercises.
# --------------------------------------------------------------------------- #


def _fact_snapshot() -> FactSnapshot:
    return FactSnapshot(
        matched_attributes=['land-enters-battlefield'],
        path=['candidate', 'land-enters-battlefield/producer', 'landfall-payoff'],
        support_before=2,
        support_after=3,
        connectivity_before=1,
        connectivity_after=2,
    )


def _sgm() -> StrategyGraphMembership:
    return StrategyGraphMembership(
        proposed_membership_ref='deck-1:oracle-abc',
        target_kind='event_side',
        target_ref='land-enters-battlefield',
        direction='producer',
        fact_snapshot=_fact_snapshot(),
    )


def _rationale() -> Rationale:
    return Rationale(
        verdict='strong',
        confidence='high',
        swap_out='oracle-old',
        prose='Feeds the thin producer side of a payoff-rich landfall engine.',
        context_flags={'legality': 'commander-legal', 'singleton': 'not-in-deck'},
    )


# --------------------------------------------------------------------------- #
# CardRef
# --------------------------------------------------------------------------- #


def test_card_ref_valid_and_round_trips() -> None:
    ref = CardRef(oracle_id='oracle-abc', name='Scute Swarm')
    assert ref.oracle_id == 'oracle-abc'
    assert CardRef.model_validate(ref.model_dump()) == ref


def test_card_ref_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        CardRef.model_validate({'oracle_id': 'x', 'name': 'y', 'bogus': 1})


# --------------------------------------------------------------------------- #
# FactSnapshot (neutral facts layer)
# --------------------------------------------------------------------------- #


def test_fact_snapshot_defaults_are_empty_and_zero() -> None:
    snap = FactSnapshot()
    assert snap.matched_attributes == []
    assert snap.path == []
    assert snap.support_before == 0
    assert snap.support_after == 0
    assert snap.connectivity_before == 0
    assert snap.connectivity_after == 0


def test_fact_snapshot_round_trips() -> None:
    snap = _fact_snapshot()
    assert FactSnapshot.model_validate(snap.model_dump()) == snap


# --------------------------------------------------------------------------- #
# StrategyGraphMembership (the polymorphic anchor edge)
# --------------------------------------------------------------------------- #


def test_sgm_valid_and_round_trips() -> None:
    sgm = _sgm()
    assert sgm.role == 'anchored_at'
    assert sgm.target_kind == 'event_side'
    assert StrategyGraphMembership.model_validate(sgm.model_dump()) == sgm


def test_sgm_direction_may_be_none() -> None:
    sgm = StrategyGraphMembership(
        proposed_membership_ref='deck-1:oracle-abc',
        target_kind='card_attribute',
        target_ref='draw-engine',
        direction=None,
        fact_snapshot=FactSnapshot(),
    )
    assert sgm.direction is None


@pytest.mark.parametrize('bad_kind', ['strategy_slot', 'card', '', 'Combo'])
def test_sgm_rejects_bad_target_kind(bad_kind: str) -> None:
    with pytest.raises(ValidationError):
        StrategyGraphMembership(
            proposed_membership_ref='r',
            target_kind=bad_kind,  # type: ignore[arg-type]
            target_ref='x',
            direction=None,
            fact_snapshot=FactSnapshot(),
        )


@pytest.mark.parametrize('bad_dir', ['sink', 'source', 'Producer'])
def test_sgm_rejects_bad_direction(bad_dir: str) -> None:
    with pytest.raises(ValidationError):
        StrategyGraphMembership(
            proposed_membership_ref='r',
            target_kind='event_side',
            target_ref='x',
            direction=bad_dir,  # type: ignore[arg-type]
            fact_snapshot=FactSnapshot(),
        )


@pytest.mark.parametrize('role', ['anchors', 'anchored', ''])
def test_sgm_rejects_bad_role(role: str) -> None:
    with pytest.raises(ValidationError):
        StrategyGraphMembership(
            proposed_membership_ref='r',
            role=role,  # type: ignore[arg-type]
            target_kind='event_side',
            target_ref='x',
            direction='producer',
            fact_snapshot=FactSnapshot(),
        )


# --------------------------------------------------------------------------- #
# Rationale (judgment layer) + the forward strength slot
# --------------------------------------------------------------------------- #


def test_rationale_valid_and_round_trips() -> None:
    r = _rationale()
    assert r.verdict == 'strong'
    assert r.strength is None  # reserved forward slot defaults empty
    assert Rationale.model_validate(r.model_dump()) == r


@pytest.mark.parametrize('bad_verdict', ['great', 'skip', 'STRONG', ''])
def test_rationale_rejects_bad_verdict(bad_verdict: str) -> None:
    with pytest.raises(ValidationError):
        Rationale(verdict=bad_verdict, confidence='high', prose='p')  # type: ignore[arg-type]


@pytest.mark.parametrize('bad_conf', ['hi', 'none', 'HIGH'])
def test_rationale_rejects_bad_confidence(bad_conf: str) -> None:
    with pytest.raises(ValidationError):
        Rationale(verdict='pass', confidence=bad_conf, prose='p')  # type: ignore[arg-type]


def test_rationale_swap_out_optional() -> None:
    r = Rationale(verdict='consider', confidence='medium', prose='p')
    assert r.swap_out is None
    assert r.context_flags == {}


def test_rationale_strength_accepts_typed_record_and_round_trips() -> None:
    """The ``strength`` slot is the sibling ``StrengthRecord`` verbatim: a typed
    instance is accepted, survives a dump/reload, and the embedded record is itself
    a valid ``StrengthRecord`` (one model, no fork)."""
    record = StrengthRecord(
        strength_relative=StrengthRelative(anchor_event='ramp', engine_participation=1.5),
        strength_agnostic=StrengthAgnostic(edhrec_rank=42, game_changer=True),
        confidence='high',
    )
    r = Rationale(verdict='strong', confidence='high', prose='p', strength=record)
    assert isinstance(r.strength, StrengthRecord)
    assert r.strength.strength_agnostic.edhrec_rank == 42
    assert r.strength.rank_known is True  # derived by the sibling validator, carried through
    reloaded = Rationale.model_validate(r.model_dump())
    assert reloaded == r
    assert isinstance(reloaded.strength, StrengthRecord)


def test_rationale_strength_rejects_non_conforming_payload() -> None:
    """Now that the slot is typed (no longer ``Any``), a dict that violates the
    ``StrengthRecord`` shape is a loud validation error, not a silent accept."""
    with pytest.raises(ValidationError):
        Rationale(
            verdict='strong',
            confidence='high',
            prose='p',
            strength={'strength_agnostic': {'edhrec_rank': -1}},  # type: ignore[arg-type]  # gt=0 violated
        )


def test_confidence_vocab_matches_comparison_package() -> None:
    """Cross-package invariant: the ``Confidence`` band vocabulary is mirrored, not
    shared by import, so the recommendation and deck-comparison packages stay
    decoupled (no cross-dependency). This guard asserts the two ``Literal`` sets stay
    byte-for-byte equal — a drift on either side (a renamed/added/removed band) is a
    loud failure here rather than a silent seam mismatch when a ``StrengthRecord``'s
    ``confidence`` is co-presented with a ``Rationale``'s. See the deck-comparison
    handoff (``handoff-to-card-rec.md``), which specifies this test."""
    assert get_args(RecommendConfidence) == get_args(ComparisonConfidence)


# --------------------------------------------------------------------------- #
# Membership + the rationale-only-on-proposed invariant
# --------------------------------------------------------------------------- #


def test_membership_valid_and_round_trips() -> None:
    m = Membership(deck_id='deck-1', oracle_id='oracle-abc', rel='in-deck')
    assert m.quantity == 1
    assert m.rationale is None
    assert Membership.model_validate(m.model_dump()) == m


def test_membership_proposed_may_carry_rationale() -> None:
    m = Membership(deck_id='deck-1', oracle_id='oracle-abc', rel='proposed', rationale=_rationale())
    assert m.rationale is not None


@pytest.mark.parametrize('rel', ['in-deck', 'inventory', 'chase'])
def test_membership_rationale_only_on_proposed(rel: str) -> None:
    with pytest.raises(ValidationError):
        Membership(
            deck_id='deck-1',
            oracle_id='oracle-abc',
            rel=rel,  # type: ignore[arg-type]
            rationale=_rationale(),
        )


@pytest.mark.parametrize('bad_rel', ['maindeck', 'owned', 'deck', ''])
def test_membership_rejects_bad_rel(bad_rel: str) -> None:
    with pytest.raises(ValidationError):
        Membership(deck_id='d', oracle_id='o', rel=bad_rel)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Candidate
# --------------------------------------------------------------------------- #


def test_candidate_valid_and_round_trips() -> None:
    cand = Candidate(
        card=CardRef(oracle_id='oracle-abc', name='Scute Swarm'),
        anchors=[_sgm()],
        membership_tags=['inventory', 'chase'],
        rationale=_rationale(),
    )
    assert cand.membership_tags == ['inventory', 'chase']
    assert Candidate.model_validate(cand.model_dump()) == cand


def test_candidate_defaults_empty_lists_and_no_rationale() -> None:
    cand = Candidate(card=CardRef(oracle_id='o', name='n'))
    assert cand.anchors == []
    assert cand.membership_tags == []
    assert cand.rationale is None


# --------------------------------------------------------------------------- #
# RecommendationResult (top-level return contract)
# --------------------------------------------------------------------------- #


def test_recommendation_result_valid_and_round_trips() -> None:
    result = RecommendationResult(
        deck_id='deck-1',
        target_ref='land-enters-battlefield',
        ordering='degraded_structural',
        candidates=[
            Candidate(card=CardRef(oracle_id='o1', name='Scute Swarm'), anchors=[_sgm()]),
            Candidate(card=CardRef(oracle_id='o2', name='Avenger of Zendikar')),
        ],
        notes=['ordering is degraded: strength model not yet wired'],
    )
    assert result.ordering == 'degraded_structural'
    assert len(result.candidates) == 2
    assert RecommendationResult.model_validate(result.model_dump()) == result


def test_recommendation_result_rejects_bad_ordering() -> None:
    with pytest.raises(ValidationError):
        RecommendationResult(
            deck_id='d',
            target_ref='t',
            ordering='random',  # type: ignore[arg-type]
            candidates=[],
        )


def test_recommendation_result_defaults_empty_notes() -> None:
    result = RecommendationResult(deck_id='d', target_ref='t', ordering='strength_model')
    assert result.candidates == []
    assert result.notes == []


def test_result_json_schema_emits_closed_vocab() -> None:
    # The boundary contract must survive model_json_schema() (MCP inputSchema/
    # outputSchema generation); closed vocabularies must surface as enums.
    schema = RecommendationResult.model_json_schema()
    assert isinstance(schema, dict)
    assert 'properties' in schema
