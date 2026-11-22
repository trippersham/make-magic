"""Resolve bridge — a deck reference → a cached :class:`DeckDump`.

This is the one lake-coupled seam of the comparison package: it turns a resolvable
deck (a ``resolve()``-shaped deck dict) into the materialized comparison artifact,
going through the shared :mod:`.cache` so a deck seen before is answered without a
re-resolve. The pure diff layers (``identity_diff`` / ``execution_diff``) read the
artifact; only this module calls ``resolve()`` / ``semantic_dump`` / the lake.

The lake dependencies are injected as a small :class:`LakeContext` so the engine
work (``resolve``) and the curve's card pricing read one shared lake load, and so
tests can build a context from a pinned lake.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pipeline.transforms.comparison.cache as dump_cache
from pipeline.transforms.comparison.cache import DeckDump
from pipeline.transforms.comparison.execution_diff import deck_curve

#: otags that mark a card as ramp (for the early-ramp curve proxy); the engine's
#: own ramp vocabulary, not a frozen card list.
_RAMP_OTAGS = frozenset({'ramp', 'mana-rock', 'mana-dork', 'land-ramp'})


@dataclass
class LakeContext:
    """The shared lake handles the resolve bridge threads through one deck.

    Bundles the ``corpus.Lake`` (name → otags), the card-meta map (name → cmc /
    type line), the detected-combo list, and the strategy assets so ``resolve``
    and the curve share one load. Build it once per process.
    """

    lake: Any
    meta: dict[str, dict]
    combos: list[dict]
    assets: Any

    @classmethod
    def build(cls, *, cfg: Any | None = None) -> LakeContext:
        """Load the live-lake context (``corpus.Lake`` + meta + combos + assets)."""
        from pipeline.transforms.strategy import corpus, load_assets
        from pipeline.transforms.strategy.combo import load_combos_from_lake

        return cls(
            lake=corpus.Lake(),
            meta=corpus.load_card_meta(),
            combos=load_combos_from_lake(),
            assets=load_assets(cfg),
        )

    # -- lake-backed curve callables (the lake lives behind these) ---------- #

    def _cmc_of(self, name: str) -> float | None:
        """A nonland card's CMC, or None to exclude it (a land).

        Lands (type line contains ``land``) are excluded from the curve. A card
        the lake cannot price — not in meta, or no CMC — counts as a CMC-0 nonland
        (it still lands in the ``0-1`` bucket and the spell count), matching the
        spike's curve so the reported numbers reproduce exactly.
        """
        from pipeline.transforms.strategy import corpus

        m = self.meta.get(corpus._norm(name)) or {}
        if 'land' in (m.get('type_line_full') or '').lower():
            return None
        return float(m.get('cmc') or 0.0)

    def _is_early_ramp(self, name: str) -> bool:
        """True for a ramp-tagged nonland (the turn-two speed proxy reads it with CMC≤2)."""
        _oid, _tl, slugs = self.lake.resolve(name)
        return bool(slugs and (_RAMP_OTAGS & set(slugs)))


def engine_surface_from_facts(
    facts_engines: dict[str, Any],
    *,
    augmented_isolation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize ``facts['engines']`` into the layer-② / ④ engine surface.

    Reads the structure-only engine facts: ``convergent_event_components`` (the SCC
    cycles) stay from the default resolve, while the per-event engine isolation comes
    from ``engine_units`` by default, or from ``augmented_isolation`` (an
    ``identify_engines(...).raw_isolation`` dict carrying the opt-in snapshot's injected
    edges) when the caller resolved with an authored-edge snapshot. The convergent
    components are NOT recomputed under a snapshot (injection augments the elected units /
    producers, not the event-edge SCCs), so they are always the default resolve's.

    Returns ``{convergent_event_components, ranked_units, relay_hubs, commander_loop,
    forward_chains, events}``; ``events`` maps each event to its in-deck producer /
    consumer attrs (+ mass / kind) — the stable anchor layer ④ joins cards against.
    """
    eu = augmented_isolation if augmented_isolation is not None else (facts_engines.get('engine_units') or {})
    events: dict[str, dict[str, Any]] = {}
    for e in eu.get('engines') or []:
        events[e['event']] = {
            'producers': sorted(e.get('producers', [])),
            'consumers': sorted(e.get('consumers', [])),
            'mass': e.get('mass'),
            'kind': e.get('kind'),
        }
    return {
        'convergent_event_components': facts_engines.get('convergent_event_components') or [],
        'ranked_units': eu.get('ranked_units') or [],
        'relay_hubs': eu.get('relay_hubs') or [],
        'commander_loop': eu.get('commander_loop'),
        'forward_chains': [list(fc) for fc in (eu.get('forward_chains') or [])],
        'events': events,
    }


