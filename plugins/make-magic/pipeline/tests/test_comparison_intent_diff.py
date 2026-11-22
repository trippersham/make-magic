"""Unit tests for layer ④ surgical intent diff (``comparison.intent_diff``).

Offline + fixture-free: the diff reads hand-built intent surfaces (``{engine,
card_attributes}``), so no lake / resolve enters the test. Locks the event-anchored
discovery, the oracle_id join (never name), the participation value, the strength
ranking AID (vs participation-order discovery), the ≥4-MV bench lens, and the
divergence threshold.
"""

from __future__ import annotations

from pipeline.transforms.comparison import IntentDiff, diff_intent, event_investment
from pipeline.transforms.comparison.intent_diff import (
    _DIVERGENCE_THRESHOLD,
)
from pipeline.transforms.comparison.intent_diff import (
    diff_intent as diff_intent_direct,
)


def _engine(events: dict, *, relay: list[str] | None = None, recursive: list[str] | None = None) -> dict:
    return {
        'events': events,
        'relay_hubs': relay or [],
        'ranked_units': [{'event': e, 'recursive': True} for e in (recursive or [])],
    }


def _card(oid: str, name: str, attrs: list[str], mv: float, *, commander: bool = False) -> dict:
    return {'oracle_id': oid, 'name': name, 'attrs': attrs, 'mv': mv, 'commander': commander}


def test_public_home_re_exports_the_core() -> None:
    assert diff_intent is diff_intent_direct


def test_event_investment_counts_participation_and_cards() -> None:
    engine = _engine({'counter-placed': {'producers': ['counters'], 'consumers': ['counters-matter']}})
    cards = [
        _card('o1', 'Giver', ['counters'], 3.0),  # producer: 1
        _card('o2', 'Payoff', ['counters-matter'], 4.0),  # consumer: 1
        _card('o3', 'Both', ['counters', 'counters-matter'], 2.0),  # produce+consume: 1+1+2 loop = 4
        _card('o4', 'Unrelated', ['ramp'], 1.0),  # no participation
    ]
    prof = event_investment(engine, cards)
    assert prof['counter-placed']['n_cards'] == 3  # o4 excluded
    assert prof['counter-placed']['total'] == 1 + 1 + 4  # 6


def test_signature_cards_join_by_oracle_id_not_name() -> None:
    # Two cards share a display name but differ by oracle_id (a |MSC| / DFC hazard); the join
    # is per-oracle_id so both are discovered independently, never collapsed by name.
    engine = _engine({'spell-cast': {'producers': ['artifact-cast-mv4'], 'consumers': []}})
    a_cards = [
        _card('oid-A', "T'Challa|MSC|1", ['artifact-cast-mv4'], 4.0, commander=True),
        _card('oid-B', "T'Challa|MSC|1", ['artifact-cast-mv4'], 2.0),
    ]
    d = diff_intent(
        {'engine': engine, 'card_attributes': a_cards},
        {'engine': _engine({'spell-cast': {'producers': [], 'consumers': []}}), 'card_attributes': []},
    )
    row = next(e for e in d.events if e.event == 'spell-cast')
    assert {ic.oracle_id for ic in row.a_signature} == {'oid-A', 'oid-B'}  # both kept, name-collision safe


def test_ranker_is_the_aid_discovery_is_participation() -> None:
    # WITHOUT a ranker, signature cards order by participation then MV (discovery order).
    # WITH a ranker (the Phase-2 strength/value-density AID), order follows the scalar — the
    # ranker never changes WHICH cards are discovered, only their order.
    engine = _engine({'draw-a-card': {'producers': ['draw-engine'], 'consumers': []}})
    cards = [
        _card('weak', 'Weak Draw', ['draw-engine'], 5.0),
        _card('strong', 'Rhystic Study', ['draw-engine'], 3.0),
    ]
    surface = {'engine': engine, 'card_attributes': cards}
    empty = {'engine': _engine({'draw-a-card': {'producers': [], 'consumers': []}}), 'card_attributes': []}

    unranked = diff_intent(surface, empty)
    row = next(e for e in unranked.events if e.event == 'draw-a-card')
    # same participation (both just produce) -> higher MV first in discovery order
    assert [ic.name for ic in row.a_signature] == ['Weak Draw', 'Rhystic Study']
    assert all(ic.strength is None for ic in row.a_signature)

    ranked = diff_intent(surface, empty, a_ranker=lambda rec: 100.0 if rec['name'] == 'Rhystic Study' else 1.0)
    rrow = next(e for e in ranked.events if e.event == 'draw-a-card')
    assert [ic.name for ic in rrow.a_signature] == ['Rhystic Study', 'Weak Draw']  # ranker reorders
    assert {ic.oracle_id for ic in rrow.a_signature} == {'weak', 'strong'}  # same discovery set


def test_high_mv_bench_lens() -> None:
    engine = _engine({'artifact-enters': {'producers': ['artifacts'], 'consumers': []}})
    cards = [
        _card('o1', 'Krang', ['artifacts'], 9.0),
        _card('o2', 'Cheap Rock', ['artifacts'], 1.0),
        _card('o3', 'Skullspore', ['artifacts'], 8.0),
    ]
    d = diff_intent({'engine': engine, 'card_attributes': cards}, {'engine': _engine({}), 'card_attributes': []})
    row = next(e for e in d.events if e.event == 'artifact-enters')
    assert [ic.name for ic in row.a_high_mv] == ['Krang', 'Skullspore']  # >=4 MV, heaviest first


def test_divergence_threshold_and_leader() -> None:
    # A invests heavily in an event; B not at all. Delta clears the threshold -> divergent, A leads.
    engine_a = _engine({'sac': {'producers': ['sacrifice-outlet'], 'consumers': ['death-trigger']}})
    a_cards = [_card(f'o{i}', f'Sac{i}', ['sacrifice-outlet', 'death-trigger'], 3.0) for i in range(3)]
    d = diff_intent(
        {'engine': engine_a, 'card_attributes': a_cards},
        {'engine': _engine({'sac': {'producers': [], 'consumers': []}}), 'card_attributes': []},
        a_name='Grind',
        b_name='Pile',
    )
    assert isinstance(d, IntentDiff)
    row = next(e for e in d.events if e.event == 'sac')
    assert row.a_total >= _DIVERGENCE_THRESHOLD
    assert row.b_total == 0
    assert row.leader == 'A'
    assert row.divergent is True
    assert row in d.divergent_events
    assert 'Grind' in d.reading and 'sac' in d.reading


def test_recursive_flag_marks_loop_events() -> None:
    engine = _engine(
        {'land-enters-battlefield': {'producers': ['recursion-land'], 'consumers': ['landfall']}},
        recursive=['land-enters-battlefield'],
    )
    cards = [_card('o1', 'Crucible', ['recursion-land', 'landfall'], 3.0)]
    d = diff_intent({'engine': engine, 'card_attributes': cards}, {'engine': _engine({}), 'card_attributes': []})
    row = next(e for e in d.events if e.event == 'land-enters-battlefield')
    assert row.recursive is True


def test_empty_surfaces_are_safe() -> None:
    d = diff_intent({'engine': {}, 'card_attributes': []}, {'engine': {}, 'card_attributes': []})
    assert d.events == []
    assert d.divergent_events == []
    assert 'invest alike' in d.reading
