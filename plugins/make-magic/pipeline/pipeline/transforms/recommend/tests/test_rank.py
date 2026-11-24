"""Tests for Phase-5 strength ranking (``rank.rank_result``).

Offline + hermetic: a fixture ``strength_fn`` returns crafted
:class:`StrengthRecord`s, so ranking is exercised without a lake or the Scryfall
strength cache. The ordering + ``strong``-verdict POLICY lives in ``rank.py``; these
pin it. The scorer itself is the comparison engine's and is tested there.
"""

from __future__ import annotations

from collections.abc import Callable

from pipeline.transforms.comparison.contracts import StrengthAgnostic, StrengthRecord, StrengthRelative
from pipeline.transforms.recommend.contracts import (
    Candidate,
    CardRef,
    FactSnapshot,
    Rationale,
    RecommendationResult,
    StrategyGraphMembership,
    Verdict,
)
from pipeline.transforms.recommend.rank import rank_result


def _sgm(target_ref: str = 'land-enters-battlefield') -> StrategyGraphMembership:
    return StrategyGraphMembership(
        proposed_membership_ref=f't:{target_ref}',
        target_kind='event_side',
        target_ref=target_ref,
        direction='producer',
        fact_snapshot=FactSnapshot(matched_attributes=['recursion-land'], path=['candidate']),
    )


def _accepted(oracle_id: str, name: str, *, verdict: Verdict = 'marginal', n_anchors: int = 1) -> Candidate:
    refs = ['land-enters-battlefield', 'graveyard-recursion', 'ramp'][:n_anchors]
    return Candidate(
        card=CardRef(oracle_id=oracle_id, name=name),
        anchors=[_sgm(r) for r in refs],
        rationale=Rationale(
            verdict=verdict,
            confidence='low',
            prose='Fills 1 structural anchor(s). Degraded structural verdict — strength ranking is pending.',
            context_flags={},
        ),
    )


def _result(candidates: list[Candidate]) -> RecommendationResult:
    return RecommendationResult(deck_id='d', target_ref='d', ordering='degraded_structural', candidates=candidates)


def _record(*, fit: float = 0.0, rank: int | None = None, gc: bool = False, combo: bool = False) -> StrengthRecord:
    return StrengthRecord(
        strength_relative=StrengthRelative(engine_participation=fit),
        strength_agnostic=StrengthAgnostic(edhrec_rank=rank, game_changer=gc, combo=combo),
    )


def _strength_fn(records: dict[str, StrengthRecord]) -> Callable[..., dict[str, StrengthRecord]]:
    def fn(_deck: dict, oracle_ids: list[str], **_kw: object) -> dict[str, StrengthRecord]:
        return {oid: records[oid] for oid in oracle_ids if oid in records}

    return fn


def test_rank_flips_ordering_and_embeds_record() -> None:
    result = _result([_accepted('a', 'A')])
    ranked = rank_result(result, {'cards': []}, strength_fn=_strength_fn({'a': _record(fit=1.0, rank=100)}))
    assert ranked.ordering == 'strength_model'
    assert ranked.candidates[0].rationale is not None
    assert ranked.candidates[0].rationale.strength is not None
    assert ranked.candidates[0].rationale.strength.strength_agnostic.edhrec_rank == 100


def test_rank_orders_strongest_first() -> None:
    # Generation order (weak, strong); ranking must put the stronger (higher axis-A fit
    # + a promote flag + a known rank) first.
    weak = _accepted('w', 'Weak')
    strong = _accepted('s', 'Strong')
    records = {'w': _record(fit=0.0, rank=None), 's': _record(fit=2.0, rank=50, gc=True)}
    ranked = rank_result(_result([weak, strong]), {'cards': []}, strength_fn=_strength_fn(records))
    assert [c.card.oracle_id for c in ranked.candidates] == ['s', 'w']


def test_rank_axis_a_fit_outranks_better_popularity() -> None:
    # Axis A (serves THIS plan) gates axis B: a card that fits the plan ranks above a
    # more-popular card that does not, even with a far better edhrec_rank.
    fits = _accepted('fit', 'Fits Plan')
    popular = _accepted('pop', 'Popular Misfit')
    records = {'fit': _record(fit=3.0, rank=5000), 'pop': _record(fit=0.0, rank=1)}
    ranked = rank_result(_result([popular, fits]), {'cards': []}, strength_fn=_strength_fn(records))
    assert [c.card.oracle_id for c in ranked.candidates] == ['fit', 'pop']


def test_rank_unlocks_strong_for_plan_serving_promote_card() -> None:
    c = _accepted('s', 'Strong', verdict='consider')
    ranked = rank_result(
        _result([c]), {'cards': []}, strength_fn=_strength_fn({'s': _record(fit=1.0, rank=10, gc=True)})
    )
    assert ranked.candidates[0].rationale is not None
    assert ranked.candidates[0].rationale.verdict == 'strong'


def test_rank_no_promote_flag_keeps_degraded_verdict() -> None:
    # Great rank + strong fit but NO promote flag ⇒ not `strong` (no arbitrary cutoff).
    c = _accepted('x', 'X', verdict='consider')
    ranked = rank_result(_result([c]), {'cards': []}, strength_fn=_strength_fn({'x': _record(fit=5.0, rank=1)}))
    assert ranked.candidates[0].rationale is not None
    assert ranked.candidates[0].rationale.verdict == 'consider'


def test_rank_combo_flag_also_unlocks_strong() -> None:
    c = _accepted('c', 'Combo Piece', verdict='marginal')
    ranked = rank_result(_result([c]), {'cards': []}, strength_fn=_strength_fn({'c': _record(fit=1.0, combo=True)}))
    assert ranked.candidates[0].rationale is not None
    assert ranked.candidates[0].rationale.verdict == 'strong'


def test_rank_unscored_candidate_sorts_last_with_strength_none() -> None:
    scored = _accepted('a', 'A')
    unscored = _accepted('z', 'Z')
    records = {'a': _record(fit=1.0, rank=100)}  # 'z' not scored
    ranked = rank_result(_result([unscored, scored]), {'cards': []}, strength_fn=_strength_fn(records))
    assert [c.card.oracle_id for c in ranked.candidates] == ['a', 'z']
    assert ranked.candidates[1].rationale is not None
    assert ranked.candidates[1].rationale.strength is None


def test_rank_empty_pool_relabels_ordering() -> None:
    ranked = rank_result(_result([]), {'cards': []}, strength_fn=_strength_fn({}))
    assert ranked.ordering == 'strength_model'
    assert ranked.candidates == []
