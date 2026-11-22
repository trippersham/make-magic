"""TDD tests for the Scryfall strength fetch + cache (comparison/strength_fetch.py).

The default suite makes **NO real network call**: the fetch is exercised through an
``httpx.MockTransport`` that returns a canned ``/cards/collection`` body. The live
test (marked ``live``, deselected by default) is the only one that touches Scryfall.

The contract these pin:

* join is by **oracle_id**, never name (the response's ``oracle_id`` is the key);
* a **generic** User-Agent that never carries the user's identity;
* a card found but rank-blind (banned / crossover) is ``edhrec_rank=None`` -> UNKNOWN;
* a card not returned at all is simply absent (also UNKNOWN downstream, not weak);
* batching at 75 identifiers per request;
* the cache round-trips to a documented JSON location and merges on re-populate.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pipeline.transforms.comparison import strength_fetch as sf

# A real-shaped oracle_id triple (values are canonical; the mock keys off them).
RHYSTIC = '53236dd7-845a-444c-96d5-f41ed7325d8f'
REMORA = '8a52f3c0-2552-4425-b2e3-5496eb2232a7'
# A deliberately unknown-to-Scryfall oracle_id (mock omits it from data).
GHOST = '00000000-0000-0000-0000-000000000000'


def _mock_client(captured: list[httpx.Request]) -> httpx.Client:
    """An httpx.Client whose transport answers /cards/collection from a fixed table."""
    table = {
        RHYSTIC: {
            'oracle_id': RHYSTIC,
            'name': 'Rhystic Study',
            'edhrec_rank': 46,
            'game_changer': True,
            'prices': {'usd': '24.50'},
        },
        REMORA: {
            'oracle_id': REMORA,
            'name': 'Mystic Remora',
            'edhrec_rank': 103,
            'game_changer': False,
            'prices': {'usd': '1.20'},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        body = json.loads(request.content)
        data = []
        not_found = []
        for ident in body['identifiers']:
            oid = ident.get('oracle_id')
            if oid in table:
                data.append(table[oid])
            else:
                not_found.append(ident)
        return httpx.Response(200, json={'data': data, 'not_found': not_found})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_joins_by_oracle_id_and_parses_fields() -> None:
    captured: list[httpx.Request] = []
    with _mock_client(captured) as client:
        out = sf.fetch_strength([RHYSTIC, REMORA], client=client)
    assert set(out) == {RHYSTIC, REMORA}
    assert out[RHYSTIC].edhrec_rank == 46
    assert out[RHYSTIC].game_changer is True
    assert out[RHYSTIC].usd == pytest.approx(24.50)
    assert out[REMORA].edhrec_rank == 103
    assert out[REMORA].game_changer is False


def test_fetch_sends_oracle_id_identifiers_and_generic_user_agent() -> None:
    captured: list[httpx.Request] = []
    with _mock_client(captured) as client:
        sf.fetch_strength([RHYSTIC], client=client)
    assert len(captured) == 1
    req = captured[0]
    # Identifiers are keyed by oracle_id (never name).
    body = json.loads(req.content)
    assert body['identifiers'] == [{'oracle_id': RHYSTIC}]
    # The User-Agent is generic — never the user's name / email / machine identity.
    ua = req.headers['user-agent']
    assert 'make-magic' in ua
    assert '@' not in ua  # no email
    assert ua == sf.USER_AGENT


def test_unknown_oracle_id_is_absent_not_fabricated() -> None:
    """A card Scryfall does not return is simply missing (UNKNOWN downstream)."""
    captured: list[httpx.Request] = []
    with _mock_client(captured) as client:
        out = sf.fetch_strength([RHYSTIC, GHOST], client=client)
    assert GHOST not in out
    assert RHYSTIC in out


def test_batching_splits_at_75_identifiers() -> None:
    captured: list[httpx.Request] = []
    ids = [f'{i:08d}-0000-0000-0000-000000000000' for i in range(160)]
    with _mock_client(captured) as client:
        sf.fetch_strength(ids, client=client)
    # 160 ids -> 75 + 75 + 10 = 3 requests.
    assert len(captured) == 3
    assert all(len(json.loads(r.content)['identifiers']) <= 75 for r in captured)


def test_cache_round_trips_and_merges(tmp_path: Path) -> None:
    path = tmp_path / 'strength' / 'scryfall_strength.json'
    first = {RHYSTIC: sf.StrengthData(edhrec_rank=46, game_changer=True, usd=24.5)}
    sf.save_cache(first, path)
    assert sf.load_cache(path) == first
    # Merge a second card without losing the first.
    merged = sf.merge_cache(first, {REMORA: sf.StrengthData(edhrec_rank=103, game_changer=False, usd=1.2)})
    sf.save_cache(merged, path)
    back = sf.load_cache(path)
    assert set(back) == {RHYSTIC, REMORA}
    assert back[RHYSTIC].edhrec_rank == 46


def test_load_cache_missing_file_is_empty(tmp_path: Path) -> None:
    assert sf.load_cache(tmp_path / 'nope.json') == {}


def test_populate_cache_fetches_merges_and_persists(tmp_path: Path) -> None:
    path = tmp_path / 'strength.json'
    captured: list[httpx.Request] = []
    with _mock_client(captured) as client:
        out = sf.populate_cache([RHYSTIC, REMORA], path=path, client=client)
    assert set(out) == {RHYSTIC, REMORA}
    assert sf.load_cache(path) == out


def test_cache_path_honors_data_dir_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MAKE_MAGIC_DATA_DIR', str(tmp_path))
    p = sf.strength_cache_path()
    assert str(p).startswith(str(tmp_path))
    assert p.name.endswith('.json')


def test_cached_nonpositive_rank_degrades_to_unknown(tmp_path: Path) -> None:
    """m2: a cached edhrec_rank <= 0 (or non-int) coerces to None (UNKNOWN), not a crash.

    contracts.StrengthAgnostic constrains edhrec_rank with gt=0, so an un-coerced 0 in
    the cache would raise ValidationError and abort the WHOLE pool's strength(). The
    loader must degrade that one card to UNKNOWN and keep the rest.
    """
    path = tmp_path / 'strength' / 'scryfall_strength.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                REMORA: {'edhrec_rank': 103, 'game_changer': False, 'usd': 1.2},
                RHYSTIC: {'edhrec_rank': 0, 'game_changer': True, 'usd': 24.5},  # bogus rank 0
                GHOST: {'edhrec_rank': -5, 'game_changer': False, 'usd': None},  # bogus negative rank
            }
        )
    )
    cache = sf.load_cache(path)
    assert set(cache) == {REMORA, RHYSTIC, GHOST}
    assert cache[REMORA].edhrec_rank == 103  # valid rank preserved
    assert cache[RHYSTIC].edhrec_rank is None  # 0 -> UNKNOWN
    assert cache[RHYSTIC].game_changer is True  # other fields untouched
    assert cache[GHOST].edhrec_rank is None  # negative -> UNKNOWN


def test_parse_card_coerces_nonpositive_rank() -> None:
    """m2: the Scryfall parser also coerces a <= 0 rank to None (UNKNOWN)."""
    parsed = sf._parse_card({'oracle_id': RHYSTIC, 'edhrec_rank': 0, 'game_changer': True})
    assert parsed is not None
    assert parsed[1].edhrec_rank is None


@pytest.mark.live
def test_live_scryfall_oracle_id_join() -> None:
    """Hits real Scryfall (deselected by default). Proves the oracle_id join is real."""
    out = sf.fetch_strength([RHYSTIC])
    assert RHYSTIC in out
    assert out[RHYSTIC].edhrec_rank is not None
