"""Judgment layer: enrichment + hard gates + a degraded, transparent rationale (Phase 4a).

Phase 3 produces a structurally-fitting candidate pool with neutral per-anchor
facts and no opinion. This layer turns that pool into *proposals*: it enriches
each candidate with the catalog facts the generation path could not cheaply carry
(color identity, and — where available — commander legality), applies the hard
context gates a suggestion must survive, and attaches a :class:`Rationale` to each
survivor.

Two reasons this is a *separate* layer from generation:

1. **Enrichment is an injected seam, like Phase-3 search.** The default path reads
   ``raw/oracle_cards`` from the local lake (OFFLINE — :func:`make_lake_enricher`);
   the unit suite injects a fixture ``EnrichFn`` so it never touches the lake or the
   network (local-first rule). Generation deliberately left color identity
   un-enforced when the lake projection omitted it (Phase-3 ``ci_skipped`` note);
   enrichment supplies it, so the color-identity gate here is the one that Phase 3
   could only skip — and the stale note is removed once it is enforced.
2. **The gates are context, not structure.** Legality / ban-list, singleton,
   does-not-want, and budget are judgments about whether a structurally-fitting card
   *should be proposed* — they belong to the opinion layer, not the recomputable
   facts (design §"mechanism"). A gated-out card is not silently dropped: it is
   recorded in :attr:`RecommendationResult.rejected` with a ``pass`` verdict and a
   ``context_flags`` entry naming the deciding gate.

Why the verdict is DEGRADED
---------------------------
There is no strength model in v1 (ranking is the sibling deck-comparison effort's
job — design §"Interface"). So the verdict/confidence are derived transparently
from **structural facts only** — the number of distinct anchors a card fills, and
the certainty of the gate outcome — never a learned scalar and never a per-strategy
branch. A hard-gate rejection is a *certain* ``pass`` (``high`` confidence: we know
the card is illegal / out of identity / unwanted). A surviving structural fit is
``consider`` (≥2 anchors) or ``marginal`` (one anchor) at ``low`` confidence, with
``prose`` stating that strength ranking is pending. The layer never emits ``strong``
or a confidence above ``high``: those await the strength model.

Legality source (guarded, backward-compatible)
-----------------------------------------------
``raw/oracle_cards`` (Scryfall bulk, as ingested) carries commander legality in a
flat ``legality_commander`` column, so the default enricher supplies
``legalities.commander`` and the gate is decisive offline. The read is GUARDED: a
lake ingested before that column existed (and the pinned repro fixture) simply lacks
it, so :func:`make_lake_enricher` probes the parquet and omits legalities when the
column is absent — the legality gate then degrades to "unknown ⇒ allow + flag", the
card surviving with a soft ``legality`` flag rather than being dropped or triggering
a network fetch (local-first rule). A caller with its own legality source may still
inject an ``EnrichFn`` that populates ``legalities.commander`` directly.
"""

from __future__ import annotations

from collections.abc import Callable

from pipeline.transforms.recommend.contracts import (
    Candidate,
    Rationale,
    RecommendationResult,
    Verdict,
)
from pipeline.transforms.strategy.corpus import lake_root

#: Candidate enrichment seam: ``oracle_id`` list → ``oracle_id`` → facts dict
#: (``color_identity``, optional ``legalities``, and any extra catalog fields such
#: as ``oracle_text`` / ``type_line`` a gate needs). Injected so the default suite
#: stays offline; the default path (:func:`make_lake_enricher`) reads the local lake.
EnrichFn = Callable[[list[str]], dict[str, dict]]

#: A per-card price lookup for the (optional) budget gate: ``oracle_id`` → price in
#: the caller's currency, or ``None`` when the price is unknown (an unknown price is
#: not a budget violation — the gate cannot decide, so the card is kept).
PriceLookup = Callable[[str], float | None]


