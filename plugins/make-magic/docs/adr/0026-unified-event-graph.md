# 0026 Unified event graph — events-as-nodes, gated event→event relays, loop-aware substantiveness

| Field | Value |
|---|---|
| Status | Accepted (framework). Phase 0 (schema) in force on branch `feat/83-unified-event-graph`; Phases 1–5 pending. Multi-phase, proof-carrying (ADR 0003); ship gate at Phase 5. |
| Review | **pending owner review — the model is the contract.** Agent-authored from the approved design (`~/.claude/plans/trippersham/magic-tools/2026-11-19-node-admission-thematic-vs-mechanical/design.md`) + the astra-5.2 drift pressure-test (`drift-pressure-test.md`, spike `spike/83-unified-event-drift`, throwaway). Owner reads this record before Phase 1. |
| Test | Phase 0 (schema): from `plugins/make-magic/pipeline`: `uv run --extra dev pytest pipeline/transforms/strategy/tests/test_relay_schema.py pipeline/transforms/strategy/tests/test_event_ontology.py -q`. Parity gate: `uv run python -m pipeline.transforms.strategy.tests.engine_parity_oracle` — **the P0 SHA triple is UNCHANGED** (no relay families authored, no edges added). Later phases re-baseline under ADR 0003. |
| Code paths | [`derive_card_events.py`](../../pipeline/pipeline/transforms/strategy/derive_card_events.py) (`RelayEntry`, `SEED_MAP`, `derive_card_events`, `validate_seed_map`), [`relay.py`](../../pipeline/pipeline/transforms/strategy/relay.py) (repurposed as the provisional-edge channel, Phase 1), [`strategy_graph.py`](../../pipeline/pipeline/transforms/strategy/strategy_graph.py) (the one walk; SCC; loop-aware admission, Phase 1), [`combo_win.py`](../../pipeline/pipeline/transforms/strategy/combo_win.py) (combo-certified promotion, Phase 3) |

## Context

The engine modelled two kinds of thing in two places, and that split is the root of
issue #83. An **Event** (`{produced_by:[attr], triggers:[attr]}`) collapses to
producer→consumer bipartite edges (`strategy_graph.py`). An **EventEffect** — a guarded
`trigger_event → produced_event` edge in the `edge_roles.py` sidecar (ADR 0011) — is read
only by the non-P0 `relay.py` path. The P0 graph that produces `facts['engines']` never
sees the relay edges, so within-card loops do not close.

The corpus audit (`loop-coverage-audit.md`) reframed the scope: across 187 decks / 380 win
combos, **323 combos are loop-bearing**, and the entire modelling gap is the **event→event
EDGE layer** — **zero new events are needed** (every loop-carrier event already exists in
the 33-event catalog). #83's lifetrade fix alone covers 1 of 31 "combo-wins-but-engine-
sees-no-loop" decks; the remaining families cover the rest.

The split also forced two bandaids: `recursive-engine` was `producers & consumers` (a
heuristic for "is this a loop", not a real cycle test), and admission was framed as
"thematic vs mechanical" — a framing that never named the real signal (structural
load-bearing-ness).

## Decision

**Unify Event and EventEffect into one concept, build one graph, walk it once, and make
substantiveness loop-aware.** The `EventEffect`-sidecar design is superseded.

### 1. One Event concept; `{from_event, gate}` relay entries

A `produced_by`/`triggers` entry is uniformly a **CardAttribute reference** (an otag/synth
slug string), OR a **relay** that additionally names a source Event:
`RelayEntry(from_event, gate)`. Nodes = CardAttributes ∪ Events. The field mapping from the
dissolved `EventEffect` (ADR 0011) is:

- `trigger_event` → `from_event` (the source event the relay fires on),
- `produced_event` → the **owning event** (the side the relay entry sits on),
- the guard query / provenance → `gate` (one CardAttribute ref, over the ADR 0009 closed
  query vocabulary).

`EventEffect` dissolves into `Event`; `edge_roles.py`'s sidecar role ends once the families
are relocated. Relays are authored in **`SEED_MAP`**, not hand-edited `event_catalog.json`,
because `derive_card_events` overwrites `produced_by`/`triggers` from the SEED_MAP closure
at load — a hand-edited catalog relay would be clobbered.

