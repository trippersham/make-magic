"""Phase 5: strength ranking — flips the degraded structural order to ``strength_model``.

Generation produces the structural pool and judgment gates it (Phases 3/4); this
final phase *orders* the accepted candidates by the sibling deck-comparison effort's
``strength()`` scorer, embeds each card's typed :class:`StrengthRecord` into its
``Rationale.strength``, and unlocks the ``strong`` verdict. Ranking is OWNED by the
comparison engine — this module only CONSUMES it (one model, no fork), so there is no
scoring logic here, only the consumer's two documented policy choices:

* **ordering** — axis A first (does the card serve THIS plan: ``engine_participation``),
  then the asymmetric promote flags (``game_changer`` / ``combo``), then the
  ``edhrec_rank`` popularity backbone (lower = stronger; UNKNOWN sorts last — unknown
  is *not* weak, but a rank-blind card has no popularity position), then the
  value-density corrective, then name for a stable tiebreak. This is the design's
  "gate/weight axis B by axis A", expressed as a deterministic lexicographic key — no
  single aggregate power scalar (the three proxies can disagree, design §9).
* **``strong`` verdict** — reserved for a card that BOTH serves the plan
  (``engine_participation > 0``) AND carries an asymmetric promote flag
  (``game_changer`` or ``combo``). No arbitrary ``edhrec_rank`` cutoff decides power —
  the flags are the only non-popularity high-power signal the record carries. A card
  without that pairing keeps its degraded structural verdict (``consider`` / ``marginal``
  by anchor breadth). Conservative on purpose; widen only with evidence.

The scorer is injected (``strength_fn``) so the offline suite ranks a fixture pool
without a lake or the Scryfall cache; the default path uses the real ``strength()``.
"""

from __future__ import annotations

from collections.abc import Callable

from pipeline.transforms.comparison.contracts import StrengthRecord
from pipeline.transforms.recommend.contracts import Candidate, Rationale, RecommendationResult, Verdict

#: The injectable strength scorer: ``(deck, candidate_oracle_ids, **kw) -> {oid: StrengthRecord}``.
#: The default is the comparison engine's ``strength()``; tests inject a fixture.
StrengthFn = Callable[..., dict[str, StrengthRecord]]

#: A sentinel rank for a card with no (or an UNKNOWN) ``edhrec_rank`` — it sorts AFTER
#: every genuinely-ranked card within the same axis-A / promote tier, without implying
#: it is weak (unknown ≠ weak; it simply has no popularity position to sort by).
_UNKNOWN_RANK = 10**9


def _default_strength_fn() -> StrengthFn:
    """The real comparison-engine scorer (imported lazily to keep import light)."""
    from pipeline.transforms.comparison import strength

    return strength


def _sort_key(record: StrengthRecord | None, name: str) -> tuple:
    """Deterministic strength order (ascending sort ⇒ strongest first).

    A candidate with no record (the scorer returned nothing for it) sorts last within
    its own bucket via a zero axis-A / unknown-rank reading.
    """
    if record is None:
        return (0.0, 0, _UNKNOWN_RANK, 0.0, name)
    rel, agn = record.strength_relative, record.strength_agnostic
    promote = 1 if (agn.game_changer or agn.combo) else 0
    rank = agn.edhrec_rank if agn.edhrec_rank is not None else _UNKNOWN_RANK
    vd = agn.value_density or 0.0
    # Negate the "higher is stronger" axes so a plain ascending sort puts the strongest
    # first; rank ascends as-is (lower is stronger).
    return (-rel.engine_participation, -promote, rank, -vd, name)


def _ranked_verdict(record: StrengthRecord | None, current: Verdict) -> Verdict:
    """Upgrade to ``strong`` only for a plan-serving card with a promote flag; else keep."""
    if record is not None:
        agn = record.strength_agnostic
        if record.strength_relative.engine_participation > 0 and (agn.game_changer or agn.combo):
            return 'strong'
    return current


def _reranked_rationale(rationale: Rationale | None, record: StrengthRecord | None) -> Rationale | None:
    """Embed the strength record, upgrade the verdict, and refresh the prose/confidence."""
    if rationale is None:
        return None
    verdict = _ranked_verdict(record, rationale.verdict)
    # Drop the pending-strength disclaimer now that strength IS wired; state the basis.
    base = rationale.prose.split(' Degraded structural verdict')[0].rstrip()
    if record is None:
        prose = f'{base} Strength: not scored (card absent from the strength pool).'
        confidence = rationale.confidence
    else:
        prose = f'{base} Strength ranked (popularity + heuristics, never win-rate; basis={record.basis}).'
        confidence = record.confidence
    return rationale.model_copy(
        update={'strength': record, 'verdict': verdict, 'prose': prose, 'confidence': confidence}
    )


def rank_result(
    result: RecommendationResult,
    deck: dict,
    *,
    strength_fn: StrengthFn | None = None,
    strength_data: dict | None = None,
    candidate_cards: dict[str, dict] | None = None,
    combo_oracle_ids: set[str] | None = None,
    anchor_event: str | None = None,
) -> RecommendationResult:
    """Rank the accepted candidates by strength and flip ``ordering`` to ``strength_model``.

    Scores every accepted candidate through ``strength_fn`` (default: the comparison
    engine's ``strength()``), embeds each :class:`StrengthRecord` into its rationale,
    upgrades the verdict to ``strong`` where warranted, and re-orders strongest-first.
    The ``rejected`` set is left untouched (a gated-out card is not ranked). An empty
    accepted set still relabels the ordering so the contract reflects that ranking ran.
    """
    fn = strength_fn or _default_strength_fn()
    oracle_ids = [c.card.oracle_id for c in result.candidates]
    records: dict[str, StrengthRecord] = {}
    if oracle_ids:
        records = fn(
            deck,
            oracle_ids,
            anchor_event=anchor_event,
            strength_data=strength_data,
            candidate_cards=candidate_cards,
            combo_oracle_ids=combo_oracle_ids,
        )

    ranked: list[Candidate] = [
        c.model_copy(update={'rationale': _reranked_rationale(c.rationale, records.get(c.card.oracle_id))})
        for c in result.candidates
    ]
    ranked.sort(key=lambda c: _sort_key(c.rationale.strength if c.rationale else None, c.card.name))

    return result.model_copy(update={'candidates': ranked, 'ordering': 'strength_model'})
