"""Rendering for ``collection compare`` — identity + execution deltas → text / JSON.

Formatting only: it takes the typed :class:`~.identity_diff.IdentityDiff` and
:class:`~.execution_diff.ExecutionDiff` and lays them out. Keeping it here (not in
the CLI) makes the ``compare`` verb a thin orchestrator and lets the layout be
unit-tested without resolving a deck.
"""

from __future__ import annotations

from typing import Any

from pipeline.transforms.comparison.execution_diff import ExecutionDiff
from pipeline.transforms.comparison.identity_diff import IdentityDiff

#: How many rows of the ranked reweight table to show in the text view.
_RANKED_GAP_ROWS = 10


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

    if payload.get('cohort') is not None:
        out.append('')
        out.append(f'COHORT (stub): {payload["cohort"]}')
    return '\n'.join(out)