### 2. One graph, one walk; `recursive-engine` = true SCC membership

Build a directed graph over CardAttribute ∪ Event nodes with attr→event, event→attr, and
event→event (relay) edges. Loop/engine detection = a single Tarjan **SCC**/cycle pass over
it (sorted Tarjan + existing tiebreaks for determinism). `recursive-engine` becomes **true
SCC membership (a cycle)**, replacing the `producers & consumers` heuristic. Per-event unit
enumeration is **preserved** (static-payoff units stay); the SCC only reclassifies
recursive-vs-static and closes loops — detection is **not** cycle-only.

### 3. Substantiveness gains a structural signal (prevalence ∨ cycle-membership)

A node is **substantive (earned/admitted)** iff it is **statistically prevalent**
(`carriers ≥ 3`, or commander-carried) **OR structurally load-bearing** (on a closed cycle —
binary SCC-membership ≥ 2, among otherwise-substantive nodes). A loop-closing relay node is
therefore substantive → admitted into the one graph → eligible for identity AND for the
(UNCHANGED) centrality/mass-election ranking (ADR 0014). Prevalence alone still suppresses
incidental non-loop noise. The structural signal is computed by a **provisional cycle pass
INSIDE the substantiveness stage** (structure, not mass — honouring the ADR 0021
substantiveness→centrality order). Binary membership, not a continuous score.

The **"thematic vs mechanical"** framing is retired. **Substantiveness stays distinct from
centrality:** substantiveness (ADR 0002/0004/0020) is the Strategy/Event-layer admission
gate; centrality (ADR 0014) is the Engine-unit mass-election ranking that runs AFTER it.
This change is **entirely in substantiveness**; centrality's criterion is untouched.

### 4. The family edge-SOURCES — split by whether a query suffices

