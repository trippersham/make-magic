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