def make_lake_enricher() -> EnrichFn:
    """Enricher backed by the local DuckDB lake's ``raw/oracle_cards`` (OFFLINE).

    Reads ``color_identity`` (and ``oracle_text`` / ``type_line`` for the singleton
    exemption) for the requested ``oracle_id``s, and — when the parquet carries the
    ``legality_commander`` column — commander legality, so the legality gate becomes
    decisive offline. No network.

    The legality column is read GUARDED: a freshly-ingested lake has it, but an
    older lake (and the pinned repro fixture) predates it, so the enricher probes the
    parquet's columns and only selects ``legality_commander`` when present. When it is
    absent — or present but null for a card — the returned facts omit ``legalities``
    and the gate degrades to "unknown ⇒ allow + flag" (see module docstring). This
    keeps both a re-hydrated lake and an old/pinned lake working.

    Raises ``FileNotFoundError`` when the lake is absent — the default judgment path
    turns that into a loud ``collection hydrate-lake`` precondition error.
    """

    def enrich(oracle_ids: list[str]) -> dict[str, dict]:
        if not oracle_ids:
            return {}
        oracle_path = lake_root() / 'raw' / 'oracle_cards.parquet'
        if not oracle_path.exists():
            raise FileNotFoundError(f'Lake data not found at {oracle_path}')

        import duckdb

        con = duckdb.connect(':memory:')
        try:
            columns = {
                str(row[0]) for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{oracle_path}')").fetchall()
            }
            has_legality = 'legality_commander' in columns
            legality_col = ', legality_commander' if has_legality else ''
            placeholders = ', '.join('?' for _ in oracle_ids)
            rows = con.execute(
                f'SELECT CAST(oracle_id AS VARCHAR) AS oid, color_identity, oracle_text, type_line{legality_col} '
                f"FROM read_parquet('{oracle_path}') WHERE CAST(oracle_id AS VARCHAR) IN ({placeholders})",
                oracle_ids,
            ).fetchall()
        finally:
            con.close()

        out: dict[str, dict] = {}
        for row in rows:
            oid, color_identity, oracle_text, type_line = row[:4]
            facts: dict = {
                'color_identity': list(color_identity) if color_identity is not None else [],
                'oracle_text': oracle_text or '',
                'type_line': type_line or '',
            }
            # Only surface legalities when the column exists AND the value is known;
            # a null commander status stays unknown so the gate degrades rather than
            # dropping the card on missing data.
            if has_legality and row[4] is not None:
                facts['legalities'] = {'commander': str(row[4])}
            out[str(oid)] = facts
        return out

    return enrich


def _deck_color_identity(deck_cards: list[dict]) -> frozenset[str]:
    """The deck's legal color identity — its commander(s)', excluding the sideboard.

    Mirrors generation's derivation (the two gates must agree): the deck's identity IS
    its commander(s)' color identity, so a candidate is admitted iff its identity is a
    subset of that. Derive from the ``role == 'commander'`` card(s) when present; a bare
    card list with no commander role falls back to the maindeck union. A sideboard /
    considering card must never WIDEN the identity. ``[]`` is a decidable colorless
    identity, not "unknown".
    """
    commanders = [c for c in deck_cards if c.get('role') == 'commander']
    basis = commanders or [c for c in deck_cards if c.get('role') != 'sideboard']
    return frozenset(x for c in basis for x in (c.get('color_identity') or []))


def _commander_legality(facts: dict) -> str | None:
    """The card's ``legalities.commander`` status, or ``None`` when unknown.

    ``None`` means the enricher supplied no legality (the default lake path): the
    gate cannot decide and degrades to allow + flag rather than dropping the card.
    """
    legalities = facts.get('legalities')
    if not isinstance(legalities, dict):
        return None
    status = legalities.get('commander')
    return str(status) if status is not None else None


def _is_explicit_multiples(facts: dict) -> bool:
    """Whether the card is exempt from singleton (any number of copies allowed).

    Two cases carry the exemption: a card whose rules text grants it (Scryfall
    phrasing ``"A deck can have any number of cards named ..."`` — Relentless Rats,
    Persistent Petitioners, Shadowborn Apostle, …) and a basic land. Detected from
    the enriched ``oracle_text`` / ``type_line`` so the heuristic reads the same
    source the rules do, not a hand-maintained name list.
    """
    oracle_text = (facts.get('oracle_text') or '').lower()
    type_line = (facts.get('type_line') or '').lower()
    if 'any number of cards named' in oracle_text:
        return True
    return 'basic' in type_line and 'land' in type_line


