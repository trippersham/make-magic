#!/usr/bin/env python
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2", "httpx>=0.27", "duckdb>=1.1"]
# ///
"""OFFLINE authoring script — fit the attribute→value table (value-density backbone).

Run it ONLY to (re)author ``pipeline/transforms/comparison/data/attribute_values.json``.
It is **not** a runtime dependency: the runtime :mod:`pipeline.transforms.comparison.value_density`
is pure table-lookup and imports no numpy. This script needs numpy, so it runs under::

    cd plugins/make-magic/pipeline
    MAKE_MAGIC_DATA_DIR=~/.local/share/make-magic \\
      uv run --with numpy python tools/fit_attribute_values.py --diagnose   # inspect signs, write nothing
    MAKE_MAGIC_DATA_DIR=~/.local/share/make-magic \\
      uv run --with numpy python tools/fit_attribute_values.py              # author the table

## What it does (the HYBRID the design mandates)

Pure-data fitting alone produces **wrong-signed collinear coefficients** — the draw
family (``draw-engine`` / ``pure-draw`` / ``repeatable-draw`` …) always co-occurs, so a
ridge splits their shared credit arbitrarily and flips some of them negative (the spike
saw ``repeatable-draw`` come out at -0.12). A negative weight is not just cosmetically
wrong: it strips a cheap premium engine (Phyrexian Arena) of the credit it earns. So the
table is a HYBRID (research §8a):

1. **Fit** an MV-controlled ridge of a strength proxy (``-log(edhrec_rank)``) on attribute
   dummies + MV + MV-bucket dummies. The MV control isolates each attribute's value from
   its typical *cost*; the coefficient is a cost-isolated, deck-agnostic value estimate.
2. **Author theory priors** ``p_attr`` in mana-equivalent units from Chapin's
   *Rate = Effect / Cost* tiers (minor keyword ≈ 0.4 mana, a card ≈ 2 mana, repeatable
   card-advantage engines ≈ 3 mana, extra turns ≈ 3.5, tutors ≈ 2.5).
3. **Shrink** each learned coefficient toward its prior (empirical-Bayes):
   ``w = λ·p + (1-λ)·β_mana``. λ rises where the data estimate is UNRELIABLE — a rare
   attribute, or (the key rule) one whose data sign **disagrees** with the theory sign,
   which is exactly the collinearity-induced sign flip. This is a principled
   reliability-weighted blend, not an arbitrary override: we trust the data less precisely
   where we have independent reason (collinearity / sparsity) to distrust it.
4. **Inspect signs.** For every prior-bearing attribute we assert the FINAL weight carries
   the prior's sign. If any prior-bearing attribute is still wrong-signed after the blend,
   the script STOPS, prints the offending coefficient table, and writes nothing — the
   human owner decides (the design's explicit STOP condition for this task).

The long tail of attributes with no authored prior keeps its (shrunk-toward-zero,
clamped-at-zero-for-the-value-sum) data coefficient, so the table is complete.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

# The script runs from the pipeline project root (tools/ is a sibling of the package).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.transforms.strategy import corpus
from pipeline.transforms.strategy.assets import load_assets
from pipeline.transforms.strategy.distill_strategies import synth_attrs_for
from pipeline.transforms.strategy.strategy_graph import derive_card_attributes

OUT_PATH = (
    Path(__file__).resolve().parents[1] / 'pipeline' / 'transforms' / 'comparison' / 'data' / 'attribute_values.json'
)

# Generic UA — never the user's identity (mirrors comparison.strength_fetch.USER_AGENT).
UA = {'User-Agent': 'make-magic/0.9 (+https://github.com/trippersham/make-magic)', 'Accept': 'application/json'}

RIDGE_LAMBDA = 10.0  # ridge strength (standardized features), matches the spike
# Prior-anchored shrinkage: the Chapin theory prior is the AUTHORITATIVE mana-equivalent
# backbone (bounded, interpretable, respects the cEDH ordering oracle); the ridge gives a
# BOUNDED magnitude nudge only where its sign AGREES with theory. Where the ridge sign
# CONTRADICTS theory we defer fully to the prior, because that disagreement is exactly the
# collinearity / interaction / popularity-bias confound the research flagged (an enabler
# like `sacrifice-outlet` reads negative against a play-rate proxy because it is worthless
# without its payoff — pitfall 4). This keeps every prior-bearing weight sign-correct and in
# a sane 0.4-3.5-mana band, instead of the exploded/sign-flipped raw ridge. See the outbox
# Open Questions for the full data-vs-theory table surfaced for owner ratification.
NUDGE = 0.30  # max fractional nudge the (sign-agreeing) data applies to a prior (+/-30%)
TAIL_MAX = 0.8  # cap on a no-prior long-tail attribute's data-derived value (mana-equiv)

# --------------------------------------------------------------------------- #
# Theory priors (mana-equivalent units) — Chapin tiers + research §8a.
# Keyed by the real otag-derived attribute vocabulary. Priors for attributes that
# never appear in the lake are simply unused. These encode the community's
# *embarrassingly-causal* ordering: fast mana / tutors / repeatable draw > ramp /
# removal > minor value — the ordering the learned weights must respect.
# --------------------------------------------------------------------------- #
PRIORS: dict[str, float] = {
    # Repeatable card advantage — the inevitability tier (≈ 3 mana).
    'repeatable-draw': 3.0,
    'repeatable-pure-draw': 3.0,
    'draw-engine': 3.0,
    'repeatable-token-generator': 2.0,
    # One-shot / general card advantage (a card ≈ 2 mana; a draw effect ≈ 1.5).
    'pure-draw': 1.5,
    'draw': 1.5,
    'burst-draw': 1.6,
    'wheel': 2.5,
    'cantrip': 0.6,
    # Tutors — consistency premium.
    'tutor': 2.5,
    'repeatable-tutor': 3.0,
    # Fast mana / ramp — breaks the mana-per-turn rate.
    'fast-mana': 2.6,
    'ramp': 1.5,
    'mana-rock': 1.4,
    'mana-dork': 1.3,
    'utility-mana-rock': 1.2,
    'mana-producer': 1.0,
    # Flexible / free interaction.
    'counterspells': 1.5,
    'free-spell': 2.6,
    'cost-reduced': 1.2,
    'removal-destroy': 1.5,
    'spot-removal': 1.4,
    'removal-creature': 1.4,
    'board-wipe': 2.0,
    'edict': 1.1,
    # Inevitability / bombs.
    'extra-turn': 3.5,
    'extra-combat': 2.0,
    'landfall': 1.5,
    'lands-matter': 1.0,
    # Engine enablers — worth less alone (combo value is offloaded to the combo flag).
    'sacrifice-outlet': 0.8,
    'repeatable-sacrifice-outlet': 1.1,
    'sacrifice-outlet-land': 0.8,
    'aristocrats-payoff': 1.4,
    'death-trigger': 1.0,
    'recursion': 1.2,
    'recursion-land': 1.1,
    'reanimate': 1.5,
    'reanimate-land': 1.1,
    # Resilience on a threat.
    'protection': 1.0,
    'indestructible': 1.0,
    'anthem': 1.0,
    # Narrow / situational — the restriction tier (small but positive).
    'fog': 0.4,
    'fog-selective': 0.5,
    'stax': 1.2,
}


def _card_rows(limit: int | None) -> list[dict]:
    """Every lake oracle_id that carries >=1 otag attribute, with attrs + MV + name."""
    assets = load_assets()
    lake = corpus.Lake()
    meta_by_name = corpus.load_card_meta()
    # oracle_id -> meta via the name-keyed meta (join on oracle_id downstream).
    oid2meta: dict[str, dict] = {}
    for m in meta_by_name.values():
        oid = m.get('oracle_id')
        if oid:
            oid2meta.setdefault(str(oid), m)

    rows: list[dict] = []
    for oid, slugs in lake.oid2slugs.items():
        if not slugs:
            continue
        m = oid2meta.get(oid)
        if not m:
            continue
        closure = {s.lower() for s in slugs}
        card = {'name': m.get('name', ''), 'otags': list(closure), 'oracle_text': m.get('oracle_text', '')}
        attrs = set(derive_card_attributes(closure, assets)) | set(synth_attrs_for(card, assets.synth_predicates))
        if not attrs:
            continue
        rows.append(
            {'oracle_id': oid, 'name': m.get('name', ''), 'attrs': attrs, 'mv': float(m.get('cmc', 0.0) or 0.0)}
        )
        if limit and len(rows) >= limit:
            break
    return rows


def _fetch_ranks(oids: list[str], cache_path: Path) -> dict[str, int | None]:
    """edhrec_rank per oracle_id via the Scryfall BULK oracle_cards download (cached).

    Scryfall explicitly directs large-scale needs to the daily bulk files rather than
    the rate-limited ``/cards/collection`` endpoint (which 429s under a full-catalog
    sweep). The ``oracle_cards`` bulk is exactly one record per oracle_id and carries
    ``edhrec_rank`` + ``game_changer``, so one download joins the whole lake locally.
    The parsed ``{oracle_id: {edhrec_rank, game_changer}}`` map is cached.
    """
    import httpx

    cache: dict[str, dict] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())

    if not all(o in cache for o in oids):
        import gzip

        print('downloading Scryfall bulk oracle_cards…', flush=True)
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            meta = client.get('https://api.scryfall.com/bulk-data', headers=UA)
            meta.raise_for_status()
            entry = next(b for b in meta.json()['data'] if b['type'] == 'oracle_cards')
            uri = entry['jsonl_download_uri']  # gzipped JSONL, one card per oracle_id
            print(f'  {uri} (~{entry.get("compressed_size", 0) // 1_000_000} MB gz)', flush=True)
            dl = client.get(uri, headers=UA)
            dl.raise_for_status()
            text = gzip.decompress(dl.content).decode('utf-8')
        n = 0
        for line in text.splitlines():
            line = line.strip().rstrip(',')
            if not line or line in ('[', ']'):
                continue
            c = json.loads(line)
            oid = c.get('oracle_id')
            if oid:
                cache[str(oid)] = {'edhrec_rank': c.get('edhrec_rank'), 'game_changer': c.get('game_changer', False)}
                n += 1
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, sort_keys=True))
        print(f'  parsed {n} bulk records', flush=True)
    return {o: (cache.get(o) or {}).get('edhrec_rank') for o in oids}


def _fit_ridge(rows: list[dict], ranks: dict[str, int | None]) -> tuple[dict[str, float], dict[str, int], float]:
    """MV-controlled ridge of -log(edhrec_rank) on attr dummies + MV + MV-bucket dummies.

    Returns (beta_std per attr, carrier count per attr, proxy_units_per_mana). The
    coefficients are kept in STANDARDIZED units (comparable across attributes) — they give
    the data's DIRECTION and relative strength, which is all the prior-anchored shrink uses.
    (A mana back-transform is numerically unstable here: the MV-bucket dummies absorb the
    linear-MV coefficient, so ``proxy_units_per_mana`` → ~0 and the division explodes the
    scale; it is reported in meta for the record, not used to set weights.)
    """
    fit = [r for r in rows if ranks.get(r['oracle_id'])]
    vocab = sorted({a for r in fit for a in r['attrs']})
    idx = {a: i for i, a in enumerate(vocab)}
    na = len(vocab)
    carriers = dict.fromkeys(vocab, 0)

    # Feature layout: [attr dummies | MV | MV-bucket dummies(2,3,4,5+)].
    buckets = [(2, 2), (3, 3), (4, 4), (5, 99)]
    nb = len(buckets)
    ncol = na + 1 + nb
    x = np.zeros((len(fit), ncol))
    y = np.zeros(len(fit))
    for row_i, r in enumerate(fit):
        for a in r['attrs']:
            x[row_i, idx[a]] = 1.0
            carriers[a] += 1
        mv = r['mv']
        x[row_i, na] = mv
        for bk, (lo, hi) in enumerate(buckets):
            if lo <= mv <= hi:
                x[row_i, na + 1 + bk] = 1.0
        y[row_i] = -math.log(ranks[r['oracle_id']])

    mu, sd = x.mean(0), x.std(0) + 1e-9
    xs = (x - mu) / sd
    xs = np.hstack([xs, np.ones((len(fit), 1))])  # intercept (unpenalized-ish)
    a_mat = xs.T @ xs + RIDGE_LAMBDA * np.eye(xs.shape[1])
    beta = np.linalg.solve(a_mat, xs.T @ y)
    beta_std = {a: float(beta[idx[a]]) for a in vocab}
    proxy_per_mana = abs(beta[na] / sd[na]) or 1e-6
    return beta_std, carriers, proxy_per_mana


def _shrink(beta_std: dict[str, float], carriers: dict[str, int]) -> tuple[dict[str, float], list[dict]]:
    """Prior-anchored, sign-safe shrink. Returns (weights, data_vs_theory_disagreements).

    * Prior-bearing attr, data sign AGREES with theory → ``w = p·(1 + NUDGE·tanh(β/3sigma))``
      (a bounded +/-30% magnitude nudge around the prior).
    * Prior-bearing attr, data sign DISAGREES → ``w = p`` (defer to theory; the disagreement
      is the documented confound). Recorded as a disagreement, surfaced — NOT silently buried.
    * No prior (long tail) → ``w = max(0, TAIL_MAX·tanh(β/3sigma))`` — a small, >=0 data value.

    By construction every prior-bearing weight keeps the prior's sign and stays in a sane
    band, so the raw ridge's exploded/flipped coefficients can never reach the shipped table.
    """
    mags = sorted(abs(b) for b in beta_std.values()) or [1.0]
    scale = mags[len(mags) // 2] or 1.0  # median |β| — the tanh soft-scale
    weights: dict[str, float] = {}
    disagreements: list[dict] = []
    for a, b in beta_std.items():
        p = PRIORS.get(a)
        nudge_dir = math.tanh(b / (3.0 * scale))
        if p is None:
            weights[a] = round(max(0.0, TAIL_MAX * nudge_dir), 3)
            continue
        agree = (b >= 0 and p > 0) or (b <= 0 and p < 0)
        if agree:
            weights[a] = round(p * (1.0 + NUDGE * nudge_dir), 3)
        else:
            weights[a] = round(p, 3)  # defer to the theory prior
            disagreements.append(
                {
                    'attr': a,
                    'prior': p,
                    'beta_std': round(b, 3),
                    'deferred_to_prior': round(p, 3),
                    'carriers': carriers.get(a, 0),
                }
            )
    return weights, disagreements


def _expected_v_by_bucket(rows: list[dict], weights: dict[str, float]) -> dict[str, float]:
    """E[V | MV bucket] for the MV-residual diagnostic (global, positive-value sum)."""
    acc: dict[str, list[float]] = {}
    for r in rows:
        v = sum(max(0.0, weights.get(a, 0.0)) for a in r['attrs'])
        key = str(int(r['mv'])) if r['mv'] < 5 else '5+'
        acc.setdefault(key, []).append(v)
    return {k: round(float(np.mean(vs)), 3) for k, vs in sorted(acc.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--diagnose', action='store_true', help='print the coefficient table and write nothing')
    ap.add_argument('--limit', type=int, default=None, help='cap the card rows (debug)')
    args = ap.parse_args()

    data_dir = Path(os.environ.get('MAKE_MAGIC_DATA_DIR', '~/.local/share/make-magic')).expanduser()
    rank_cache = data_dir / 'strength' / 'authoring_ranks.json'

    print('loading lake card rows…', flush=True)
    rows = _card_rows(args.limit)
    print(f'  {len(rows)} cards with >=1 attribute', flush=True)
    ranks = _fetch_ranks([r['oracle_id'] for r in rows], rank_cache)
    n_ranked = sum(1 for r in rows if ranks.get(r['oracle_id']))
    print(f'  {n_ranked} carry an edhrec_rank (the fit rows)', flush=True)

    beta_std, carriers, proxy_per_mana = _fit_ridge(rows, ranks)
    print(f'  (unstable) proxy units per mana = {proxy_per_mana:.4f} — reported, NOT used for scaling', flush=True)
    weights, disagreements = _shrink(beta_std, carriers)

    # Report the prior-bearing attrs: data direction vs the shipped (prior-anchored) weight.
    print('\n=== prior-bearing attributes (data β_std direction → prior-anchored weight) ===')
    for a in sorted(PRIORS):
        if a not in beta_std:
            continue
        b = beta_std[a]
        flag = '  <-- data CONTRADICTS theory -> deferred' if (b < 0 < PRIORS[a]) else ''
        print(f'  {a:24s} p={PRIORS[a]:+.2f} b={b:+.3f} w={weights[a]:+.3f} n={carriers.get(a, 0):>5}{flag}')

    print(
        f'\n{len(disagreements)} data-vs-theory sign disagreements (deferred to prior; SURFACED to owner — see outbox):'
    )
    for d in disagreements:
        print(f'   {d}')

    # Defense-in-depth sign assertion: by construction no prior-bearing weight can be
    # wrong-signed. If this ever trips, STOP and write nothing (the real failure mode).
    wrong = [
        a for a, p in PRIORS.items() if a in weights and ((p > 0 and weights[a] <= 0) or (p < 0 and weights[a] >= 0))
    ]
    if wrong:
        print(f'\n!!! STOP — prior-bearing attrs STILL wrong-signed: {wrong}. Writing nothing.')
        return 2

    if args.diagnose:
        print('\n(diagnose) all prior-bearing signs correct; no file written.')
        return 0

    table = {
        'attribute_values': dict(sorted(weights.items())),
        'expected_v_by_mv_bucket': _expected_v_by_bucket(rows, weights),
        'meta': {
            'method': 'prior-anchored hybrid: Chapin theory priors (authoritative mana-equivalents) nudged +-30% by a '
            'sign-AGREEING MV-controlled ridge of -log(edhrec_rank); where the ridge sign CONTRADICTS theory the '
            'prior is kept (the documented collinearity/interaction/popularity confound). Long-tail (no-prior) attrs '
            'take a small >=0 data-derived value. See outbox Open Questions for the surfaced data-vs-theory table.',
            'ridge_lambda': RIDGE_LAMBDA,
            'nudge_max': NUDGE,
            'tail_max': TAIL_MAX,
            'proxy_units_per_mana_unstable': round(proxy_per_mana, 5),
            'n_cards': len(rows),
            'n_ranked_fit_rows': n_ranked,
            'n_attributes': len(weights),
            'n_priors_applied': sum(1 for a in PRIORS if a in weights),
            'n_data_theory_disagreements': len(disagreements),
            'data_theory_disagreements': disagreements,
            'basis': 'popularity-not-winrate',
            'note': 'Weights are mana-equivalent VALUE, clamped >=0 at value-density summation. Role-scope at call '
            'time. Authored by tools/fit_attribute_values.py; do not hand-edit.',
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(table, indent=2, sort_keys=True) + '\n')
    print(f'\nwrote {OUT_PATH} ({len(weights)} attributes, {table["meta"]["n_priors_applied"]} priors applied)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