def card_attributes(deck: dict[str, Any], ctx: LakeContext) -> list[dict[str, Any]]:
    """Per-card derived engine attributes, oracle_id-keyed, for layer ④'s join.

    Re-homes the spike's per-card attribute derivation onto the SAME one-named-rule the
    engine uses (``derive_card_attributes`` + ``synth_attrs_for``, ADR 0001), so the
    comparison layer and the engine never read a card's attrs differently. Keyed by
    ``oracle_id`` (never name): a card is ``{oracle_id, name, attrs, mv, type_line,
    commander}``. Does not mutate ``deck``.
    """
    from pipeline.transforms.strategy.derive_card_attributes import derive_card_attributes
    from pipeline.transforms.strategy.distill_strategies import synth_attrs_for

    out: list[dict[str, Any]] = []
    for c in deck.get('cards', []):
        closure = {t.lower() for t in c.get('otags', [])}
        attrs = derive_card_attributes(closure, ctx.assets) | synth_attrs_for(c, ctx.assets.synth_predicates)
        out.append(
            {
                'oracle_id': c.get('oracle_id'),
                'name': c.get('name'),
                'attrs': sorted(attrs),
                'mv': float(c.get('cmc') or 0.0),
                'type_line': c.get('type_line') or '',
                'commander': c.get('role') == 'commander',
            }
        )
    return out


def build_deck_dump(
    deck: dict[str, Any],
    rec: dict[str, Any],
    ctx: LakeContext,
    *,
    engine_report: Any | None = None,
) -> DeckDump:
    """Assemble a :class:`DeckDump` from a deck + its ``resolve()`` record. Pure-ish.

    Reads ``semantic_dump.build_record`` (the shared artifact), the ``degree_axes``
    / ``power_profile`` facts, the identity gap, the lake-computed curve, the normalized
    engine surface, and the oracle_id-keyed card attributes. When ``engine_report`` is
    given (the opt-in snapshot path — an ``identify_engines(...)`` result), its injected
    edges augment the engine surface's elected units; otherwise the default resolve's
    engine facts are used verbatim. Does not mutate ``deck`` or ``rec``.
    """
    from pipeline.transforms.strategy.tests.repro import semantic_dump

    sd = semantic_dump.build_record(deck, rec)
    curve = deck_curve(sd['pool'], cmc_of=ctx._cmc_of, is_early_ramp=ctx._is_early_ramp)
    facts = rec.get('facts', {})
    augmented = engine_report.raw_isolation if engine_report is not None else None
    engine = engine_surface_from_facts(facts.get('engines', {}), augmented_isolation=augmented)
    return DeckDump(
        semantic_dump=sd,
        degree_axes=facts.get('degree_axes', {}),
        power_profile=facts.get('power_profile', {}),
        curve=curve,
        engine=engine,
        card_attributes=card_attributes(deck, ctx),
        identity_gap=rec.get('archetype_identity_gap'),
    )


def resolve_deck_dump(
    deck: dict[str, Any],
    ctx: LakeContext,
    *,
    edges_snapshot: Path | str | None = None,
) -> DeckDump:
    """Resolve a deck and build its :class:`DeckDump` (no cache). Runs the engine.

    ``edges_snapshot`` is the comparison-owned, **opt-in** authored-edge path: a frozen
    machine-vocab snapshot (``machine_vocab.load_frozen_snapshot``) whose edges are
    injected via ``identify_engines`` so the engine surface shows the extra edges (e.g.
    Wakanda's ``artifact-cast-mv4 → counter-placed`` cast lean). ``resolve()`` itself is
    ALWAYS the default (no snapshot) call — the snapshot only augments the read-side
    engine isolation, never the live store and never the default resolve output.
    """
    from pipeline.transforms.strategy.resolve import resolve

    rec = resolve(deck, ctx.combos, assets=ctx.assets)
    engine_report = None
    if edges_snapshot is not None:
        from pipeline.transforms.strategy.engine_report import identify_engines
        from pipeline.transforms.strategy.machine_vocab import load_frozen_snapshot

        store = load_frozen_snapshot(Path(edges_snapshot))
        engine_report = identify_engines(deck, store=store, combos=ctx.combos)
    return build_deck_dump(deck, rec, ctx, engine_report=engine_report)