def _does_not_want_hit(candidate: Candidate, terms: list[str]) -> str | None:
    """The first does-not-want term the candidate matches, or ``None``.

    A term matches on the card name or on any of the candidate's anchor identities
    (the anchor ``target_ref`` and the attributes it matched) — a stated
    does-not-want is a hard pre-filter on the card *or* on the structural role it
    would fill (refining-decks' DOES-NOT-WANT invariant). Case-insensitive substring.
    """
    haystack = {candidate.card.name.lower()}
    for sgm in candidate.anchors:
        haystack.add(sgm.target_ref.lower())
        haystack.update(a.lower() for a in sgm.fact_snapshot.matched_attributes)
    for term in terms:
        t = term.lower().strip()
        if t and any(t in h for h in haystack):
            return term
    return None


def _degraded_verdict(candidate: Candidate) -> Verdict:
    """The transparent structural verdict for a surviving candidate.

    Derived from anchor breadth alone — more distinct anchors filled is broader
    structural relevance: ``consider`` for ≥2, ``marginal`` for one. Never ``strong``
    (that claims a strength judgment the degraded layer does not have).
    """
    return 'consider' if len(candidate.anchors) >= 2 else 'marginal'


#: Appended to every surviving proposal's prose so a caller never reads the degraded
#: structural verdict as a strength ranking.
_PENDING_STRENGTH = 'Degraded structural verdict — strength ranking is pending the deck-comparison model.'


def _accept(candidate: Candidate, soft_flags: dict[str, str]) -> Candidate:
    """Attach the degraded survivor rationale, preserving the candidate's anchors."""
    verdict = _degraded_verdict(candidate)
    refs = ', '.join(sorted({s.target_ref for s in candidate.anchors})) or '—'
    prose = (
        f'Fills {len(candidate.anchors)} structural anchor(s) ({refs}) and passes all active hard gates '
        f'(legality, color-identity, singleton, does-not-want, budget). {_PENDING_STRENGTH}'
    )
    return candidate.model_copy(
        update={
            'rationale': Rationale(
                verdict=verdict,
                confidence='low',
                swap_out=None,  # swap-pairing is refining-decks' job (design: leave None in v1)
                prose=prose,
                context_flags=soft_flags,
            )
        }
    )


def _reject(candidate: Candidate, gate: str, reason: str) -> Candidate:
    """Attach a certain ``pass`` rationale naming the gate that removed the candidate."""
    return candidate.model_copy(
        update={
            'rationale': Rationale(
                verdict='pass',
                confidence='high',  # a hard-gate rejection is a certain verdict
                swap_out=None,
                prose=f'Not proposed: {reason}.',
                context_flags={gate: reason},
            )
        }
    )


def _judge_one(
    candidate: Candidate,
    facts: dict,
    *,
    deck_ci: frozenset[str],
    deck_oracle_ids: set[str],
    does_not_want: list[str],
    budget: float | None,
    price_lookup: PriceLookup | None,
) -> tuple[bool, Candidate]:
    """Apply the hard gates to one candidate; return ``(accepted, judged_candidate)``.

    Gates run color-identity → legality → singleton → does-not-want → budget; the
    first hard failure decides the rejection (its reason is recorded in
    ``context_flags``). Soft, non-fatal observations (an unknown legality) accrue as
    flags on an accepted candidate.
    """
    oid = candidate.card.oracle_id
    soft_flags: dict[str, str] = {}

    ci = facts.get('color_identity')
    if ci is not None and not (frozenset(ci) <= deck_ci):
        return False, _reject(
            candidate,
            'color-identity',
            f'color identity {sorted(ci)} is not within the deck identity {sorted(deck_ci)}',
        )

    legality = _commander_legality(facts)
    if legality is not None and legality != 'legal':
        return False, _reject(candidate, 'legality', f'not legal in commander (legalities.commander={legality})')
    if legality is None:
        soft_flags['legality'] = 'unknown — commander legality unavailable offline; allowed pending verification'

    if oid in deck_oracle_ids and not _is_explicit_multiples(facts):
        return False, _reject(candidate, 'singleton', 'already in the deck and not an explicit-multiples card')

    hit = _does_not_want_hit(candidate, does_not_want)
    if hit is not None:
        return False, _reject(candidate, 'does-not-want', f'matches the deck does-not-want term {hit!r}')

    if budget is not None and price_lookup is not None:
        price = price_lookup(oid)
        if price is not None and price > budget:
            return False, _reject(candidate, 'budget', f'price {price} exceeds the per-card budget ceiling {budget}')

    return True, _accept(candidate, soft_flags)


