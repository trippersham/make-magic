# 0026 Unified event graph — events-as-nodes, gated event→event relays, loop-aware substantiveness

| Field | Value |
|---|---|
| Status | Accepted (framework). Phase 0 (schema) + Phase 1 (unified graph, true-SCC, loop-aware substantiveness) + Phase 2 (lifetrade gate-edges relocated to catalog relays) + Phase 3 (combo-certified promotion of the 7 resource-dependent families) + Phase 4 (internal reconciliation metric) + Phase 5 (behavioral verification + the cumulative 0.9.0 proof-carrying re-baseline) in force on branch `feat/83-unified-event-graph`. Self-mill + extra-turn DEFERRED (Phase-3b reverted → #90). Multi-phase, proof-carrying (ADR 0003); the cumulative OFF→ON re-baseline is the ADR 0003 2026-11-20 entry. |
| Review | **owner-approved 2026-11-19 (relay placement = `produced_by`-only confirmed).** Agent-authored from the approved design (`~/.claude/plans/trippersham/magic-tools/2026-11-19-node-admission-thematic-vs-mechanical/design.md`) + the astra-5.2 drift pressure-test (`drift-pressure-test.md`, spike `spike/83-unified-event-drift`, throwaway). Owner reads this record before Phase 1. |
| Test | Phase 0 (schema): from `plugins/make-magic/pipeline`: `uv run --extra dev pytest pipeline/transforms/strategy/tests/test_relay_schema.py pipeline/transforms/strategy/tests/test_event_ontology.py -q`. Parity gate: `uv run python -m pipeline.transforms.strategy.tests.engine_parity_oracle` — **the P0 SHA triple is UNCHANGED** (no relay families authored, no edges added). Later phases re-baseline under ADR 0003. |
| Code paths | [`derive_card_events.py`](../../pipeline/pipeline/transforms/strategy/derive_card_events.py) (`RelayEntry`, `SEED_MAP`, `derive_card_events`, `validate_seed_map`), [`relay.py`](../../pipeline/pipeline/transforms/strategy/relay.py) (repurposed as the provisional-edge channel, Phase 1), [`strategy_graph.py`](../../pipeline/pipeline/transforms/strategy/strategy_graph.py) (the one walk; SCC; loop-aware admission, Phase 1; `combo_event_edges` threading, Phase 3), [`combo_win.py`](../../pipeline/pipeline/transforms/strategy/combo_win.py) (`unbounded_intermediate_events` — the certified-unbounded liveness filter, Phase 3), [`combo_promotion.py`](../../pipeline/pipeline/transforms/strategy/combo_promotion.py) (the 7-family combo-certified promotion, Phase 3) |

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

The engine⟷combo loop-disagreement set is a **pipeline quality metric** tracked per corpus
run — the dumb honest diff (no `win_kind` predicate). The two engine loop-signals are read
**symmetrically**: the engine "sees a loop" iff it has a `recursive-engine` unit **OR** a
closing commander loop (`commander_loop.closes`). So:

- **Class A** = a combo WIN (`n_win_combos > 0`) but the engine sees NO loop — **neither** a
  `recursive-engine` unit **nor** a closing commander loop.
- **Class B** = the engine sees a loop (a `recursive-engine` unit **OR** a closing commander
  loop) but there is NO combo win.

The same "recursive unit OR closing commander loop" predicate defines "the engine sees a
loop" on both sides, so A and B are exact complements of the win/loop cross-product (ADR 0003
2026-11-20 re-baseline). It measures error now and is the future trigger for inference-based
self-healing (the Class-A list = the "loops to author" queue). It is **never in `resolve()`
output** (summary/log-only; see `reconciliation.py`). Class-A disagreements not combo-certified
stay surfaced for owner triage; they are never force-closed by a false gate edge.

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
- **Phase 1 (DONE 2026-11-19):** true-SCC `recursive-engine` + loop-aware substantiveness over the
  unified graph; ENGINE/RESOLVE/FULL_CORPUS re-baselined; 0 spurious admissions (W2 PASS).
