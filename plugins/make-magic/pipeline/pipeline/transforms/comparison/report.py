"""Rendering for ``collection compare`` — the four comparison layers → text / JSON.

Formatting only: it takes the typed :class:`~.identity_diff.IdentityDiff` (layer ①),
:class:`~.engine_diff.EngineDiff` (layer ②), :class:`~.execution_diff.ExecutionDiff`
(layer ③), and :class:`~.intent_diff.IntentDiff` (layer ④) and lays them out. Keeping it
here (not in the CLI) makes the ``compare`` verb a thin orchestrator and lets the layout
be unit-tested without resolving a deck.
"""

from __future__ import annotations

from typing import Any

from pipeline.transforms.comparison.engine_diff import EngineDiff
from pipeline.transforms.comparison.execution_diff import ExecutionDiff
from pipeline.transforms.comparison.identity_diff import IdentityDiff
from pipeline.transforms.comparison.intent_diff import IntentDiff

#: How many rows of the ranked reweight table to show in the text view.
_RANKED_GAP_ROWS = 10
#: How many ranked-unit deltas to show in the engine text view.
_UNIT_DELTA_ROWS = 10
#: How many divergent events / signature cards to show in the intent text view.
_INTENT_EVENT_ROWS = 6
_SIGNATURE_ROWS = 6


def _engine_payload(d: EngineDiff) -> dict[str, Any]:
    """Serialize the layer-② engine diff to the JSON shape."""
    return {
        'regime_a': d.regime_a,
        'regime_b': d.regime_b,
        'a_cycle_events': d.a_cycle_events,
        'b_cycle_events': d.b_cycle_events,
        'a_cycle_weight': d.a_cycle_weight,
        'b_cycle_weight': d.b_cycle_weight,
        'shared_cycle_events': d.shared_cycle_events,
        'added_cycle_events': d.added_cycle_events,
        'dropped_cycle_events': d.dropped_cycle_events,
        'a_loop_closes': d.a_loop_closes,
        'b_loop_closes': d.b_loop_closes,
        'a_reaches_back': d.a_reaches_back,
        'b_reaches_back': d.b_reaches_back,
        'a_relay_hubs': d.a_relay_hubs,
        'b_relay_hubs': d.b_relay_hubs,
        'shared_relay_hubs': d.shared_relay_hubs,
        'a_certified_infinite': d.a_certified_infinite,
        'b_certified_infinite': d.b_certified_infinite,
        'unit_deltas': [
            {
                'event': u.event,
                'a_mass': u.a_mass,
                'b_mass': u.b_mass,
                'delta_mass': u.delta_mass,
                'a_share': u.a_share,
                'b_share': u.b_share,
                'a_formation_mode': u.a_formation_mode,
                'b_formation_mode': u.b_formation_mode,
                'a_commander_coupled': u.a_commander_coupled,
                'b_commander_coupled': u.b_commander_coupled,
                'a_recursive': u.a_recursive,
                'b_recursive': u.b_recursive,
                'tag': u.tag,
            }
            for u in d.unit_deltas
        ],
        'reading': d.reading,
    }


def _signature_payload(cards: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            'oracle_id': ic.oracle_id,
            'name': ic.name,
            'mv': ic.mv,
            'produces': ic.produces,
            'consumes': ic.consumes,
            'participation': ic.participation,
            'carried_attrs': ic.carried_attrs,
            'strength': ic.strength,
        }
        for ic in cards
    ]


def _intent_payload(d: IntentDiff) -> dict[str, Any]:
    """Serialize the layer-④ surgical intent diff to the JSON shape."""
    return {
        'reading': d.reading,
        'divergent_events': [
            {
                'event': e.event,
                'a_total': e.a_total,
                'b_total': e.b_total,
                'delta': e.delta,
                'leader': e.leader,
                'recursive': e.recursive,
                'a_n_cards': e.a_n_cards,
                'b_n_cards': e.b_n_cards,
                'a_signature': _signature_payload(e.a_signature),
                'b_signature': _signature_payload(e.b_signature),
                'a_high_mv': [{'name': ic.name, 'mv': ic.mv} for ic in e.a_high_mv],
                'b_high_mv': [{'name': ic.name, 'mv': ic.mv} for ic in e.b_high_mv],
            }
            for e in d.divergent_events
        ],
    }


