"""Candidate generation: anchors → demand-set query → lake retrieval (Phase 3).

Turns a deck's enumerated anchors (Phase 2) into a pool of recommended cards. The
chain is deterministic and source-only — it finds *which* cards structurally fit a
deck's thin sides; it does **not** judge them (verdict / swap / prose is Phase 4)
and does **not** score them (scalar strength is the sibling deck-comparison
effort). The order it emits is the transparent ``degraded_structural`` fallback,
clearly labelled, used until that strength model is wired.

Mechanism (design §"mechanism" pipeline steps 4-5)
--------------------------------------------------
1. :func:`~pipeline.transforms.recommend.anchors.enumerate_anchors` gives the
   deck's anchors with their saturation. Generation attaches to ``complement``
   anchors (the thin side of a rich edge — a *structural hole*) by default, and
   optionally to ``augment`` anchors (deepening an already-rich side).
2. Each anchor's ``matched_attributes`` are its **demand set**. The demand-set
   query is the OR of each attribute's membership query (``compile_query`` then an
   injected :data:`SearchClient`). One query per unique demand set, run once.
3. Retrieved cards are filtered: intersect the deck's color identity, drop cards
   already in the deck, and (only when an inventory / chase lookup is injected)
   tag the card's existing collection relationships.
4. Each surviving (candidate, anchor) pair becomes one
   :class:`StrategyGraphMembership` carrying the neutral per-anchor facts: the
   attribute(s) this card actually matched, the anchor's directed path, and the
   before/after support/connectivity delta. A card joins the thin side, so
   ``support_after = support_before + 1``; connectivity is unchanged in singleton.
5. A card that fills several anchors dedupes into one :class:`Candidate` carrying
   all its anchor edges.

Why the search client is injected
---------------------------------
``SearchClient = Callable[[str], list[dict]]`` is a seam, not a dependency: the
default path scans the local lake (:func:`make_lake_search_client`), but the unit
suite injects :func:`make_fixture_search_client` over a synthetic card set so it
stays offline. The lake is a hard precondition on the default path — when it is
absent generation fails loud, pointing at ``collection hydrate-lake`` (design
resolution #5), rather than silently returning an empty pool.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache

from pipeline.transforms.recommend.anchors import (
    DEFAULT_SATURATION,
    Anchor,
    AnchorMode,
    SaturationConfig,
    enumerate_anchors,
)
from pipeline.transforms.recommend.contracts import (
    Candidate,
    CardRef,
    FactSnapshot,
    Ordering,
    RecommendationResult,
    Rel,
    StrategyGraphMembership,
)
from pipeline.transforms.strategy.assets import VOCAB_FILE, StrategyAssets, is_land
from pipeline.transforms.strategy.config import StrategyConfig
from pipeline.transforms.strategy.distill_strategies import (
    SYNTH_ALWAYS_ATTRIBUTES,
    SYNTH_EDHREC_ATTRIBUTES,
)
from pipeline.transforms.strategy.engine_hydration import (
    SearchClient,
    make_lake_search_client,
)
from pipeline.transforms.strategy.query import CardView, Predicate, compile_query

#: A membership-store lookup: ``oracle_id -> bool`` ("does the collection hold
#: this card in this relationship?"). Injected per store so generation never hard-
#: depends on the inventory / chase stores (they stay optional — the unit suite
#: passes none).
MembershipLookup = Callable[[str], bool]

#: The degraded-order disclaimer attached to every result until the sibling
#: strength model is wired (design §"Interface"). Stated in ``notes`` so a caller
#: never mistakes the structural fallback for a strength ranking.
_DEGRADED_ORDER_NOTE = (
    'ordering is degraded_structural (support/connectivity delta), a transparent '
    'fallback pending the deck-comparison strength model; it is not a strength ranking'
)


def _subtype_concentration_query(args: list[str]) -> str:
    """Membership query for the ``subtype-concentration`` family given its subtypes.

    Mirrors the engine's own rule — a card is in ``subtype-concentration[Elf,Goblin]``
    iff ``creature_subtypes(card) & {Elf, Goblin}`` (``strategy_graph`` typal
    concentration) — as an OR over the DSL's case-sensitive ``type:<Subtype>``
    (a ``type_line`` substring, and subtypes are single capitalised tokens). The SAME
    compiled query drives both lake retrieval and the per-candidate match re-check, so
    they can never disagree.
    """
    return ' OR '.join(f'(type:{a})' for a in args)


#: Parameterised attribute families: a base name whose bracketed suffix is a semantic
#: ARGUMENT list (``subtype-concentration[Elf,Goblin]`` → the tribes), not a disposable
#: graph role tag. The registry — not a blanket "strip any trailing bracket" — decides
#: whether a given name's ``[...]`` is an argument or a role tag, so a parameterised
#: family keeps its argument while a role suffix (e.g. ``tutor-land[enabler]``) is
#: dropped. Add a family here to teach retrieval its argument semantics.
_PARAMETERIZED_FAMILIES: dict[str, Callable[[list[str]], str]] = {
    'subtype-concentration': _subtype_concentration_query,
}


def _parse_attribute_name(name: str) -> tuple[str, list[str] | None]:
    """Split a node-attribute NAME into ``(base, args)``.

    A node attribute may carry a trailing ``[...]`` that is EITHER a parameterised
    family's argument list (``subtype-concentration[Elf,Goblin]``) or a graph-side
    role/variant tag (``tutor-land[enabler]``). These share the bracket syntax but mean
    opposite things, so a blanket strip conflates them and drops real arguments; the
    family registry resolves the ambiguity instead. Returns ``args`` as the parsed list
    when ``base`` is a parameterised family, else ``None`` (no bracket, or a role tag
    that is not part of the attribute's identity).
    """
    if name.endswith(']') and '[' in name:
        base, inner = name[: name.index('[')], name[name.index('[') + 1 : -1]
        if base in _PARAMETERIZED_FAMILIES:
            return base, [a.strip() for a in inner.split(',') if a.strip()]
        return base, None
    return name, None


@lru_cache(maxsize=1)
def _card_attribute_queries() -> dict[str, str]:
    """The ``card_attributes`` name→query map (buckets + payoffs) from the catalog.

    This is the design-sanctioned name→query source (``attribute_catalog.json``);
    it is the same map :mod:`~pipeline.transforms.strategy.assets` compiles for
    membership, read here as strings so an attribute name becomes a retrieval query.
    """
    return json.loads(VOCAB_FILE.read_text())['card_attributes']


@lru_cache(maxsize=1)
def _synth_attribute_queries() -> dict[str, str]:
    """The synth (card-field) attribute name→query map.

    Synth attributes carry no otag, so their membership is a card-field query
    (e.g. ``dies-removal``, ``color-fixing``). Reusing the engine's own
    ``(name, query)`` pairs keeps retrieval on the one named rule that defines
    membership rather than a parallel re-encoding.
    """
    pairs = dict(SYNTH_EDHREC_ATTRIBUTES)
    pairs.update(SYNTH_ALWAYS_ATTRIBUTES)
    return {name: query for name, query in pairs.items() if query}


def attribute_query(name: str) -> str:
    """Resolve one node-attribute NAME to its membership query string.

    A parameterised family (e.g. ``subtype-concentration[Elf]``) resolves through its
    argument-aware query FIRST — its bracket is a semantic argument, not a role tag — so
    the tribe is never dropped. Otherwise the base name (its role suffix, if any, having
    been recognised as a tag and removed) resolves in three tiers, in the order the
    engine itself derives a card's attributes
    (:func:`~pipeline.transforms.strategy.derive_card_attributes.derive_card_attributes`
    + :func:`~pipeline.transforms.strategy.distill_strategies.synth_attrs_for`), so
    retrieval cannot drift from membership:

    1. a curated ``card_attributes`` bucket / payoff → its authored OR-of-otag query;
    2. a synth attribute → its card-field query;
    3. otherwise a raw otag slug (an event-anchor / enchantment / EDHREC node) →
       ``otag:<slug>`` exact membership (card otags are ancestor-closed).
    """
    base, args = _parse_attribute_name(name)
    if args is not None:
        return _PARAMETERIZED_FAMILIES[base](args)
    card_attrs = _card_attribute_queries()
    if base in card_attrs:
        return card_attrs[base]
    synth = _synth_attribute_queries()
    if base in synth:
        return synth[base]
    return f'otag:{base}'


def demand_set_query(anchor: Anchor) -> str:
    """Build an anchor's demand-set query — the OR of its attributes' queries.

    The anchor's ``matched_attributes`` are the demand set (the attributes that
    define its side); each resolves through :func:`attribute_query` and the
    distinct sub-queries are OR-combined, each parenthesised so an inner
    ``AND``/``OR``/``NOT`` keeps its precedence. Duplicates are collapsed so the
    same sub-query is not searched twice.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for attr in anchor.matched_attributes:
        sub = attribute_query(attr)
        if sub not in seen:
            seen.add(sub)
            parts.append(f'({sub})')
    return ' OR '.join(parts)


