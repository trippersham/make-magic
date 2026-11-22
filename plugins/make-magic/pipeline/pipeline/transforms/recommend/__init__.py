"""Graph-driven card recommendation — the typed return contract (Phase 1).

A standalone, additive subpackage that will host the recommendation primitive:
given a deck and a target (a Deck / Strategy / Engine ref / CardAttribute), it
uses the strategy graph's *structure* to suggest cards that augment or complement
the deck's strategies, replacing the scattered pre-graph discovery logic.

Phase 1 ships the boundary contract only — the Pydantic v2 DTOs the primitive
returns (:mod:`.contracts`). No enumeration, query, scoring, judgment, serving,
or I/O logic lives here yet; later phases add those behind this frozen contract.

Public contract::

    from pipeline.transforms.recommend import RecommendationResult, Candidate
"""

from __future__ import annotations

from pipeline.transforms.recommend.contracts import (
    AnchorKind,
    Candidate,
    CardRef,
    Confidence,
    Direction,
    FactSnapshot,
    Membership,
    Ordering,
    Rationale,
    RecommendationResult,
    Rel,
    StrategyGraphMembership,
    Verdict,
)

__all__ = (
    'AnchorKind',
    'Candidate',
    'CardRef',
    'Confidence',
    'Direction',
    'FactSnapshot',
    'Membership',
    'Ordering',
    'Rationale',
    'RecommendationResult',
    'Rel',
    'StrategyGraphMembership',
    'Verdict',
)
