"""CLI tests for the ``collection recommend`` verb (READ-ONLY, Phase 4b serving).

The verb composes the recommendation primitive behind a read-only deck load. These
tests lock the CLI layer — argument parsing, the read-only deck-load path, the
JSON vs explain rendering, and the MCP ``--schema`` surface — OFFLINE: the deck
load and the ``recommend()`` composition are monkeypatched, so no lake, store, or
network is touched (the faithful generate→judge composition is proven in
``pipeline/transforms/recommend/tests/test_serve.py``). The verb must never write a
store; nothing here provides a writable backend, so a write would fail loudly.
"""

from __future__ import annotations

import json

import pytest

from pipeline.collection import run as cli
from pipeline.contracts import Deck, DeckCard
from pipeline.transforms.recommend import serve as serve_mod
from pipeline.transforms.recommend.contracts import (
    Candidate,
    CardRef,
    FactSnapshot,
    Rationale,
    RecommendationResult,
    StrategyGraphMembership,
)


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    """Invoke the dispatcher as the CLI would (``collection <verb> ...``)."""
    monkeypatch.setattr('sys.argv', ['collection', *argv])
    cli.main()


def _fixture_deck() -> Deck:
    """A tiny read-only deck the monkeypatched loader returns (no store/lake)."""
    return Deck(name='World Reclaimer', format='Commander', cards=[DeckCard(name='Szarel', role='commander')])


def _anchor(ref: str = 'land-enters-battlefield') -> StrategyGraphMembership:
    return StrategyGraphMembership(
        proposed_membership_ref=f't:{ref}',
        target_kind='event_side',
        target_ref=ref,
        direction='producer',
        fact_snapshot=FactSnapshot(matched_attributes=['recursion-land'], path=['candidate', f'{ref}/producer']),
    )


def _accepted() -> Candidate:
    return Candidate(
        card=CardRef(oracle_id='oid-ok', name='Fixture Recursion'),
        anchors=[_anchor()],
        membership_tags=['inventory'],
        rationale=Rationale(verdict='marginal', confidence='low', prose='Fills 1 structural anchor.'),
    )


def _rejected_banned() -> Candidate:
    return Candidate(
        card=CardRef(oracle_id='oid-banned', name='Black Lotus'),
        anchors=[_anchor()],
        rationale=Rationale(
            verdict='pass',
            confidence='high',
            prose='Not proposed: not legal in commander.',
            context_flags={'legality': 'not legal in commander (legalities.commander=banned)'},
        ),
    )


def _canned_result() -> RecommendationResult:
    return RecommendationResult(
        deck_id='World Reclaimer',
        target_ref='World Reclaimer',
        ordering='degraded_structural',
        candidates=[_accepted()],
        rejected=[_rejected_banned()],
        notes=['ordering is degraded_structural'],
    )


@pytest.fixture()
def patched(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch the deck load + the ``recommend`` composition; record their call args."""
    calls: dict = {}

    def _fake_recommend_deck(name, id_prefix):
        calls['deck_load'] = (name, id_prefix)
        return _fixture_deck()

    def _fake_recommend(deck, **kwargs):
        calls['deck_dict'] = deck
        calls['kwargs'] = kwargs
        return _canned_result()

    monkeypatch.setattr(cli, '_recommend_deck', _fake_recommend_deck)
    monkeypatch.setattr(serve_mod, 'recommend', _fake_recommend)
    return calls


# --------------------------------------------------------------------------- #
# Read-only deck load + argument passthrough.
# --------------------------------------------------------------------------- #


def test_recommend_loads_deck_read_only_and_passes_args(
    monkeypatch: pytest.MonkeyPatch, patched: dict, capsys: pytest.CaptureFixture
) -> None:
    _run(monkeypatch, 'recommend', 'World Reclaimer', '--target', 'ramp', '--augment', '--budget', '7.5', '--json')
    assert patched['deck_load'] == ('World Reclaimer', None)
    # The deck is handed to recommend as a plain dict (the model_dump), never a writer.
    assert isinstance(patched['deck_dict'], dict)
    assert patched['deck_dict']['name'] == 'World Reclaimer'
    assert patched['kwargs'] == {'target': 'ramp', 'include_augment': True, 'budget': 7.5}
    capsys.readouterr()  # drain


def test_recommend_requires_a_deck(monkeypatch: pytest.MonkeyPatch, patched: dict) -> None:
    # No name and no --id is a clean one-line error (exit 1), not a traceback.
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, 'recommend')
    assert exc.value.code == 1


# --------------------------------------------------------------------------- #
# JSON rendering — the typed RecommendationResult round-trips.
# --------------------------------------------------------------------------- #


def test_recommend_json_emits_typed_result(
    monkeypatch: pytest.MonkeyPatch, patched: dict, capsys: pytest.CaptureFixture
) -> None:
    _run(monkeypatch, 'recommend', 'World Reclaimer', '--json')
    out = capsys.readouterr().out
    parsed = RecommendationResult.model_validate_json(out)
    assert parsed.ordering == 'degraded_structural'
    assert [c.card.name for c in parsed.candidates] == ['Fixture Recursion']
    assert [c.card.name for c in parsed.rejected] == ['Black Lotus']


# --------------------------------------------------------------------------- #
# Explain rendering — candidates grouped with anchors; rejected shown with gate.
# --------------------------------------------------------------------------- #


def test_recommend_explain_groups_candidates_and_rejected(
    monkeypatch: pytest.MonkeyPatch, patched: dict, capsys: pytest.CaptureFixture
) -> None:
    _run(monkeypatch, 'recommend', 'World Reclaimer')  # default = explain
    out = capsys.readouterr().out

    # The accepted candidate shows with its verdict, anchor, and membership tag.
    assert 'Fixture Recursion' in out
    assert 'marginal/low' in out
    assert 'event_side:land-enters-battlefield' in out
    assert 'inventory' in out

    # The banned card appears ONLY under rejected (with its gate), never as a candidate.
    assert 'rejected (hard gate)' in out
    cand_section, rej_section = out.split('rejected (hard gate)', 1)
    assert 'Black Lotus' not in cand_section
    assert 'Black Lotus' in rej_section
    assert 'legality' in rej_section


# --------------------------------------------------------------------------- #
# MCP schema — --schema prints the contract and never reads a deck.
# --------------------------------------------------------------------------- #


def test_recommend_schema_prints_contract_without_deck_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def _must_not_load(*_a, **_k):
        raise AssertionError('--schema must not read a deck')

    monkeypatch.setattr(cli, '_recommend_deck', _must_not_load)
    _run(monkeypatch, 'recommend', '--schema')
    schema = json.loads(capsys.readouterr().out)
    assert schema['type'] == 'object'
    assert 'candidates' in schema['properties']
    assert 'Candidate' in schema['$defs']