def _matched_subset(card_view: CardView, anchor: Anchor, compiled: dict[str, Predicate]) -> list[str]:
    """The anchor attributes THIS card actually carries (the neutral match fact).

    The card reached the pool via the OR of the whole demand set, so it matches at
    least one attribute; this records exactly which, in the anchor's declared order.
    Sub-query predicates are compiled once per unique query and reused.
    """
    hits: list[str] = []
    for attr in anchor.matched_attributes:
        sub = attribute_query(attr)
        pred = compiled.get(sub)
        if pred is None:
            pred = compile_query(sub)
            compiled[sub] = pred
        if pred(card_view):
            hits.append(attr)
    return hits


def _matches_any(card_view: CardView, attrs: tuple[str, ...], compiled: dict[str, Predicate]) -> bool:
    """Whether the card matches ANY of ``attrs`` (their resolved membership queries).

    Used to decide the connectivity marginal: a candidate joins a NEW far-side
    neighbour only if it also carries an opposite-side attribute. Shares the
    per-unique-query predicate cache with :func:`_matched_subset`.
    """
    for attr in attrs:
        sub = attribute_query(attr)
        pred = compiled.get(sub)
        if pred is None:
            pred = compile_query(sub)
            compiled[sub] = pred
        if pred(card_view):
            return True
    return False