def judge(
    result: RecommendationResult,
    deck: dict,
    *,
    enrich: EnrichFn | None = None,
    does_not_want: list[str] | None = None,
    budget: float | None = None,
    price_lookup: PriceLookup | None = None,
) -> RecommendationResult:
    """Enrich, gate, and attach a degraded rationale to a Phase-3 candidate pool.

    Enriches every candidate once through *enrich* (defaulting to the OFFLINE lake
    enricher), enforces the color-identity filter Phase 3 could only skip, and
    applies the hard context gates (legality/ban-list, singleton, does-not-want, and
    — only when both *budget* and *price_lookup* are injected — budget). Survivors
    keep their anchors and gain a transparent :class:`Rationale`; gated-out cards
    move to :attr:`RecommendationResult.rejected` with a ``pass`` verdict naming the
    gate. Ordering is unchanged (still ``degraded_structural``): v1 adds no ranking.

    Parameters
    ----------
    enrich
        The enrichment seam. Defaults to :func:`make_lake_enricher` (reads the local
        lake); the unit suite injects a fixture enricher. Each value is a facts dict
        with at least ``color_identity`` and optionally ``legalities`` /
        ``oracle_text`` / ``type_line``.
    does_not_want
        The deck Strategy's "what doesn't fit" terms; a matching candidate is filtered.
    budget, price_lookup
        The budget gate is active only when BOTH are supplied: a candidate priced
        above *budget* is filtered. An unknown price (``None``) is not a violation.

    Raises
    ------
    RuntimeError
        On the default (lake) enrichment path when the lake is absent — a loud
        precondition failure pointing at ``collection hydrate-lake`` (never a silent
        un-enriched pass). An injected enricher's own errors propagate unwrapped.
    """
    dnw = does_not_want or []
    deck_cards: list[dict] = deck.get('cards') or []
    deck_ci = _deck_color_identity(deck_cards)
    deck_oracle_ids = {c['oracle_id'] for c in deck_cards if c.get('oracle_id')}

    used_default = enrich is None
    enricher = enrich or make_lake_enricher()
    oracle_ids = [c.card.oracle_id for c in result.candidates]
    try:
        facts_map = enricher(oracle_ids) if oracle_ids else {}
    except FileNotFoundError as exc:
        if used_default:
            raise RuntimeError(
                'card recommendation judgment requires a hydrated card lake to enrich candidates, '
                'but none was found. Run `collection hydrate-lake` to build it, then retry.'
            ) from exc
        raise

    accepted: list[Candidate] = []
    rejected: list[Candidate] = []
    any_ci_known = False
    for candidate in result.candidates:
        facts = facts_map.get(candidate.card.oracle_id, {})
        if facts.get('color_identity') is not None:
            any_ci_known = True
        ok, judged = _judge_one(
            candidate,
            facts,
            deck_ci=deck_ci,
            deck_oracle_ids=deck_oracle_ids,
            does_not_want=dnw,
            budget=budget,
            price_lookup=price_lookup,
        )
        (accepted if ok else rejected).append(judged)

    # The Phase-3 ci-skip caveat is stale once enrichment supplies color identity and
    # the gate is enforced here; drop it so the result does not warn about a check it
    # no longer skips.
    notes = list(result.notes)
    if any_ci_known:
        notes = [n for n in notes if not n.startswith('color-identity filter skipped')]

    return RecommendationResult(
        deck_id=result.deck_id,
        target_ref=result.target_ref,
        ordering=result.ordering,  # unchanged — judgment adds gates, not a ranking
        candidates=accepted,
        rejected=rejected,
        notes=notes,
    )
