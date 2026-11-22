"""Serving: the ``recommend()`` library entry + its MCP-ready schema (Phase 4b).

This is the one call a caller makes to get recommendations: it composes the two
prior phases — Phase 3 :func:`~pipeline.transforms.recommend.generate.generate_candidates`
(anchors → demand-set query → structurally-fitting pool) into Phase 4a
:func:`~pipeline.transforms.recommend.judgment.judge` (enrichment + hard gates +
a degraded, transparent rationale) — and returns the single typed
:class:`~pipeline.transforms.recommend.contracts.RecommendationResult` the app, the
skills, a direct agent caller, and the ``collection recommend`` CLI verb all consume.

Why a thin composing layer (not more logic)
--------------------------------------------
The two phases already own the hard parts; serving must add no new judgment. Its
only net-new behavior is the ``target`` scope — the polymorphic narrowing of a bare
whole-deck recommendation to one anchor of interest (a Strategy, an Engine/event
ref, or a CardAttribute). That is expressed as a *filter over the enumerated
anchors* on the generated pool, never as a second query or a per-strategy branch:
an anchor is in scope when its stable ``target_ref`` is the requested target, or
when the target is one of the attributes that anchor matched. Scoping before
judgment keeps the gated-out set (:attr:`RecommendationResult.rejected`) scoped too.

Seams stay injectable
----------------------
Every I/O seam the composed phases expose is threaded straight through —
``search_client`` (retrieval), ``enrich`` (candidate enrichment), the optional
membership / price lookups — so the default path reads the OFFLINE local lake and
the unit suite injects fixtures and never touches the lake or the network
(local-first rule). The lake precondition stays loud: when it is absent on the
default path, the inherited ``collection hydrate-lake`` ``RuntimeError`` from
Phase 3/4a propagates unswallowed.
"""

from __future__ import annotations

from pipeline.transforms.recommend.anchors import DEFAULT_SATURATION, SaturationConfig
from pipeline.transforms.recommend.contracts import (
    Candidate,
    RecommendationResult,
    StrategyGraphMembership,
)
from pipeline.transforms.recommend.generate import (
    MembershipLookup,
    generate_candidates,
)
from pipeline.transforms.recommend.judgment import EnrichFn, PriceLookup, judge
from pipeline.transforms.strategy.assets import StrategyAssets
from pipeline.transforms.strategy.config import StrategyConfig
from pipeline.transforms.strategy.engine_hydration import SearchClient

#: Caveat appended when a budget ceiling is requested but no price source is wired.
#: The budget gate (Phase 4a) is active only when BOTH a budget and a ``price_lookup``
#: are supplied; a bare budget cannot filter anything, so the result says so rather
#: than silently ignoring the ceiling.
_BUDGET_INERT_NOTE = (
    'budget ceiling {budget:g} was requested but no price source is wired, so the budget '
    'gate is inactive; inject a price_lookup to enforce it'
)


def _anchor_in_scope(anchor: StrategyGraphMembership, target: str) -> bool:
    """Whether one anchor edge is within the requested ``target`` scope.

    Polymorphic by design: ``target`` may name a Strategy, an Engine/event ref, or a
    CardAttribute, and all three are the same stable-id comparison. An anchor is in
    scope when it IS that instance (``target_ref`` equals the target — the strategy /
    event / attribute node the anchor is anchored at) OR when the target is one of
    the attributes this anchor matched (so targeting an attribute also keeps the
    directed event/engine anchors that attribute sits on). No ``AnchorKind`` branch:
    the match is on identity, not kind.
    """
    return anchor.target_ref == target or target in anchor.fact_snapshot.matched_attributes