def _fact_snapshot(
    card: dict, card_view: CardView, matched: list[str], anchor: Anchor, compiled: dict[str, Predicate]
) -> FactSnapshot:
    """Neutral per-anchor facts for a candidate joining this anchor's thin side.

    The ``*_after`` deltas are the candidate's ACTUAL marginal, computed by the same
    rules enumeration used (``support`` / ``connectivity`` count distinct NON-LAND
    cards, per the engine's incidence):

    * ``support_after`` rises by one only for a NON-LAND card — a land is excluded
      from the side's support, so a land candidate leaves support unchanged (a flat
      ``+1`` over-counted lands, e.g. a landfall producer).
    * ``connectivity_after`` rises by one only when the candidate ALSO matches an
      opposite-side attribute (and is non-land): it then adds a new distinct neighbour
      across the structural edge — a both-sides card (a relay). A card that joins only
      this side adds no far-side neighbour, so connectivity is unchanged.

    The directed path prepends the candidate to the anchor's edge descriptor to read
    as "this card joins <side>, which connects to <far side>".
    """
    non_land = not is_land(card)
    support_after = anchor.support + (1 if non_land else 0)
    adds_neighbour = non_land and _matches_any(card_view, anchor.opposite_attributes, compiled)
    connectivity_after = anchor.connectivity + (1 if adds_neighbour else 0)
    return FactSnapshot(
        matched_attributes=matched,
        path=['candidate', *anchor.path],
        support_before=anchor.support,
        support_after=support_after,
        connectivity_before=anchor.connectivity,
        connectivity_after=connectivity_after,
    )


