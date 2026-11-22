"""Shared strength contract for the deck-comparison / card-recommendation seam.

This module is the single source of truth for the typed **strength record** the
card-recommendation effort embeds verbatim into its ``Rationale.strength`` slot
(see the sibling ``pipeline/transforms/recommend/contracts.py``, where that slot
is a reserved forward reference). Ranking / strength is **owned here**: the
recommendation side *generates* candidates and this side *evaluates* them, so the
DTO lives in the comparison package and the recommendation contract carries only a
pointer to it — one model, no fork.

It is a *pure data contract*: the typed shape plus consistency validators — no
scoring logic, no I/O, no network, no numpy. ``model_json_schema()`` over
:class:`StrengthRecord` is the language-agnostic seam (the sibling serializes it
across MCP), so every field carries a description and every model forbids unknown
keys — schema drift is a loud error, never a silent accept — matching the
``contracts/models.py`` edge-contract conventions.

Two orthogonal strength axes, never collapsed into one number (design §4, §9):

* **axis A — deck-relative** (:class:`StrengthRelative`): does this card serve
  *this* plan — engine participation in an anchored role plus EDHREC
  commander-conditioned synergy/lift. The same card scores differently under a
  different commander or anchor.
* **axis B — deck-agnostic** (:class:`StrengthAgnostic`): of the cards that serve
  the plan, which is strongest — the ``edhrec_rank`` popularity backbone, the
  asymmetric promote-only ``game_changer`` / ``combo`` flags, and the
  value-density corrective.

The honest limits are carried as **first-class, always-serialized fields**, not
left to a docstring the caller may never read (design §9):

* strength is **popularity + heuristics, never win-rate** — no per-card EDH
  win-rate data exists anywhere; :attr:`StrengthRecord.basis` says so on the wire.
* a missing ``edhrec_rank`` means **UNKNOWN, not weak** — a crossover / new card
  is rank-blind, not bad; :attr:`StrengthRecord.rank_known` surfaces that, derived
  so it can never drift from the rank it describes.
* the three proxies (popularity / value-per-mana / synergy) can **disagree** on
  "more powerful" and did (Wakanda, design §9) — so there is deliberately **no
  single aggregate power score** on the record; it reports strength *with its
  basis* (:attr:`StrengthRecord.proxies_may_disagree`), and the consumer
  gates/weights axis B by axis A rather than reading one scalar.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- #
# Closed vocabularies — one source of truth per string set (schema references
# the alias, not a scattered inline literal).
# --------------------------------------------------------------------------- #

#: Confidence band annotating a strength reading. Mirrors the sibling
#: recommendation contract's ``Confidence`` and ``card_tagger.get_confidence``
#: tiers so the project converges on one vocabulary; the two are intended to be
#: asserted equal by a cross-branch test rather than shared by import (the
#: packages stay decoupled — additive, no cross-dependency). ``low`` is the honest
#: default for the diffuse popularity-heuristic midrange.
Confidence = Literal['very-high', 'high', 'medium', 'low']

#: The basis marker value. Strength is popularity + heuristics, never win-rate —
#: no per-card EDH win-rate data exists anywhere (design §6, §9). The record
#: states this so no consumer mistakes a popularity signal for a quality one.
StrengthBasis = Literal['popularity-not-winrate']


# --------------------------------------------------------------------------- #
# Axis A — deck-relative (does this card serve THIS plan).
# --------------------------------------------------------------------------- #


class StrengthRelative(BaseModel):
    """Axis A — deck-relative strength: does this card serve THIS deck's plan.

    Engine-participation value for the role the score was scoped to (design layer
    ④) plus EDHREC commander-conditioned synergy — both are *relative to a deck*,
    so the same card reads differently under a different commander or anchor.
    Every field defaults so a partially-computed reading is still a valid contract
    (matching the recommendation side's ``FactSnapshot`` convention).
    """

    model_config = ConfigDict(extra='forbid')

    anchor_event: str | None = Field(
        default=None,
        description=(
            'Event/role (from the shared 33-event catalog) axis A was scoped to, or None for a '
            'whole-deck reading — records which role the score answers for.'
        ),
    )
    engine_participation: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            'Non-negative deck-relative engine-participation signal for the anchored role '
            '(produces+consumes the event, closes a loop as a relay, repeatable/scaling payoff, '
            'commander-coupled). Higher = drives this plan more.'
        ),
    )
    edhrec_synergy: float | None = Field(
        default=None,
        description=(
            'EDHREC commander-conditioned synergy/lift — relative, not absolute, and may be '
            'negative (anti-synergy). None = unavailable, which is NOT zero synergy.'
        ),
    )


# --------------------------------------------------------------------------- #
# Axis B — deck-agnostic (of the plan-serving cards, which is strongest).
# --------------------------------------------------------------------------- #


class StrengthAgnostic(BaseModel):
    """Axis B — deck-agnostic strength: of the plan-serving cards, which is strongest.

    A composite reported as its *components*, never summed into one number (design
    §4): the ``edhrec_rank`` popularity backbone, two asymmetric promote-only
    flags, and the value-density corrective. The flags are deliberately NOT
    additive weights — see each field.
    """

    model_config = ConfigDict(extra='forbid')

    edhrec_rank: int | None = Field(
        default=None,
        gt=0,
        description=(
            'EDHREC popularity rank (1 = most played; LOWER is stronger), joined by oracle_id. '
            'None = UNKNOWN — a crossover / new card Scryfall has no rank for yet — and UNKNOWN '
            'means unranked, NOT weak; lean on the flags and value-density instead (design §9).'
        ),
    )
    game_changer: bool = Field(
        default=False,
        description=(
            'Scryfall game_changer flag. ASYMMETRIC, promote-only: True promotes the card; False '
            'carries NO signal (absence must never demote). Never a weight in an additive sum.'
        ),
    )
    combo: bool = Field(
        default=False,
        description=(
            'Combo-piece membership. ASYMMETRIC, promote-only like game_changer: True promotes; '
            'False carries no signal. Never a weight in an additive sum.'
        ),
    )
    value_density: float | None = Field(
        default=None,
        description=(
            'Value-density corrective: role-scoped Σ(attribute/event value) ÷ (MV+1), the '
            'popularity-free lever (theory-prior + ridge-shrunk hybrid). None = not computed.'
        ),
    )


# --------------------------------------------------------------------------- #
# The record — both axes + the honest-limit flags, embedded verbatim downstream.
# --------------------------------------------------------------------------- #


class StrengthRecord(BaseModel):
    """The typed strength reading for one card — the shared cross-effort contract.

    Embedded verbatim by the card-recommendation side into ``Rationale.strength``
    (ranking is owned HERE, the single source of truth). It pairs the two
    orthogonal axes and carries the honest-limit flags as first-class, always-
    serialized fields so the limits travel with the data, not in a docstring the
    consumer may skip.

    There is intentionally **no single aggregate "power" score**: the three proxies
    can disagree on "more powerful" (design §9), so strength is reported *with its
    basis* across :attr:`strength_relative` and :attr:`strength_agnostic`, and the
    consumer gates/weights axis B by axis A rather than reading one scalar.
    """

    model_config = ConfigDict(extra='forbid')

    strength_relative: StrengthRelative = Field(
        default_factory=StrengthRelative,
        description='Axis A — deck-relative fit (engine participation + commander synergy).',
    )
    strength_agnostic: StrengthAgnostic = Field(
        default_factory=StrengthAgnostic,
        description='Axis B — deck-agnostic power (edhrec_rank backbone + promote-only flags + value-density).',
    )
    confidence: Confidence = Field(
        default='low',
        description='Confidence band for this reading (default low — the diffuse popularity-heuristic midrange).',
    )
    basis: StrengthBasis = Field(
        default='popularity-not-winrate',
        description='Honest-limit marker: strength is popularity + heuristics, NEVER win-rate (no such data exists).',
    )
    rank_known: bool = Field(
        default=False,
        description=(
            'Whether an edhrec_rank was available. DERIVED from strength_agnostic.edhrec_rank and kept '
            'consistent by a validator: False = rank-blind (UNKNOWN), which means unranked, NOT weak.'
        ),
    )
    proxies_may_disagree: bool = Field(
        default=True,
        description=(
            'Honest-limit marker (structurally always True): the three proxies (popularity / '
            'value-per-mana / synergy) can disagree on "more powerful", so this record carries no '
            'single power number and must be read with its basis.'
        ),
    )

    @model_validator(mode='after')
    def _derive_rank_known(self) -> StrengthRecord:
        """Keep ``rank_known`` honest: a card is rank-known iff it has an ``edhrec_rank``.

        Derived here rather than trusted from the caller so the UNKNOWN sentinel can
        never drift from the rank it describes — a rank-blind crossover card always
        reads ``rank_known=False`` (UNKNOWN, not weak), whatever the caller passed.
        """
        self.rank_known = self.strength_agnostic.edhrec_rank is not None
        return self