- **Phase 2 (DONE 2026-11-20): lifetrade gate-edges relocated to catalog relays.** The 6 lifetrade
  `EventEffect`s moved from the dissolved `edge_roles.py` sidecar into `SEED_MAP` as
  `RelayEntry(from_event, gate)` on the produced event's `produced_by` (gate queries byte-for-byte).
  **Realized relay-gate liveness = PRESENCE, not admission** (§1 "enabling cards" clarified): a
  catalog relay is live iff its gate CardAttribute **has a carrier in the deck**, NOT iff the gate is
  a substantive identity node. This lets a sub-threshold enabler pair (Sanguine Bond's `gain-to-drain`
  + Exquisite Blood's `drain-to-gain`, each a 1–2-card attr) close the recursive lifegain↔life-loss-
  drain engine and reconcile engine⟷combo (both need both cards). Implemented as the optional
  `relay_gate_carriers` argument to `unified_event_graph_edges`/`recursive_event_set`, threaded from
  `elect_engine_units` (`set(attr_weight)`); it falls back to `nodes` for callers where present ==
  admitted (e.g. `loop_aware_nodes`, which already passes all present attrs). A gate does NOT become
  an identity node merely by enabling a relay (loop-aware admission unchanged — a pure lifetrade
  event-cycle carries no attr anchor, so no identity drift). Measured OFF/ON on the 187 corpus:
  exactly 4/170 gauntlet decks gain a recursive lifetrade engine (celes/elenda/karlov/liesa), 0 false
  lifetrade recursions, one-sided decks stay open; ENGINE/RESOLVE byte-identical (no hermetic fixture
  carries an enabler pair), FULL_CORPUS re-baselined `5045f7af…→85eba970…`. `CURATED_EDGE_ROLES` is
  now empty; the `EventEffect` type + the store waterfall remain for haiku/spellbook provisional edges.
- **Phase 3 (DONE 2026-11-20): combo-certified promotion of the 7 resource-dependent families**
  (ETB/death-recursion, mana-refund, cast/copy, draw-wheel, untap-mana, counter, landfall). These
  families get their event→event edges by reusing loops `combo_win` has ALREADY certified unbounded
  (`combo_win.unbounded_intermediate_events`, which reads `fid_route`'s per-fid magnitudes — no
  re-derived resource arithmetic, no gate query), NOT from naive gate edges (§4). `combo_promotion.py`
  holds a declarative `FAMILY_RELAY_EDGES` table (12 edge rules) and one uniform predicate
  (`certified_family_pairs`): a family edge `(a, b)` fires iff BOTH endpoint events are certified
  unbounded in the SAME combo. `combo_certified_event_edges` builds the per-deck 2-cycle edges, which
  `compute_facts` threads into the one SCC walk (`recursive_event_set` via `combo_event_edges`). The
  promotion only reclassifies the KIND of an engine unit that already exists (an event with in-deck
  producer AND consumer attrs) — it admits no new node, so `loop_aware_nodes` / identity / strategy /
  communities / headline are untouched; only `facts['engines']['engine_units']` moves. Correct by
  construction: a false carrier's event is finite (routes `oneshot`, not `intermediate`), so it is
  never in the certified-unbounded set and its family edge can never form — the Grim-Monolith /
  one-shot-copy / token-ETB-false-draw circuits stay structurally open. Measured OFF/ON on the 187
  corpus (pinned lake): 36/170 gauntlet decks gain ≥1 combo-certified recursive event across all 7
  families; Class A 34 → 26 (8 decks reconciled, incl. the audit's named Selvala/Derevi); the 10
  named false circuits ALL stay open (W5 hard gate held). ENGINE `80d0116a…→8f45c628…`, RESOLVE
  `3ebb33fd…→6eed6ba7…` (both via exactly 2 fixtures that carry a combo-certified combo —
  `gauntlet__urza-lord-high-artificer`, `owned__ozai`), FULL_CORPUS `85eba970…→17e806fa…`.
  Reproduced deterministically across two oracle runs. NOT a version bump (Phase 5 owns the ship
  re-baseline).
- **Phase 3b (built then DEFERRED, owner 2026-11-20 — see #90).** Two more combo-certified
  families (self-mill `card-enters-graveyard`, extra-turn) were built and measured (commit
  `b77da70`) but reverted (`883baff`) because the single-event **self-edge** representation they
  required is **topology-erased** and **leaks a false positive**: (1) a real self-mill engine's
  loop routes through its true carrier (Phenax's loop runs through `untap`), which the flat
  certified-unbounded set collapses to a bare `card-enters-graveyard` self-loop — erasing the
  mechanism; (2) Bruvac + Traumatize is a **one-shot full-mill** that the Spellbook combo DB
  mislabels `near-infinite`, so the self-edge falsely flags a `recursive-engine`. Win-detection is
  unaffected (only the engine-label families were reverted). A faithful re-add needs the #86
  magnitude + topology work first, then lands in **#90**. The seven Phase-3 families are
  unchanged; the P0 SHA triple is back at the Phase-3 values (ENGINE `8f45c628…`, RESOLVE
  `6eed6ba7…`, FULL_CORPUS `17e806fa…`).
- **Phase 4 (DONE 2026-11-20): internal reconciliation metric, as code.** The engine⟷combo
  loop-disagreement (§5) is a new module `reconciliation.py` emitted ONLY into the corpus-run
  summary (`run._summary`), never into a `resolve()` record. It READS existing record fields
  (`n_win_combos`, `facts['engines']['engine_units']`) with the symmetric "recursive unit OR
  closing commander loop" predicate — no new `win_kind`, no scoring — so it adds no field and
  the P0 SHA triple is **byte-unchanged** (ENGINE `8f45c628…`, RESOLVE `6eed6ba7…`, FULL_CORPUS
  `17e806fa…`; a pure addition). At the Phase-3 tip, gauntlet corpus: **Class A = 26, Class B =
  42** (`test_reconciliation.py::TestReconciliationCensus` pins the 26 named decks).
- **Phase 5 (DONE 2026-11-20): behavioral verification + the cumulative 0.9.0 re-baseline.** The
  feature is verified end-to-end on the real corpus (lifetrade + the 7 families close; the 10
  named false circuits stay open; the deferred families stay `static-payoff`; Class A = 26 /
  Class B = 42) and the whole #83 change ships as ONE proof-carrying cumulative re-baseline from
  pre-#83 `main` (ENGINE `4d3f5e6d…`, RESOLVE `940c3881…`, FULL_CORPUS `ec917355…`) to the P3 tip
  (ENGINE `8f45c628…`, RESOLVE `6eed6ba7…`, FULL_CORPUS `17e806fa…`) — see the ADR 0003
  2026-11-20 entry. Version bumped 0.8.0 → **0.9.0** (lockstep).
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