def _membership_tags(
    oracle_id: str,
    inventory_lookup: MembershipLookup | None,
    chase_lookup: MembershipLookup | None,
) -> list[Rel]:
    """The card's existing collection relationships, only where a store is injected.

    ``inventory`` (owned) and ``chase`` (already being chased) are tagged solely
    when their lookup is provided; with no store injected the tags stay empty — the
    neutral generation path never reaches into the inventory / chase stores itself.
    """
    tags: list[Rel] = []
    if inventory_lookup is not None and inventory_lookup(oracle_id):
        tags.append('inventory')
    if chase_lookup is not None and chase_lookup(oracle_id):
        tags.append('chase')
    return tags


class _CandidateBuilder:
    """Accumulator for one card's anchor edges across the whole generation pass.

    A card can fill several anchors; it must surface as ONE :class:`Candidate`
    carrying every anchor edge, so edges accrue here keyed by ``oracle_id`` and the
    builder emits the deduped candidate at the end.
    """

    def __init__(self, card_ref: CardRef, membership_tags: list[Rel]) -> None:
        self.card_ref = card_ref
        self.membership_tags = membership_tags
        self.anchors: list[StrategyGraphMembership] = []

    def best_gap(self) -> int:
        """The thinnest hole this card fills — the max ``connectivity_before - support_before``.

        The degraded structural order favours a card that completes the most
        lopsided edge (the biggest structural deficit); a bigger gap is a thinner
        side relative to the rich side it feeds.
        """
        return max(
            (sgm.fact_snapshot.connectivity_before - sgm.fact_snapshot.support_before for sgm in self.anchors),
            default=0,
        )

    def build(self) -> Candidate:
        """Freeze the accrued edges into the typed :class:`Candidate` (rationale stays None)."""
        return Candidate(
            card=self.card_ref,
            anchors=self.anchors,
            membership_tags=self.membership_tags,
            rationale=None,
        )


def _ordering_key(builder: _CandidateBuilder) -> tuple[int, int, str]:
    """Degraded structural sort key (descending by the first two components).

    Transparent and deterministic: more anchors filled first (broader structural
    relevance), then the thinnest hole filled, then card name for a stable tie-break.
    Returned negated on the numeric components so a plain ascending sort ranks the
    strongest candidate first.
    """
    return (-len(builder.anchors), -builder.best_gap(), builder.card_ref.name.lower())


def _selected_modes(include_augment: bool) -> frozenset[AnchorMode]:
    """The anchor saturation modes generation attaches to.

    ``complement`` (the structural holes) is always in scope; ``augment`` (deepening
    a rich side) is opt-in — most callers want the thin sides completed, not the
    strong sides piled higher.
    """
    modes: set[AnchorMode] = {'complement'}
    if include_augment:
        modes.add('augment')
    return frozenset(modes)


