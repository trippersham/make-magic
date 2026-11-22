"""Pydantic v2 boundary contracts for the graph-driven recommendation primitive.

These are the typed DTOs the recommendation primitive *returns* — the shape the
app, the skills, and a direct agent caller all consume, and from which
``model_json_schema()`` yields the MCP ``inputSchema`` / ``outputSchema``. They
mirror the ``contracts/models.py`` edge-contract conventions: Pydantic lives only
at the boundary, every model forbids unknown keys (schema drift is a loud error,
never a silent accept), and every field carries a description so the generated
schema is self-documenting.

This module is Phase 1: DTOs only. It defines **no** enumeration, query, scoring,
judgment, or serving logic, performs no I/O, persists nothing, and deliberately
does not import the strategy graph. The models realize the design's three-row
data model as a logical return contract — nothing new is persisted; the deck,
inventory, and chase stores stay as-is (see design §"Realization").

The two layers of the design map onto two row types:

* **neutral facts** — :class:`FactSnapshot`, carried per anchor on each
  :class:`StrategyGraphMembership`; deterministic and recomputable.
* **judgment** — :class:`Rationale`, carried once per proposal on a ``proposed``
  :class:`Membership`; reasoning-owned and worth persisting.

Ranking/strength is explicitly **not** computed here: it is owned by the sibling
deck-comparison primitives. :attr:`Rationale.strength` carries their typed
:class:`~pipeline.transforms.comparison.contracts.StrengthRecord` DTO verbatim —
one model, no fork. The slot stays unpopulated until that side ships its scorer
(``ordering`` remains ``degraded_structural`` meanwhile); typing it now only
pins the shape the generator will later receive.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.transforms.comparison.contracts import StrengthRecord

# --------------------------------------------------------------------------- #
# Closed vocabularies — the stable string sets the contract commits to. Kept as
# module-level ``Literal`` aliases so every model (and the generated JSON schema)
# references one source of truth rather than a scattered inline literal.
# --------------------------------------------------------------------------- #

#: A card's relationship to a deck. ``proposed`` is a not-yet-added suggestion
#: (the only ``rel`` that may carry a :class:`Rationale`); the other three mirror
#: the existing deck / inventory / chase stores.
Rel = Literal['in-deck', 'inventory', 'chase', 'proposed']

#: What a :class:`StrategyGraphMembership` anchors a proposal to. These are the
#: durable catalog vocabulary (attribute names, strategy names, event names, …),
#: carried inline — there is no separate graph-instance table to FK against.
AnchorKind = Literal['card_attribute', 'strategy', 'event_side', 'engine_slot', 'degree_axis', 'combo']

#: The directed side of an anchor an endpoint sits on (``None`` for an
#: undirected anchor such as a bare ``card_attribute``). ``producer`` /
#: ``consumer`` are the two sides of a directed event edge; ``hub`` is an engine
#: hub attribute; ``payoff`` is a degree/engine payoff role.
Direction = Literal['producer', 'consumer', 'hub', 'payoff'] | None

#: The judgment verdict on a proposed card (strongest → weakest).
Verdict = Literal['strong', 'consider', 'marginal', 'pass']

#: How confident the judgment layer is in its verdict. The four tiers mirror the
#: ``refining-decks`` swap object's ``confidence`` and ``card_tagger.get_confidence``
#: (``very-high`` / ``high`` / ``medium`` / ``low``) so the project's output
#: contracts converge on one vocabulary. In the degraded v1 judgment the top tiers
#: are reserved: a certain hard-gate rejection earns ``high``, a degraded structural
#: fit stays ``low``, and ``very-high`` awaits the strength model.
Confidence = Literal['very-high', 'high', 'medium', 'low']

#: How the candidate list is ordered. ``degraded_structural`` is the transparent
#: fallback (support/connectivity delta) used until the sibling strength model is
#: wired; ``strength_model`` is the strength-ranked order once it is.
Ordering = Literal['degraded_structural', 'strength_model']


# --------------------------------------------------------------------------- #
# Card identity — the one primitive everything references.
# --------------------------------------------------------------------------- #


class CardRef(BaseModel):
    """A printing-independent reference to a card (``oracle_id`` + ``name``).

    The lightweight join key the recommendation contract carries — the full
    enrichment lives on :class:`pipeline.contracts.models.Card`, hydrated by the
    consumer when it needs more than identity.
    """

    model_config = ConfigDict(extra='forbid')

    oracle_id: str = Field(description='Scryfall oracle_id — the durable, printing-independent card identity.')
    name: str = Field(description='Card name (Scryfall `name`).')


# --------------------------------------------------------------------------- #
# Neutral facts layer — per-anchor, deterministic, recomputable.
# --------------------------------------------------------------------------- #


class FactSnapshot(BaseModel):
    """The neutral per-anchor facts explaining why a candidate fits one anchor.

    Membership intersection alone is insufficient (design §"mechanism"): the fit
    is the matched demand attribute(s) **plus** the directed ``path`` that makes
    the candidate relevant **plus** the before/after support & connectivity delta
    the candidate would produce for that anchor's side. Every field defaults
    empty / zero so a partially-computed snapshot is still a valid contract.

    * **support** — distinct cards feeding the anchor's side.
    * **connectivity** — distinct supported neighbours on the other side.
    """

    model_config = ConfigDict(extra='forbid')

    matched_attributes: list[str] = Field(
        default_factory=list,
        description="Demand attribute name(s) the candidate matches for this anchor (from the anchor's query).",
    )
    path: list[str] = Field(
        default_factory=list,
        description='The directed graph path (node/edge refs) that makes the candidate relevant to this anchor.',
    )
    support_before: int = Field(default=0, description="Distinct cards feeding the anchor's side, before the add.")
    support_after: int = Field(default=0, description="Distinct cards feeding the anchor's side, after the add.")
    connectivity_before: int = Field(default=0, description='Distinct supported neighbours on the side, before.')
    connectivity_after: int = Field(default=0, description='Distinct supported neighbours on the side, after.')


# --------------------------------------------------------------------------- #
# Judgment layer — per proposal, reasoning-owned, worth persisting.
# --------------------------------------------------------------------------- #


class Rationale(BaseModel):
    """The judgment payload for one proposed card-in-deck — verdict, confidence, why.

    Synthesized by the reasoning/judgment layer across a proposal's anchors,
    applying context (legality, singleton, bracket, budget, does-not-want) that
    the neutral facts deliberately omit. It rides only on a ``proposed``
    :class:`Membership` (enforced there).

    ``swap_out`` is a product-level reference to an in-deck card's ``oracle_id``
    (a field, not a graph edge). ``strength`` is an OPTIONAL slot carrying the
    sibling deck-comparison effort's :class:`StrengthRecord` verbatim — the single
    source of truth for ranking, owned there, embedded (never forked) here.
    """

    model_config = ConfigDict(extra='forbid')

    verdict: Verdict = Field(description='The judgment verdict on the proposal (strong / consider / marginal / pass).')
    confidence: Confidence = Field(description='Confidence in the verdict (high / medium / low).')
    swap_out: str | None = Field(
        default=None,
        description='oracle_id of the in-deck card this proposal would replace (a reference, not a graph edge).',
    )
    prose: str = Field(description='Human-readable explanation co-presented with the neutral facts.')
    context_flags: dict[str, str] = Field(
        default_factory=dict,
        description='Context reasons keyed by concern (legality / singleton / budget / does-not-want).',
    )
    # The typed strength DTO is owned by the sibling comparison package and embedded
    # here verbatim (join by oracle_id, never name). Optional + None-default because
    # the scorer is not yet wired: v1 leaves this unpopulated and keeps
    # `ordering='degraded_structural'`; it is set, and `ordering` flips to
    # `strength_model`, once `strength()` / `marginal_participation()` land.
    strength: StrengthRecord | None = Field(
        default=None,
        description='Typed deck-comparison StrengthRecord (owned there; unpopulated until the scorer lands).',
    )


# --------------------------------------------------------------------------- #
# The polymorphic anchor edge — one per (candidate, anchor).
# --------------------------------------------------------------------------- #


class StrategyGraphMembership(BaseModel):
    """The formal, polymorphic anchor edge: a proposal anchored at a graph instance.

    Its source is a ``proposed`` :class:`Membership`; its target
    (``target_kind`` / ``target_ref``) is carried inline — the targets are the
    durable catalog vocabulary, so there is nothing to FK. One proposal fans out
    to many of these rows (several paths can explain one suggestion), each
    carrying its own neutral :class:`FactSnapshot`.
    """

    model_config = ConfigDict(extra='forbid')

    proposed_membership_ref: str = Field(
        description='Reference to the source `proposed` Membership this anchor explains.',
    )
    role: Literal['anchored_at'] = Field(
        default='anchored_at',
        description='The edge role — always `anchored_at` (the one relationship this edge expresses).',
    )
    target_kind: AnchorKind = Field(description='The kind of graph instance anchored to (closed vocabulary).')
    target_ref: str = Field(description='Stable catalog id of the anchored instance (attribute/strategy/event name).')
    direction: Direction = Field(
        default=None,
        description='Directed side of the anchor (producer/consumer/hub/payoff), or None when undirected.',
    )
    fact_snapshot: FactSnapshot = Field(
        default_factory=FactSnapshot,
        description='The neutral per-anchor facts (matched attributes, path, support/connectivity delta).',
    )


# --------------------------------------------------------------------------- #
# Membership — the Card↔Deck backbone (facts/judgment hang off `proposed`).
# --------------------------------------------------------------------------- #


class Membership(BaseModel):
    """A card's relationship to a deck — the general product backbone.

    ``rel`` spans the existing stores (``in-deck`` / ``inventory`` / ``chase``)
    plus the net-new ``proposed`` suggestion. Only a ``proposed`` membership may
    carry a :class:`Rationale` (the judgment payload) — enforced by
    :meth:`_rationale_only_on_proposed`. The strategy-graph linkage deliberately
    does **not** live here; it is the separate :class:`StrategyGraphMembership`
    edge, keyed back to a ``proposed`` row.
    """

    model_config = ConfigDict(extra='forbid')

    deck_id: str = Field(description='Identity of the deck this membership belongs to.')
    oracle_id: str = Field(description='oracle_id of the card (the durable, printing-independent identity).')
    rel: Rel = Field(description='The card↔deck relationship (in-deck / inventory / chase / proposed).')
    quantity: int = Field(default=1, ge=1, description='Number of copies (at least 1; ~moot in singleton).')
    rationale: Rationale | None = Field(
        default=None,
        description='The judgment payload — populated ONLY when `rel == "proposed"` (validated).',
    )

    @model_validator(mode='after')
    def _rationale_only_on_proposed(self) -> Membership:
        """A ``rationale`` is a judgment about a *suggestion*; reject it elsewhere.

        Carrying judgment on an ``in-deck`` / ``inventory`` / ``chase`` row would
        misplace the proposal-level opinion onto a settled relationship, so it is
        a loud construction error rather than a silently-ignored field.
        """
        if self.rationale is not None and self.rel != 'proposed':
            raise ValueError(f'rationale may only be set when rel == "proposed" (got rel={self.rel!r})')
        return self


# --------------------------------------------------------------------------- #
# Candidate + the top-level result.
# --------------------------------------------------------------------------- #


class Candidate(BaseModel):
    """One recommended card with its anchors, membership tags, and judgment.

    ``anchors`` are the (candidate, anchor) :class:`StrategyGraphMembership`
    edges that explain the suggestion structurally; ``membership_tags`` records
    the card's existing relationships to the collection (e.g. ``inventory`` =
    owned, ``chase`` = already being chased); ``rationale`` is the proposal-level
    judgment (present once the judgment layer has run).
    """

    model_config = ConfigDict(extra='forbid')

    card: CardRef = Field(description='The recommended card.')
    anchors: list[StrategyGraphMembership] = Field(
        default_factory=list,
        description='The anchor edges (with neutral facts) explaining why this card fits.',
    )
    membership_tags: list[Rel] = Field(
        default_factory=list,
        description="The card's existing relationships to the collection (e.g. inventory / chase).",
    )
    rationale: Rationale | None = Field(
        default=None,
        description='Proposal-level judgment; None until the judgment layer has run.',
    )


class RecommendationResult(BaseModel):
    """The top-level return contract of the recommendation primitive.

    Carries the candidates generated for one ``target_ref`` against one deck,
    plus the ``ordering`` that produced them (``degraded_structural`` until the
    sibling strength model is wired) and free-text ``notes`` for caller-facing
    caveats (e.g. the degraded-order disclaimer, a lake-precondition note).

    ``rejected`` is the transparency channel for the judgment layer: a candidate a
    hard gate removed for a reason the caller may care about or override — illegal /
    banned, out of color identity, a does-not-want match, over budget — is recorded
    here with a ``pass``-verdict :class:`Rationale` whose ``context_flags`` names the
    deciding gate, so a caller sees *why* a structurally-fitting card was not proposed.
    A card ALREADY IN THE DECK is NOT listed here: it is silently excluded (it is
    neither a new suggestion nor a useful rejection), except an explicit-multiples card
    (any number of copies allowed), which stays recommendable. It is empty until the
    judgment layer runs.
    """

    model_config = ConfigDict(extra='forbid')

    deck_id: str = Field(description='Identity of the deck recommendations were generated for.')
    target_ref: str = Field(description='The anchor/target the recommendation was requested against.')
    ordering: Ordering = Field(description='How `candidates` is ordered (degraded_structural / strength_model).')
    candidates: list[Candidate] = Field(default_factory=list, description='The ranked candidate cards.')
    rejected: list[Candidate] = Field(
        default_factory=list,
        description='Candidates a hard judgment gate removed, each carrying a pass-verdict rationale with the reason.',
    )
    notes: list[str] = Field(default_factory=list, description='Caller-facing caveats (e.g. degraded-order notice).')