def comparison_payload(
    id_diff: IdentityDiff,
    exec_diff: ExecutionDiff,
    *,
    a_name: str,
    b_name: str,
    a_identity_gap: float | None = None,
    b_identity_gap: float | None = None,
    cache_hits: tuple[bool, bool] | None = None,
    cohort: dict[str, Any] | None = None,
    engine_diff: EngineDiff | None = None,
    intent_diff: IntentDiff | None = None,
) -> dict[str, Any]:
    """Assemble the structured ``compare`` payload (the ``--json`` shape)."""
    payload: dict[str, Any] = {
        'a': a_name,
        'b': b_name,
        'identity': {
            'a_identity': id_diff.a_identity,
            'b_identity': id_diff.b_identity,
            'added_in_b': id_diff.added_in_b,
            'dropped_in_b': id_diff.dropped_in_b,
            'cosine_share': id_diff.cosine_share,
            'cosine_ranked': id_diff.cosine_ranked,
            'l1_share': id_diff.l1_share,
            'a_identity_gap': a_identity_gap,
            'b_identity_gap': b_identity_gap,
            'ranked_gap': [list(row) for row in id_diff.ranked_gap],
        },
        'execution': {
            'axis_deltas': [
                {'axis': d.axis, 'a_n': d.a_n, 'b_n': d.b_n, 'delta': d.delta} for d in exec_diff.axis_deltas
            ],
            'a_mean_cmc': exec_diff.a_mean_cmc,
            'b_mean_cmc': exec_diff.b_mean_cmc,
            'a_buckets': exec_diff.a_buckets,
            'b_buckets': exec_diff.b_buckets,
            'a_early_ramp': exec_diff.a_early_ramp,
            'b_early_ramp': exec_diff.b_early_ramp,
            'a_ramp_cedh': exec_diff.a_ramp_cedh,
            'b_ramp_cedh': exec_diff.b_ramp_cedh,
            'power': {k: list(v) for k, v in exec_diff.power.items()},
        },
    }
    if engine_diff is not None:
        payload['engine'] = _engine_payload(engine_diff)
    if intent_diff is not None:
        payload['intent'] = _intent_payload(intent_diff)
    if cache_hits is not None:
        payload['cache_hits'] = {'a': cache_hits[0], 'b': cache_hits[1]}
    if cohort is not None:
        payload['cohort'] = cohort
    return payload


def render_text(payload: dict[str, Any]) -> str:
    """Render the ``compare`` payload as a stable, human-readable report."""
    idn = payload['identity']
    ex = payload['execution']
    out: list[str] = []
    out.append(f'compare: A={payload["a"]!r}  vs  B={payload["b"]!r}')
    if 'cache_hits' in payload:
        ch = payload['cache_hits']
        out.append(f'  (cache: A={"hit" if ch["a"] else "miss"}  B={"hit" if ch["b"] else "miss"})')

    out.append('')
    out.append('IDENTITY (layer 1)')
    out.append(f'  A identity: {idn["a_identity"]}')
    out.append(f'  B identity: {idn["b_identity"]}')
    out.append(f'  added in B: {idn["added_in_b"]}   dropped in B: {idn["dropped_in_b"]}')
    out.append(
        f'  cosine(share)={idn["cosine_share"]}  cosine(ranked)={idn["cosine_ranked"]}  '
        f'L1(share)={idn["l1_share"]}  gap A/B={idn["a_identity_gap"]}/{idn["b_identity_gap"]}'
    )
    out.append('  ranked gap (strategy | A_wq | B_wq | delta):')
    for strat, a_wq, b_wq, delta in idn['ranked_gap'][:_RANKED_GAP_ROWS]:
        out.append(f'    {strat}: {a_wq} | {b_wq} | {delta:+g}')

    if 'engine' in payload:
        _render_engine(out, payload['engine'])

    out.append('')
    out.append('EXECUTION (layer 3)')
    out.append('  degree axes (axis | A | B | delta):')
    for d in ex['axis_deltas']:
        delta = '—' if d['delta'] is None else f'{d["delta"]:+d}'
        out.append(f'    {d["axis"]}: {d["a_n"]} | {d["b_n"]} | {delta}')
    out.append(f'  mean CMC: A={ex["a_mean_cmc"]}  B={ex["b_mean_cmc"]}')
    out.append(f'  curve A: {ex["a_buckets"]}')
    out.append(f'  curve B: {ex["b_buckets"]}')
    out.append(f'  early ramp (<=2): A={ex["a_early_ramp"]}  B={ex["b_early_ramp"]}')
    out.append(f'  ramp cedh-level: A={ex["a_ramp_cedh"]}  B={ex["b_ramp_cedh"]}')
    out.append('  interaction power (field | A | B):')
    for field, (av, bv) in ex['power'].items():
        out.append(f'    {field}: {av} | {bv}')

    if 'intent' in payload:
        _render_intent(out, payload['intent'])

    if payload.get('cohort') is not None:
        out.append('')
        out.append(f'COHORT (stub): {payload["cohort"]}')
    return '\n'.join(out)