def generate_candidates(
    deck: dict,
    *,
    search_client: SearchClient | None = None,
    include_augment: bool = False,
    target_ref: str | None = None,
    inventory_lookup: MembershipLookup | None = None,
    chase_lookup: MembershipLookup | None = None,
    combos: list[dict] | None = None,
    assets: StrategyAssets | None = None,
    cfg: StrategyConfig | None = None,
    saturation: SaturationConfig = DEFAULT_SATURATION,
) -> RecommendationResult:
    """Generate the candidate pool for a deck from its structural anchors.

    Enumerates the deck's anchors, attaches to the ``complement`` holes (plus
    ``augment`` sides when *include_augment*), retrieves each anchor's demand set
    through *search_client*, and emits one :class:`StrategyGraphMembership` per
    (candidate, anchor) that structurally matches, with neutral before/after facts.
    Cards filling several anchors are deduped into one :class:`Candidate`. This is the
    STRUCTURAL pool only: the context gates — color identity, legality, singleton,
    does-not-want, budget — are judgment's (Phase 4a), applied in one ordered pass over
    this pool, NOT pre-filtered here. Pre-filtering identity / in-deck here is what made
    judgment's singleton gate dead and lost the ``rejected`` transparency, so it is
    deliberately left to judgment. No judgment and no scalar score: the result is
    ordered by the transparent ``degraded_structural`` fallback and every ``rationale``
    is ``None`` (Phase 4's to fill).

    Parameters
    ----------
    search_client
        The retrieval seam. Defaults to :func:`make_lake_search_client` (scans the
        local lake); the unit suite injects :func:`make_fixture_search_client`.
    include_augment
        Also attach to ``augment`` anchors (deepen already-rich sides). Off by
        default — complement holes are the structural suggestions.
    target_ref
        The top-level target the request was made against; defaults to the deck id.
    inventory_lookup, chase_lookup
        Optional ``oracle_id -> bool`` membership stores. When injected, a candidate
        is tagged ``inventory`` / ``chase`` accordingly; omitted means no tagging.

    Raises
    ------
    RuntimeError
        On the default (lake) path when the lake is absent — a loud precondition
        failure pointing at ``collection hydrate-lake`` (never a silent empty pool).
    """
    deck_id = deck.get('uuid') or deck.get('name') or '<unknown-deck>'
    result_target = target_ref or str(deck_id)

    used_default = search_client is None
    client = search_client or make_lake_search_client()
    search_memo: dict[str, list[dict]] = {}

    def run_search(query: str) -> list[dict]:
        if query in search_memo:
            return search_memo[query]
        try:
            rows = client(query)
        except FileNotFoundError as exc:
            if used_default:
                raise RuntimeError(
                    'card recommendation requires a hydrated card lake, but none was found. '
                    'Run `collection hydrate-lake` to build it, then retry.'
                ) from exc
            raise
        search_memo[query] = rows
        return rows

    anchors = enumerate_anchors(deck, combos=combos, assets=assets, cfg=cfg, saturation=saturation)
    modes = _selected_modes(include_augment)
    selected = [a for a in anchors if a.mode in modes]

    builders: dict[str, _CandidateBuilder] = {}
    compiled: dict[str, Predicate] = {}  # sub-query -> predicate, compiled once

    for anchor in selected:
        query = demand_set_query(anchor)
        if not query:
            continue
        for card in run_search(query):
            oracle_id = card.get('oracle_id')
            name = card.get('name')
            if not oracle_id or not name:
                continue  # cannot form a CardRef without identity

            view = CardView.from_card(card)
            matched = _matched_subset(view, anchor, compiled)
            if not matched:
                continue  # matched the demand OR but no attribute attributable to THIS anchor

            builder = builders.get(oracle_id)
            if builder is None:
                builder = _CandidateBuilder(
                    card_ref=CardRef(oracle_id=oracle_id, name=name),
                    membership_tags=_membership_tags(oracle_id, inventory_lookup, chase_lookup),
                )
                builders[oracle_id] = builder
            builder.anchors.append(
                StrategyGraphMembership(
                    proposed_membership_ref=f'{result_target}:{oracle_id}',
                    target_kind=anchor.kind,
                    target_ref=anchor.ref,
                    direction=anchor.direction,
                    fact_snapshot=_fact_snapshot(card, view, matched, anchor, compiled),
                )
            )

    ordered = sorted(builders.values(), key=_ordering_key)
    candidates = [b.build() for b in ordered]

    notes = [_DEGRADED_ORDER_NOTE]
    if not selected:
        notes.append('no complement anchors enumerated for this deck; the candidate pool is empty')

    ordering: Ordering = 'degraded_structural'
    return RecommendationResult(
        deck_id=str(deck_id),
        target_ref=result_target,
        ordering=ordering,
        candidates=candidates,
        notes=notes,
    )
