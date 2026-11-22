"""Unit tests for the shared semantic-dump cache (``comparison.cache``).

Offline + fixture-free: the cache is pure I/O over a tmp store dir, so the tests
lock the key scheme (reuses ``engine_cache.input_hash``, deck-input payload), the
two-layer freshness (input hash + ``DUMP_CACHE_VERSION``), the JSONL round-trip,
and idempotent persist. No lake, no resolve.
"""

from __future__ import annotations

from pipeline.transforms.comparison import DeckDump, SemanticDumpCache
from pipeline.transforms.comparison.cache import (
    DUMP_CACHE_VERSION,
    DumpCacheRecord,
    dump_input_hash,
    load_cache,
    make_record,
    persist,
)
from pipeline.transforms.strategy import engine_cache


def _deck(cards: list[dict], bracket: int = 3) -> dict:
    return {'name': 'Test Deck', 'cards': cards, 'bracket': bracket}


def _dump(tag: str = 'x') -> DeckDump:
    return DeckDump(
        semantic_dump={'attributes': [tag]},
        degree_axes={'ramp': {'n': 24}},
        power_profile={'mean_efficiency': 0.26},
        curve={'mean_cmc': 3.83},
        identity_gap=0.185,
    )


def test_key_reuses_engine_cache_input_hash_not_a_fork() -> None:
    deck = _deck([{'name': 'Sol Ring', 'oracle_id': 'oid1', 'role': None}], bracket=3)
    # The dump key is engine_cache.input_hash over the cheap deck-input payload —
    # same hashing primitive as the engine cache, different payload.
    payload = {'cards': [['Sol Ring', 'oid1', None]], 'bracket': 3}
    assert dump_input_hash(deck) == engine_cache.input_hash(payload)


def test_key_is_order_independent_in_card_list() -> None:
    a = _deck([{'name': 'Sol Ring', 'oracle_id': '1'}, {'name': 'Island', 'oracle_id': '2'}])
    b = _deck([{'name': 'Island', 'oracle_id': '2'}, {'name': 'Sol Ring', 'oracle_id': '1'}])
    assert dump_input_hash(a) == dump_input_hash(b)


def test_key_changes_on_deck_edit() -> None:
    base = _deck([{'name': 'Sol Ring', 'oracle_id': '1'}])
    edited = _deck([{'name': 'Mana Crypt', 'oracle_id': '9'}])
    assert dump_input_hash(base) != dump_input_hash(edited)


def test_roundtrip_through_jsonl(tmp_path) -> None:
    deck = _deck([{'name': 'Sol Ring', 'oracle_id': '1'}])
    record = make_record('test-deck', 'Test Deck', deck, _dump('rt'))
    persist(record, tmp_path)
    loaded = load_cache(tmp_path)
    got = loaded.get('test-deck')
    assert got is not None
    assert got.dump.semantic_dump == {'attributes': ['rt']}
    assert got.dump.degree_axes == {'ramp': {'n': 24}}
    assert got.dump.identity_gap == 0.185
    assert got.dump.strength is None  # reserved slot default


def test_freshness_two_layers(tmp_path) -> None:
    deck = _deck([{'name': 'Sol Ring', 'oracle_id': '1'}])
    record = make_record('d', 'd', deck, _dump())
    persist(record, tmp_path)
    cache = load_cache(tmp_path)
    key = dump_input_hash(deck)
    assert cache.is_fresh('d', key)
    assert cache.fresh_dump('d', key) is not None
    # A different deck input is stale (input-hash miss).
    assert not cache.is_fresh('d', 'deadbeef')
    assert cache.fresh_dump('d', 'deadbeef') is None
    # A newer extraction version is stale (version miss).
    assert not cache.is_fresh('d', key, dump_version=DUMP_CACHE_VERSION + 1)


def test_persist_is_idempotent_for_a_fresh_slug(tmp_path) -> None:
    deck = _deck([{'name': 'Sol Ring', 'oracle_id': '1'}])
    record = make_record('d', 'd', deck, _dump())
    persist(record, tmp_path)
    persist(record, tmp_path)  # second write is a no-op (already fresh)
    store = tmp_path / 'semantic_dumps.jsonl'
    assert len([ln for ln in store.read_text().splitlines() if ln.strip()]) == 1


def test_missing_store_loads_empty(tmp_path) -> None:
    cache = load_cache(tmp_path / 'does-not-exist')
    assert isinstance(cache, SemanticDumpCache)
    assert cache.is_empty


def test_record_to_from_record_is_stable() -> None:
    deck = _deck([{'name': 'Sol Ring', 'oracle_id': '1'}])
    record = make_record('d', 'Name', deck, _dump('z'))
    rebuilt = DumpCacheRecord.from_record(record.to_record())
    assert rebuilt == record