def _render_engine(out: list[str], eng: dict[str, Any]) -> None:
    """Append the layer-② engine section (regime-switched reading + structured surface)."""
    out.append('')
    out.append('ENGINE (layer 2)')
    out.append(f'  regime: A={eng["regime_a"]}  B={eng["regime_b"]}')
    out.append(f'  convergent cycle A: {eng["a_cycle_events"]} (weight {eng["a_cycle_weight"]})')
    out.append(f'  convergent cycle B: {eng["b_cycle_events"]} (weight {eng["b_cycle_weight"]})')
    if eng['added_cycle_events'] or eng['dropped_cycle_events']:
        out.append(
            f'  cycle events added in B: {eng["added_cycle_events"]}   dropped in B: {eng["dropped_cycle_events"]}'
        )
    out.append(
        f'  commander loop closes: A={eng["a_loop_closes"]} (→ {eng["a_reaches_back"]})  '
        f'B={eng["b_loop_closes"]} (→ {eng["b_reaches_back"]})'
    )
    out.append(f'  relay hubs  A={eng["a_relay_hubs"]}  B={eng["b_relay_hubs"]}')
    out.append(f'  certified-infinite (#90): A={eng["a_certified_infinite"]}  B={eng["b_certified_infinite"]}')
    out.append('  ranked units (event | A mass/mode | B mass/mode | Δ | tag):')
    for u in eng['unit_deltas'][:_UNIT_DELTA_ROWS]:
        a = '—' if u['a_mass'] is None else f'{u["a_mass"]}/{u["a_formation_mode"]}'
        b = '—' if u['b_mass'] is None else f'{u["b_mass"]}/{u["b_formation_mode"]}'
        delta = '—' if u['delta_mass'] is None else f'{u["delta_mass"]:+g}'
        out.append(f'    {u["event"]}: {a} | {b} | {delta} | {u["tag"]}')
    for line in eng['reading'].split('\n'):
        out.append(f'  {line}')


def _render_intent(out: list[str], intent: dict[str, Any]) -> None:
    """Append the layer-④ surgical-intent section (per-event divergence + signature cards)."""
    out.append('')
    out.append('INTENT (layer 4) — surgical, event-anchored')
    divergent = intent['divergent_events']
    if not divergent:
        out.append('  no event clears the divergence threshold (both decks invest alike).')
    for e in divergent[:_INTENT_EVENT_ROWS]:
        loop = ' [LOOP]' if e['recursive'] else ''
        out.append(
            f'  {e["event"]}{loop}: A={e["a_total"]} ({e["a_n_cards"]} cards)  '
            f'B={e["b_total"]} ({e["b_n_cards"]} cards)  Δ={e["delta"]:+d}  → {e["leader"]} invests more'
        )
        sig = e['a_signature'] if e['leader'] == 'A' else e['b_signature']
        high = e['a_high_mv'] if e['leader'] == 'A' else e['b_high_mv']
        for ic in sig[:_SIGNATURE_ROWS]:
            strength = '' if ic['strength'] is None else f' str={ic["strength"]:.3f}'
            out.append(
                f'      {ic["name"]} (MV {ic["mv"]:g}, participation {ic["participation"]}{strength}) '
                f'via {ic["carried_attrs"]}'
            )
        if high:
            out.append(f'      >=4-MV bench: {[(c["name"], int(c["mv"])) for c in high[:6]]}')
    for line in intent['reading'].split('\n'):
        out.append(f'  {line}')
