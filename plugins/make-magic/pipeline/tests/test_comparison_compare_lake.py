"""Lake-backed reproduction + end-to-end for ``compare`` (layers ①③ + cache).

Gated on a hydrated primary lake: the default offline suite has no lake, so this
module SKIPS there (keeping ``pytest`` green without a lake) and runs locally /
wherever ``MAKE_MAGIC_DATA_DIR`` points at a real lake. It proves, through the
SHIPPED pipeline (``cached_deck_dump`` → ``diff_identity`` / ``diff_execution``):

* the spike's World Reclaimer vs World Shaper execution numbers reproduce exactly
  on the current base (ramp 24 vs 22, mean CMC 3.83 vs 3.52, same identity);
* a second ``cached_deck_dump`` of the same deck is a cache HIT (no re-resolve);
* the CLI's deck-reference resolution resolves a live gauntlet precon.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.collection.resolver import lake_status

pytestmark = pytest.mark.skipif(
    lake_status() != 'ready',
    reason='needs a hydrated primary lake (set MAKE_MAGIC_DATA_DIR to a ready lake); offline suite skips',
)

_FIXTURE = Path(__file__).parent / 'fixtures' / 'comparison' / 'wr_vs_worldshaper.json'


@pytest.fixture(scope='module')
def ctx():
    from pipeline.transforms.comparison import LakeContext

    return LakeContext.build()


@pytest.fixture(scope='module')
def fixture() -> dict:
    return json.loads(_FIXTURE.read_text())


def _entries(spec: dict) -> list[tuple[str, bool]]:
    return [(name, bool(is_cmd)) for name, is_cmd in spec['entries']]


def test_wr_vs_worldshaper_execution_reproduces_the_spike(ctx, fixture, tmp_path) -> None:
    from pipeline.transforms.comparison import cached_deck_dump, diff_execution, diff_identity
    from pipeline.transforms.comparison.deck_semantics import deck_from_cards

    wr_spec, ws_spec = fixture['world_reclaimer'], fixture['world_shaper']
    wr = deck_from_cards(wr_spec['name'], _entries(wr_spec), ctx, bracket=wr_spec['bracket'])
    ws = deck_from_cards(ws_spec['name'], _entries(ws_spec), ctx, bracket=ws_spec['bracket'], bundle='precons')

    dump_wr, hit_wr = cached_deck_dump(wr, ctx, slug='wr', store_dir=tmp_path)
    dump_ws, hit_ws = cached_deck_dump(ws, ctx, slug='ws', store_dir=tmp_path)
    assert hit_wr is False and hit_ws is False  # first resolve = miss

    exp = fixture['expected']
    assert dump_wr.degree_axes['ramp']['n'] == exp['world_reclaimer']['ramp_n']  # 24
    assert dump_ws.degree_axes['ramp']['n'] == exp['world_shaper']['ramp_n']  # 22
    assert dump_wr.curve['mean_cmc'] == exp['world_reclaimer']['mean_cmc']  # 3.83
    assert dump_ws.curve['mean_cmc'] == exp['world_shaper']['mean_cmc']  # 3.52

    id_diff = diff_identity(dump_wr.semantic_dump, dump_ws.semantic_dump)
    assert id_diff.a_identity == exp['world_reclaimer']['identity']
    assert id_diff.b_identity == exp['world_shaper']['identity']
    assert id_diff.added_in_b == [] and id_diff.dropped_in_b == []  # same archetype identity

    exec_diff = diff_execution(dump_wr.execution_surface(), dump_ws.execution_surface())
    ramp = next(ad for ad in exec_diff.axis_deltas if ad.axis == 'ramp')
    assert (ramp.a_n, ramp.b_n, ramp.delta) == (24, 22, -2)  # the spike's ramp delta


def test_second_cached_deck_dump_is_a_cache_hit(ctx, fixture, tmp_path) -> None:
    from pipeline.transforms.comparison import cached_deck_dump
    from pipeline.transforms.comparison.deck_semantics import deck_from_cards

    wr_spec = fixture['world_reclaimer']
    wr = deck_from_cards(wr_spec['name'], _entries(wr_spec), ctx, bracket=wr_spec['bracket'])

    first, hit_first = cached_deck_dump(wr, ctx, slug='wr', store_dir=tmp_path)
    second, hit_second = cached_deck_dump(wr, ctx, slug='wr', store_dir=tmp_path)
    assert hit_first is False  # miss populates the cache
    assert hit_second is True  # second call served from cache (no re-resolve)
    assert first.semantic_dump == second.semantic_dump
    assert first.degree_axes == second.degree_axes


def test_reference_resolution_handles_a_live_precon(ctx, tmp_path) -> None:
    from pipeline.transforms.comparison import cached_deck_dump
    from pipeline.transforms.comparison.deck_semantics import resolve_reference

    deck = resolve_reference('WakandaForever_2026', ctx)
    assert deck['cards']
    dump, _hit = cached_deck_dump(deck, ctx, slug='wakanda-precon-test', store_dir=tmp_path)
    # The Wakanda precon reads as an artifacts / combat / counters deck (spike Pair 1).
    assert dump.degree_axes.get('ramp', {}).get('n') is not None
    assert dump.semantic_dump['archetype_identity']['archetype_identity']


# --------------------------------------------------------------------------- #
# Layer ② — World Reclaimer land-loop reproduces wr-framework-admission.md.
# --------------------------------------------------------------------------- #

_SNAPSHOT = (
    Path(__file__).parent.parent
    / 'pipeline'
    / 'transforms'
    / 'strategy'
    / 'tests'
    / 'fixtures'
    / 'machine_vocab_tchalla_frozen.json'
)


def test_wr_layer2_reproduces_the_framework_admission(ctx, fixture, tmp_path) -> None:
    """The WR land-loop surfaces as a convergent SCC + closing commander loop (layer ②).

    Reproduces ``wr-framework-admission.md``: a 42-node / 6-event convergent component
    (weight 388), ``commander_loop.closes`` with ``reaches_back_to`` the recursion/
    reanimate/crucible hubs, relay hubs ``[sac, recursion-land, reanimate-land,
    crucible-of-worlds]``, and every ranked unit ``recursive:false`` — a value grind that
    closes, NOT a certified infinite (#90 is not a gate).
    """
    from pipeline.transforms.comparison import cached_deck_dump, classify_regime, diff_engine
    from pipeline.transforms.comparison.deck_semantics import deck_from_cards
    from pipeline.transforms.comparison.engine_diff import REGIME_LOOP, heaviest_cycle

    wr_spec, ws_spec = fixture['world_reclaimer'], fixture['world_shaper']
    wr = deck_from_cards(wr_spec['name'], _entries(wr_spec), ctx, bracket=wr_spec['bracket'])
    ws = deck_from_cards(ws_spec['name'], _entries(ws_spec), ctx, bracket=ws_spec['bracket'], bundle='precons')
    dump_wr, _ = cached_deck_dump(wr, ctx, slug='wr-eng', store_dir=tmp_path)
    dump_ws, _ = cached_deck_dump(ws, ctx, slug='ws-eng', store_dir=tmp_path)

    # The convergent cycle: 42 nodes / 6 events / weight 388 over the land-sac loop events.
    cyc = heaviest_cycle(dump_wr.engine)
    assert cyc is not None
    assert cyc['size'] == 42 and cyc['n_events'] == 6 and cyc['weight'] == 388.0
    assert cyc['events'] == [
        'card-enters-graveyard',
        'creature-attacks',
        'land-enters-battlefield',
        'permanent-dies',
        'permanent-sacrificed',
        'token-created',
    ]
    assert classify_regime(dump_wr.engine) == REGIME_LOOP

    # Commander loop closes and reaches back to the recursion/reanimate/crucible hubs.
    cl = dump_wr.engine['commander_loop']
    assert cl['closes'] is True
    assert cl['reaches_back_to'] == ['recursion-land', 'reanimate-land', 'crucible-of-worlds']
    assert dump_wr.engine['relay_hubs'] == ['sac', 'recursion-land', 'reanimate-land', 'crucible-of-worlds']

    # The #90-honest read: a grind that closes, never a certified infinite.
    assert all(not u['recursive'] for u in dump_wr.engine['ranked_units'])

    d = diff_engine(dump_wr.engine, dump_ws.engine, a_name='World Reclaimer', b_name='World Shaper')
    assert d.regime_a == REGIME_LOOP  # WR is a grind engine
    assert d.a_certified_infinite is False
    assert 'value grind that closes, not a certified infinite' in d.reading
    assert 'land-enters-battlefield' in d.a_cycle_events


# --------------------------------------------------------------------------- #
# Opt-in snapshot — Wakanda ≥4-MV-artifact-cast lean surfaces; default byte-identical.
# --------------------------------------------------------------------------- #


def test_opt_in_snapshot_surfaces_the_cast_lean_default_is_byte_identical(ctx, tmp_path) -> None:
    """The frozen T'Challa snapshot adds ``artifact-cast-mv4`` as a ``counter-placed``
    producer (layer ②); WITHOUT it, the engine surface is byte-identical to the default.
    """
    from pipeline.transforms.comparison.deck_semantics import resolve_deck_dump, resolve_reference

    deck = resolve_reference('WakandaForever_2026', ctx)

    default = resolve_deck_dump(deck, ctx)
    augmented = resolve_deck_dump(deck, ctx, edges_snapshot=_SNAPSHOT)

    def _producers(engine: dict, event: str) -> list[str]:
        return engine['events'].get(event, {}).get('producers', [])

    # WITHOUT the snapshot: default counter-placed producers (no injected edge).
    assert 'artifact-cast-mv4' not in _producers(default.engine, 'counter-placed')
    # WITH the snapshot: the authored T'Challa edge surfaces as a counter-placed producer.
    assert 'artifact-cast-mv4' in _producers(augmented.engine, 'counter-placed')

    # The default path is byte-identical to a plain resolve of the same deck (the opt-in
    # snapshot is the ONLY thing that moves the engine surface).
    baseline = resolve_deck_dump(deck, ctx)
    assert default.engine == baseline.engine
    assert default.semantic_dump == baseline.semantic_dump
    assert default.card_attributes == baseline.card_attributes


def test_opt_in_snapshot_never_touches_the_shared_cache(ctx, tmp_path) -> None:
    """``edges_snapshot`` bypasses the dump cache: it reports a miss and writes nothing."""
    from pipeline.transforms.comparison import cached_deck_dump
    from pipeline.transforms.comparison.deck_semantics import resolve_reference

    deck = resolve_reference('WakandaForever_2026', ctx)
    _dump, hit = cached_deck_dump(deck, ctx, slug='wakanda-snap', store_dir=tmp_path, edges_snapshot=_SNAPSHOT)
    assert hit is False  # opt-in never served a hit
    # Nothing was persisted for the snapshot slug (the live/dump store is untouched).
    store = tmp_path / 'semantic_dumps.jsonl'
    if store.exists():
        import json as _json

        slugs = {_json.loads(ln)['slug'] for ln in store.read_text().splitlines() if ln.strip()}
        assert 'wakanda-snap' not in slugs
