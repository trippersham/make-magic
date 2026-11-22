"""Shared semantic-dump cache — the content-addressed deck-dump store.

This is the "Approach-C shared cache" the deck-comparison and card-recommendation
efforts both read: one durable, materialized ``semantic_dump`` (+ an execution
surface + a reserved strength slot) per deck, so neither effort re-resolves a deck
it has already seen.

It is a deliberate **extension of the engine cache, not a fork** (ADR 0021 /
``engine_cache.py``): it reuses ``engine_cache.input_hash`` as its hashing
primitive, the same two-layer staleness model (a content hash + a version layer),
and the same XDG store directory — only the on-disk file and the cached artifact
differ. The engine cache materializes the expensive *LLM* engine synthesis keyed
by the engine's *output* hash; this cache materializes the deterministic
``resolve()`` + ``semantic_dump`` extraction, so to actually spare the re-resolve
on a hit it keys on a **cheap canonical deck input** (the resolved card pool +
bracket) rather than the engine output, with :data:`DUMP_CACHE_VERSION` as the
second staleness layer (bump it when the extraction or engine semantics change —
exactly the role ``synthesis_version`` plays for the engine cache). Reuse of
``input_hash`` keeps the key scheme shared; the payload is the only difference.

On-disk: JSONL at ``<store_dir>/semantic_dumps.jsonl`` (``store_dir`` defaults to
the engine cache's XDG dir), one record per line, last-write-wins per ``slug``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.transforms.strategy.engine_cache import (
    _DEFAULT_STORE_DIR as _ENGINE_STORE_DIR,
)
from pipeline.transforms.strategy.engine_cache import (
    input_hash,
)

# ---------------------------------------------------------------------------
# Version (the second staleness layer — the ``synthesis_version`` analogue)
# ---------------------------------------------------------------------------

#: Bump when the cached artifact's *extraction* changes in a way that is NOT
#: reflected in the deck-input hash: a new ``semantic_dump`` layer, a new
#: execution field, or an engine-semantics change that moves ``resolve()``'s
#: output. The deck-input hash self-invalidates on a deck edit; this version
#: self-invalidates on an extraction/engine change (the two-layer ADR 0021 model).
#:
#: v2 (P4): the dump gained the layer-② ``engine`` surface (convergent components /
#: ranked units / relay hubs / commander loop / per-event attrs) and the layer-④
#: oracle_id-keyed ``card_attributes``. Bumping invalidates v1 records (which lack both)
#: so they re-resolve; it does NOT change ``resolve()`` / ``semantic_dump`` output.
DUMP_CACHE_VERSION: int = 2

#: Filename within the (shared) store directory. Sits beside ``engines.jsonl``.
_STORE_FILENAME = 'semantic_dumps.jsonl'


# ---------------------------------------------------------------------------
# The cached artifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeckDump:
    """One deck's materialized comparison artifact.

    Attributes:
        semantic_dump: the ``semantic_dump.build_record`` output — the pristine,
            byte-stable shared artifact the card-recommendation effort reads.
        degree_axes: ``facts['degree_axes']`` — the execution degree axes.
        power_profile: ``facts['power_profile']`` — the interaction-efficiency surface.
        curve: the lake-computed nonland mana curve (see ``execution_diff.deck_curve``).
        engine: the normalized ``facts['engines']`` surface for layer ② / ④ —
            ``convergent_event_components`` / ``ranked_units`` / ``relay_hubs`` /
            ``commander_loop`` / ``forward_chains`` / ``events`` (per-event producer /
            consumer attrs). Built by ``deck_semantics.engine_surface_from_facts``; the
            opt-in snapshot path overrides its ``ranked_units`` / ``relay_hubs`` /
            ``commander_loop`` / ``events`` with the injected-edge isolation.
        card_attributes: per-card derived engine attributes, oracle_id-keyed, for layer
            ④'s signature-card join: a list of ``{oracle_id, name, attrs, mv, type_line,
            commander}``. Never joined by name (DFC / ``|MSC|`` safe).
        identity_gap: ``resolve()``'s ``archetype_identity_gap`` scalar (deck-level
            identity-separation signal), or None.
        strength: a reserved slot the card-recommendation effort fills with its
            typed strength record; None until populated (never consumed here).
    """

    semantic_dump: dict[str, Any]
    degree_axes: dict[str, Any]
    power_profile: dict[str, Any]
    curve: dict[str, Any]
    engine: dict[str, Any] = field(default_factory=dict)
    card_attributes: list[dict[str, Any]] = field(default_factory=list)
    identity_gap: float | None = None
    strength: dict[str, Any] | None = None

    def execution_surface(self) -> dict[str, Any]:
        """The ``{degree_axes, power_profile, curve}`` surface ``diff_execution`` reads."""
        return {'degree_axes': self.degree_axes, 'power_profile': self.power_profile, 'curve': self.curve}

    def intent_surface(self) -> dict[str, Any]:
        """The ``{engine, card_attributes}`` surface ``intent_diff.diff_intent`` reads."""
        return {'engine': self.engine, 'card_attributes': self.card_attributes}

    def as_dict(self) -> dict[str, Any]:
        return {
            'semantic_dump': self.semantic_dump,
            'degree_axes': self.degree_axes,
            'power_profile': self.power_profile,
            'curve': self.curve,
            'engine': self.engine,
            'card_attributes': self.card_attributes,
            'identity_gap': self.identity_gap,
            'strength': self.strength,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeckDump:
        return cls(
            semantic_dump=d.get('semantic_dump', {}),
            degree_axes=d.get('degree_axes', {}),
            power_profile=d.get('power_profile', {}),
            curve=d.get('curve', {}),
            engine=d.get('engine', {}),
            card_attributes=d.get('card_attributes', []),
            identity_gap=d.get('identity_gap'),
            strength=d.get('strength'),
        )


# ---------------------------------------------------------------------------
# Staleness key (reuses engine_cache.input_hash — NOT a fork)
# ---------------------------------------------------------------------------


def dump_input_hash(deck: dict[str, Any]) -> str:
    """Stable hash of the cheap canonical deck *input* (card pool + bracket).

    Keys the dump cache on the deck's resolved card pool — ``(name, oracle_id,
    role)`` per card, sorted — plus the bracket, so a hit is answerable WITHOUT
    re-running the engine (that is the point of the cache). Reuses
    ``engine_cache.input_hash`` as the hashing primitive, so the two caches share
    one hashing scheme and differ only in payload. A deck edit moves this hash; an
    engine/extraction change is caught by :data:`DUMP_CACHE_VERSION`.
    """
    payload = {
        'cards': sorted(
            [[c.get('name'), c.get('oracle_id'), c.get('role')] for c in deck.get('cards', [])],
            key=lambda row: [('' if v is None else str(v)) for v in row],
        ),
        'bracket': deck.get('bracket'),
    }
    return input_hash(payload)


@dataclass(frozen=True)
class DumpCacheRecord:
    """A single durable cache record: a deck's :class:`DeckDump` + its staleness key."""

    slug: str
    deck_name: str
    input_hash: str
    dump_version: int
    dump: DeckDump
    cached_at: str = ''

    def to_record(self) -> dict[str, Any]:
        return {
            'slug': self.slug,
            'deck_name': self.deck_name,
            'input_hash': self.input_hash,
            'dump_version': self.dump_version,
            'dump': self.dump.as_dict(),
            'cached_at': self.cached_at,
        }

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> DumpCacheRecord:
        return cls(
            slug=rec['slug'],
            deck_name=rec.get('deck_name', rec['slug']),
            input_hash=rec.get('input_hash', ''),
            dump_version=rec.get('dump_version', 0),
            dump=DeckDump.from_dict(rec.get('dump', {})),
            cached_at=rec.get('cached_at', ''),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass
class SemanticDumpCache:
    """In-memory view of the durable dump cache (keyed by slug)."""

    records: dict[str, DumpCacheRecord] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.records

    def get(self, slug: str) -> DumpCacheRecord | None:
        return self.records.get(slug)

    def is_fresh(self, slug: str, input_hash_value: str, *, dump_version: int = DUMP_CACHE_VERSION) -> bool:
        """True iff a cached record for ``slug`` matches the two-layer staleness key.

        Fresh == same ``input_hash`` (deck unchanged) AND ``dump_version >=`` the
        requested version (extraction unchanged). Either miss is stale.
        """
        rec = self.records.get(slug)
        if rec is None:
            return False
        return rec.input_hash == input_hash_value and rec.dump_version >= dump_version

    def fresh_dump(
        self, slug: str, input_hash_value: str, *, dump_version: int = DUMP_CACHE_VERSION
    ) -> DeckDump | None:
        """The cached :class:`DeckDump` for ``slug`` iff fresh, else None (the hit test)."""
        if not self.is_fresh(slug, input_hash_value, dump_version=dump_version):
            return None
        rec = self.records[slug]
        return rec.dump


def _resolve_store_path(store_dir: Path | str | None) -> Path:
    if store_dir is not None:
        store_dir = Path(store_dir)
    return (store_dir or _ENGINE_STORE_DIR) / _STORE_FILENAME


def empty_cache() -> SemanticDumpCache:
    """A guaranteed-empty cache (the identity element)."""
    return SemanticDumpCache()


def load_cache(store_dir: Path | str | None = None) -> SemanticDumpCache:
    """Load the durable dump cache from disk (empty when the file is missing)."""
    store_path = _resolve_store_path(store_dir)
    if not store_path.exists():
        return SemanticDumpCache()
    text = store_path.read_text(encoding='utf-8').strip()
    if not text:
        return SemanticDumpCache()

    records: dict[str, DumpCacheRecord] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{store_path}:{line_no}: invalid JSON: {exc}') from exc
        record = DumpCacheRecord.from_record(rec)
        records[record.slug] = record  # last write wins
    return SemanticDumpCache(records=records)


def persist(record: DumpCacheRecord, store_dir: Path | str | None = None) -> None:
    """Append a cache record to the durable store (idempotent for a fresh slug).

    If the store already holds a fresh record for ``record.slug`` (same
    ``input_hash`` and a ``dump_version >=`` this one's), the call is a silent
    no-op; otherwise the record is appended and wins on the next load.
    """
    store_path = _resolve_store_path(store_dir)
    if store_path.exists():
        existing = load_cache(store_dir)
        prev = existing.get(record.slug)
        if prev is not None and prev.input_hash == record.input_hash and prev.dump_version >= record.dump_version:
            return
    store_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_record(), sort_keys=True, ensure_ascii=False)
    with store_path.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def make_record(
    slug: str,
    deck_name: str,
    deck: dict[str, Any],
    dump: DeckDump,
    *,
    dump_version: int = DUMP_CACHE_VERSION,
) -> DumpCacheRecord:
    """Assemble a cache record, stamping the staleness key + timestamp."""
    return DumpCacheRecord(
        slug=slug,
        deck_name=deck_name,
        input_hash=dump_input_hash(deck),
        dump_version=dump_version,
        dump=dump,
        cached_at=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    )


__all__ = [
    'DUMP_CACHE_VERSION',
    'DeckDump',
    'DumpCacheRecord',
    'SemanticDumpCache',
    'dump_input_hash',
    'empty_cache',
    'load_cache',
    'make_record',
    'persist',
]