def _scope_to_target(result: RecommendationResult, target: str) -> RecommendationResult:
    """Narrow a generated pool to the anchors matching ``target``.

    Filters each candidate's anchor edges to those in scope and drops a candidate
    left with none — so a ``target`` call yields fewer / narrower anchors (and fewer
    candidates) than the bare-deck call, scoped to the requested instance. Order is
    preserved (the degraded-structural order is not recomputed). A caveat records
    the applied scope.
    """
    scoped: list[Candidate] = []
    for candidate in result.candidates:
        kept = [a for a in candidate.anchors if _anchor_in_scope(a, target)]
        if kept:
            scoped.append(candidate.model_copy(update={'anchors': kept}))
    notes = [*result.notes, f'scoped to target {target!r}: only anchors at this instance are kept']
    return result.model_copy(update={'candidates': scoped, 'notes': notes})


def recommend(
    deck: dict,
    *,
    target: str | None = None,
    include_augment: bool = False,
    search_client: SearchClient | None = None,
    enrich: EnrichFn | None = None,
    does_not_want: list[str] | None = None,
    budget: float | None = None,
    price_lookup: PriceLookup | None = None,
    inventory_lookup: MembershipLookup | None = None,
    chase_lookup: MembershipLookup | None = None,
    combos: list[dict] | None = None,
    assets: StrategyAssets | None = None,
    cfg: StrategyConfig | None = None,
    saturation: SaturationConfig = DEFAULT_SATURATION,
) -> RecommendationResult:
    """Recommend cards for a deck: compose Phase 3 generation into Phase 4a judgment.

    Generates the structurally-fitting candidate pool from the deck's anchors,
    optionally narrows it to ``target``, then judges it (enrichment + hard gates +
    a degraded rationale). The returned :class:`RecommendationResult` carries the
    accepted ``candidates`` (each with its anchors + a transparent ``rationale``)
    and the hard-gated ``rejected`` set (each naming the deciding gate) — the one
    contract the app, skills, agent callers, and the CLI verb consume.

    Parameters
    ----------
    target
        Optional polymorphic scope — a Strategy name, an Engine/event ref, or a
        CardAttribute name. Filters the enumerated anchors to that instance (a bare
        deck keeps all anchors) and labels the result's ``target_ref``.
    include_augment
        Also attach to ``augment`` anchors (deepen already-rich sides); off by
        default (complement holes are the structural suggestions).
    search_client, enrich
        The retrieval and enrichment seams, defaulting to the OFFLINE local lake;
        the unit suite injects fixtures. Keeping them injectable is what keeps the
        default suite off the lake and the network.
    does_not_want, budget, price_lookup
        Judgment context: a stated does-not-want filter, and the budget gate (active
        only when BOTH ``budget`` and ``price_lookup`` are supplied).
    inventory_lookup, chase_lookup
        Optional ``oracle_id -> bool`` membership stores; when injected a candidate
        is tagged ``inventory`` / ``chase`` accordingly.

    Raises
    ------
    RuntimeError
        On the default (lake) path when the lake is absent — the loud
        ``collection hydrate-lake`` precondition failure inherited from Phase 3/4a,
        propagated unswallowed (never a silent empty pool).
    """
    pool = generate_candidates(
        deck,
        search_client=search_client,
        include_augment=include_augment,
        target_ref=target,
        inventory_lookup=inventory_lookup,
        chase_lookup=chase_lookup,
        combos=combos,
        assets=assets,
        cfg=cfg,
        saturation=saturation,
    )
    if target is not None:
        pool = _scope_to_target(pool, target)

    judged = judge(
        pool,
        deck,
        enrich=enrich,
        does_not_want=does_not_want,
        budget=budget,
        price_lookup=price_lookup,
    )

    if budget is not None and price_lookup is None:
        judged = judged.model_copy(update={'notes': [*judged.notes, _BUDGET_INERT_NOTE.format(budget=budget)]})
    return judged


def recommendation_schema() -> dict:
    """The JSON schema of :class:`RecommendationResult` — the MCP output contract.

    A thin pass-through to Pydantic's ``model_json_schema()`` so the recommendation
    result is MCP-ready: this is the ``outputSchema`` an MCP tool advertises for the
    ``recommend`` surface (and the CLI exposes it via ``collection recommend --schema``).
    Emitted here, rather than re-encoded, so the schema can never drift from the
    contract (contracts §``model_json_schema()``).
    """
    return RecommendationResult.model_json_schema()