def cached_deck_dump(
    deck: dict[str, Any],
    ctx: LakeContext,
    *,
    slug: str | None = None,
    store_dir: Path | str | None = None,
    edges_snapshot: Path | str | None = None,
) -> tuple[DeckDump, bool]:
    """Return ``(dump, cache_hit)`` for a deck, resolving + persisting only on a miss.

    A fresh cached dump (same deck input + :data:`~.cache.DUMP_CACHE_VERSION`) is
    returned WITHOUT a re-resolve (``cache_hit=True``). On a miss the deck is
    resolved, its dump built + persisted, and returned (``cache_hit=False``).

    The opt-in ``edges_snapshot`` path **bypasses the shared cache entirely**: it always
    resolves fresh and never reads or writes the durable dump store, so loading a snapshot
    can neither be served a non-snapshot cache hit nor pollute the default cache (keeping
    the default path byte-identical and the live store untouched). Its ``cache_hit`` is
    always ``False``.
    """
    if edges_snapshot is not None:
        return resolve_deck_dump(deck, ctx, edges_snapshot=edges_snapshot), False
    deck_slug = slug or str(deck.get('name') or 'deck')
    key = dump_cache.dump_input_hash(deck)
    cache = dump_cache.load_cache(store_dir)
    hit = cache.fresh_dump(deck_slug, key)
    if hit is not None:
        return hit, True
    dump = resolve_deck_dump(deck, ctx)
    record = dump_cache.make_record(deck_slug, deck_slug, deck, dump)
    dump_cache.persist(record, store_dir)
    return dump, False


# --------------------------------------------------------------------------- #
# Deck-reference → resolve-input deck dict (the CLI's deck resolution).
# --------------------------------------------------------------------------- #


def deck_from_cards(
    name: str,
    entries: list[tuple[str, bool]],
    ctx: LakeContext,
    *,
    bracket: int = 3,
    bundle: str = 'owned',
    source: str = 'owned',
) -> dict[str, Any]:
    """Build a ``resolve()``-shaped deck dict from ``(card_name, is_commander)`` entries.

    Resolves each card's otags / type line through the lake and attaches the
    engine-required meta (cmc / oracle text), the same shape the gauntlet builder
    produces. Unresolved names (unreleased / spoiler cards) are recorded, not dropped.
    """
    from pipeline.transforms.strategy import corpus

    cards: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for cn, is_cmd in entries:
        oid, tl, slugs = ctx.lake.resolve(cn)
        if oid is None:
            unresolved.append(cn)
            tl, slugs = '', set()
        cards.append(
            {
                'name': cn,
                'type_line': tl or '',
                'otags': sorted(slugs) if slugs else [],
                'role': 'commander' if is_cmd else None,
            }
        )
    deck = {
        'name': name,
        'cards': cards,
        'bracket': bracket,
        'bundle': bundle,
        'commander_hint': None,
        'unresolved': unresolved,
        'source': source,
    }
    return corpus.attach_meta(deck, ctx.meta)


def _gauntlet_match(ref: str, ctx: LakeContext) -> dict[str, Any] | None:
    """Resolve a gauntlet/precon deck by ``.dck`` stem (or ``bundle::stem``), else None."""
    from pipeline.transforms.strategy import corpus

    want = ref.strip().lower()
    bundle_filter: str | None = None
    if '::' in want:
        bundle_filter, want = (part.strip() for part in want.split('::', 1))
    for info in corpus.gauntlet_decks():
        if bundle_filter is not None and info.get('bundle') != bundle_filter:
            continue
        stem = info['path'].stem.lower()
        if stem == want or stem.replace('_', ' ') == want:
            deck = corpus.build_deck_from_dck(info, ctx.lake)
            return corpus.attach_meta(deck, ctx.meta)
    return None


def resolve_reference(ref: str, ctx: LakeContext) -> dict[str, Any]:
    """Resolve a deck reference to a ``resolve()``-shaped deck dict.

    Tries, in order: a gauntlet/precon ``.dck`` (by stem or ``bundle::stem``), then
    the local decks store (by name, like ``factsheet``/``crispi``). Raises
    :class:`~pipeline.collection.CollectionError` when neither resolves.
    """
    gauntlet = _gauntlet_match(ref, ctx)
    if gauntlet is not None:
        return gauntlet

    from pipeline.collection import CollectionError, get_store
    from pipeline.decks.access import deck_access

    try:
        model = deck_access(get_store()).read_deck(ref)
    except Exception as exc:  # surface a single clean reference error, whatever the store raised.
        raise CollectionError(
            f'compare: could not resolve deck reference {ref!r} — not a gauntlet/precon '
            'deck (by .dck stem or bundle::stem) nor a deck in the local store.'
        ) from exc
    entries: list[tuple[str, bool]] = []
    for card in model.cards:
        is_cmd = (card.role or '').lower() == 'commander'
        entries.extend((card.name, is_cmd) for _ in range(max(1, card.quantity)))
    bracket = getattr(model, 'bracket', None) or 3
    return deck_from_cards(model.name, entries, ctx, bracket=bracket, bundle='local', source='local')