The spike proved the **framework sound** (real events-as-nodes + Tarjan SCC; 0 off-cycle
admissions) but **naive gate queries manufacture false loops** for the resource-dependent
families: the ADR-0009 closed-vocabulary query layer can match cards but **cannot express
the resource arithmetic** (net mana, repeatability, target/zone) that edge validity needs,
and adding a resource-arithmetic atom is exactly what the closed vocabulary prohibits
(≥10 false circuits — Grim Monolith nets −1 mana across 5 cEDH decks; one-shot copies like
Kitsa / Narset's Reversal; token/ETB false draw returns). Edge-semantic validation is
**inherently outside the query layer**. So the edge SOURCE is split:

- **lifetrade (the existing 6)** → **gate-query catalog edges** (`{from_event, gate}`). The
  draw/gain/drain relations are unconditional; a query suffices; the spike found them clean.
- **the 7 resource/repeatability-dependent families** (ETB/death, mana-refund, cast/copy,
  draw-wheel, untap-mana, counter, landfall) → **combo-certified promotion**: the engine
  graph gets these event→event edges from loops `combo_win` has ALREADY certified unbounded
  (reuse its magnitudes), **NOT** from naive gate queries. This reconciles engines⟷combos by
  construction and structurally cannot form the Grim-Monolith / one-shot-copy false circuits.

### 5. Internal reconciliation metric (not user-facing)

The engine⟷combo loop-disagreement set (Class A: combo win, no recursive engine; Class B:
recursive engine, no win) is a **pipeline quality metric** tracked per corpus run — the dumb
honest diff (no `win_kind` predicate). It measures error now and is the future trigger for
inference-based self-healing (the disagreement list = the "loops to author" queue). It is
**never in `resolve()` output**. Class-A disagreements not combo-certified stay surfaced for
owner triage; they are never force-closed by a false gate edge.

### 6. Provisional-edge channel — repurpose `relay.py`, do not delete

The one walk consumes **catalog edges + the runtime store-backed provisional edges**
(combo_edges spellbook fids, ADR-0017 haiku `ProvisionalAttributeStore`), so
`engine_report`'s RESOLVED/PROVISIONAL label and `combo_edges` keep working. The 6 lifetrade
+ 7 families are catalog-static; spellbook/haiku edges stay per-deck runtime.

## Reasons

- **One concept, one graph, one walk** removes the split that is #83's root. The audit
  proved the gap is edges, not events, so unification is the minimal correct change.
- **True SCC** names what `recursive-engine` always meant (a cycle), and ends the
  `producers & consumers` heuristic that false-positived.
- **prevalence ∨ cycle-membership** names the real admission signal the "thematic vs
  mechanical" framing never did: a node earns admission by being common OR by being
  structurally load-bearing in a loop.
- **Combo-certified promotion** is correct-by-construction: it reuses the combo-detector's
  already-derived unbounded verdict instead of re-deriving resource arithmetic in a query
  layer that provably cannot express it.

## Options not taken

- **B — dual-role relay attributes (events stay edge-labels).** Mechanically equivalent, but
  not the one concept; keeps the authoring surface split.
- **C — patch the sidecar into P0 without unifying.** Keeps the two-graph split (the root of
  #83). Rejected.
- **Naive gate-query edges for all 8 families** (the pre-spike scope). Rejected by the drift
  pressure-test: the closed query vocabulary cannot express edge-validity arithmetic, so the
  7 resource-dependent families manufacture ≥10 false circuits. Replaced by combo-certified
  promotion.
- **A structural resource-balance analyzer** (derive combo magnitude from card data; validate
  *novel* complex loops) — the richer long-game replacement for combo-certified promotion.
  **Deferred to issue #86**, not in this effort.

## Consequences

- **Phase 0 (this change) is schema-only.** `RelayEntry` + the `relays` side-key carry a
  `{from_event, gate}` entry through `derive_card_events` un-clobbered; `validate_seed_map`
  and `test_event_ontology.py` accept the shape. **Zero relay families are authored**, so
  every derived list stays all-strings and the **P0 SHA triple is byte-identical**.
- Later phases are output-moving and **proof-carrying (ADR 0003)**: measure OFF/ON, verify
  the intended delta, regenerate the SHA triple, move fixtures. Acceptance: Queza reports the
  recursive lifetrade loop; the families close their audited loop-combos; the 10 named false
  circuits STAY OPEN; Class A shrinks toward the ~57 genuinely-terminal combos; 0 false
  engines.
- Substantiveness (ADR 0002/0004/0020) and centrality (ADR 0014) stay in their ADR-0021
  layers; this change is entirely in substantiveness.

## Related records

- **Supersedes** the `EventEffect`-sidecar design for within-card edges (ADR 0011's sidecar
  role; the guard operands survive as the relay `gate`).
- [0011](0011-eventeffect-guards.md) — `EventEffect` dissolves into `Event`; its
  `trigger_event`/`produced_event`/guard map onto `RelayEntry.from_event`/owning-event/`gate`.
- [0012](0012-event-ontology.md) — Event is a node; entries may be relay refs; the string-only
  invariant is relaxed (amended there).
- [0002](0002-substantiveness-generic-evaluator.md) / [0004](0004-node-predicates.md) /
  [0020](0020-event-substantiveness-producer-anchor.md) — substantiveness gains the structural
  loop-participation signal (amended there); "thematic vs mechanical" retired.
- [0014](0014-engine-mass-election.md) — centrality criterion UNCHANGED; only the engine-unit
  `recursive-engine` label becomes true-SCC (noted there).
- [0009](0009-unified-card-attribute-query-model.md) — the gate CardAttribute uses only the
  closed query vocabulary; its inability to express resource arithmetic is why the 7 families
  use combo-certified promotion, not gate queries.
- [0016](0016-closure-mechanism-win-walk.md) — `combo_win` is the structural ground truth the
  combo-certified promotion reuses and the reconciliation metric measures against.
- [0017](0017-edge-derivation-native-then-haiku.md) — the provisional (spellbook/haiku) edges
  the repurposed `relay.py` feeds into the one walk.
- [0021](0021-pipeline-superstructure-card-strategy-engine.md) — the substantiveness→centrality
  ordering this change honours.
- [0022](0022-concept-anchored-derived-adjacency.md) — the derived adjacency the relay schema
  extends; the reconciliation metric is the future self-healing trigger ([0022] direction).
- [0003](0003-byte-parity-held-files.md) — Phase 0 is byte-identical; later phases re-baseline.
- Issues: **#83** (this effort), **#86** (deferred structural resource-balance analyzer).
