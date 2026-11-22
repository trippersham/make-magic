# 0026 Unified event graph — events-as-nodes, gated event→event relays, loop-aware substantiveness

| Field | Value |
|---|---|
| Status | Accepted (framework). What SHIPS in 0.9.0 on branch `feat/83-unified-event-graph`: Phase 0 (schema) + Phase 1 (unified graph, true-SCC, loop-aware substantiveness) + Phase 2 (lifetrade gate-edges relocated to catalog relays) + Phase 4 (internal reconciliation metric). **The 7 resource-dependent combo-certified families (§4) are DEFERRED to #90** (scope reduction, owner 2026-11-22): three adversarial reviews found every corpus false-positive concentrated in that family layer (executability / quantity / role / event-mapping bugs), so the combo-certified promotion — and its `combo_promotion.py` + `combo_templates.json` template-gating — are removed; `resolve()` and `engine_report()` inject ZERO family edges. Self-mill + extra-turn were already DEFERRED (Phase-3b reverted → #90). Multi-phase, proof-carrying (ADR 0003); the families-deferred re-baseline is the ADR 0003 2026-11-22 entry. |
| Review | **owner-approved 2026-11-19 (relay placement = `produced_by`-only confirmed).** Agent-authored from the approved design (`~/.claude/plans/trippersham/magic-tools/2026-11-19-node-admission-thematic-vs-mechanical/design.md`) + the astra-5.2 drift pressure-test (`drift-pressure-test.md`, spike `spike/83-unified-event-drift`, throwaway). Owner reads this record before Phase 1. |
| Test | Phase 0 (schema): from `plugins/make-magic/pipeline`: `uv run --extra dev pytest pipeline/transforms/strategy/tests/test_relay_schema.py pipeline/transforms/strategy/tests/test_event_ontology.py -q`. Parity gate: `uv run python -m pipeline.transforms.strategy.tests.engine_parity_oracle` — **the P0 SHA triple is UNCHANGED** (no relay families authored, no edges added). Later phases re-baseline under ADR 0003. |
| Code paths | [`derive_card_events.py`](../../pipeline/pipeline/transforms/strategy/derive_card_events.py) (`RelayEntry`, `SEED_MAP`, `derive_card_events`, `validate_seed_map`), [`relay.py`](../../pipeline/pipeline/transforms/strategy/relay.py) (repurposed as the provisional-edge channel, Phase 1; `combo_event_edges` threading), [`strategy_graph.py`](../../pipeline/pipeline/transforms/strategy/strategy_graph.py) (the one walk; SCC; loop-aware admission, Phase 1; the dormant `combo_event_edges` injection seam — passed `None` while families are deferred to #90), [`engine_report.py`](../../pipeline/pipeline/transforms/strategy/engine_report.py) (the superset-of-resolve view; also passes `combo_event_edges=None`), [`reconciliation.py`](../../pipeline/pipeline/transforms/strategy/reconciliation.py) (the Phase-4 metric). The #90 re-add restores `combo_promotion.py` + `data/combo_templates.json` + the `combo_win.unbounded_intermediate_events` liveness filter (removed in the scope reduction) onto the retained `combo_event_edges` seam. |

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

  > **DEFERRED to #90 (scope reduction, owner 2026-11-22).** These 7 families do **not** ship
  > in 0.9.0. Three adversarial reviews (`research/final-review{,-2,-3}.md`) found every corpus
  > false-positive concentrated here: the certified-unbounded set is necessary but **not
  > sufficient** for an executable in-deck loop — it is blind to template pieces the deck lacks,
  > to per-piece quantity, to a commander-only role on a main-deck card, and to a handful of
  > event-mapping errors in the fid adapter. The combo-certified promotion, the
  > template-satisfaction gate below, `combo_promotion.py`, and `data/combo_templates.json` are
  > **removed**; the `combo_event_edges` injection seam is retained (passed `None`) so #90 re-adds
  > a vetted family layer — on top of the #86 executability / magnitude / topology work — without
  > re-plumbing. The paragraph below documents the gate as the #90 re-add must restore it.

  **Template-satisfaction gate (2026-11-22, #83 pre-merge review / #106 Cayth — deferred to #90).** `combo_detect`
  matches a combo on its concrete `uses` cards only and drops the `requires[].template` pieces, so
  a template-partial combo (e.g. "+ any Persist Creature") is DETECTED on a deck that lacks the
  template piece, then win-classified — but the loop is **not executable**. Promotion asserts an
  executable loop, so a win combo is promoted ONLY when the deck satisfies **every** template it
  requires. The requirement survives in the already-landed `variant_id`: its `--<ids>` suffix is
  Commander Spellbook's template-id set (verified byte-equal to `requires[].template.id` across the
  corpus). Each template id maps (in `data/combo_templates.json`) to a predicate over a deck card —
  a keyword/type check (persist/undying), an otag check (copy-clone, counter-increaser), or a
  cmc/permanent/generic-castable check — and is satisfied when any deck card matches; a template id
  with no entry **fails closed** (not promoted). This is a uniform predicate over template data +
  deck cards, no per-deck branch. Cayth's `2034-2744--5` (needs a Persist Creature it lacks) is
  dropped; Marchesa's persist combo (Glen Elendra Archmage + River Kelpie) PASSES and is kept.

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
relays are catalog-static (the 7 families are deferred to #90); spellbook/haiku edges stay
per-deck runtime.

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
  `elect_engine_units`'s `gate_carriers` (default `set(attr_weight)`; the `engine_report` path passes
  the role-matched live set — native carriers plus correct-role matched store gates, see the
  2026-11-22 #114-review note below); it falls back to `nodes` for
  callers where present == admitted (e.g. `loop_aware_nodes`, which already passes all present attrs). A gate does NOT become
  an identity node merely by enabling a relay (loop-aware admission unchanged — a pure lifetrade
  event-cycle carries no attr anchor, so no identity drift). Measured OFF/ON on the 187 corpus:
  exactly 4/170 gauntlet decks gain a recursive lifetrade engine (celes/elenda/karlov/liesa), 0 false
  lifetrade recursions, one-sided decks stay open; ENGINE/RESOLVE byte-identical (no hermetic fixture
  carries an enabler pair), FULL_CORPUS re-baselined `5045f7af…→85eba970…`. `CURATED_EDGE_ROLES` is
  now empty; the `EventEffect` type + the store waterfall remain for haiku/spellbook provisional edges.
- **Phase 3 (built 2026-11-20, DEFERRED to #90 on 2026-11-22 — did NOT ship in 0.9.0; see the
  scope-reduction bullet below): combo-certified promotion of the 7 resource-dependent families**
  (ETB/death-recursion, mana-refund, cast/copy, draw-wheel, untap-mana, counter, landfall).
  *The 36/170 and Class A 34 → 26 figures in this bullet describe what the built-then-deferred
  phase measured; the shipped 0.9.0 state reverts to the Phase-2 tip (Class A 34).* These
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
- **Phase 5 (2026-11-20 behavioral verification; final ship target updated by the 2026-11-22
  deferral below).** The feature is verified end-to-end on the real corpus (lifetrade closes; the
  10 named false circuits stay open; the deferred families stay `static-payoff`) and the whole #83
  change ships as ONE proof-carrying cumulative re-baseline from pre-#83 `main` (ENGINE
  `4d3f5e6d…`, RESOLVE `940c3881…`, FULL_CORPUS `ec917355…`). **What ships in 0.9.0 is the
  framework + lifetrade + metric: the 2026-11-22 family deferral moves the ship target to the
  Phase-2 tip** — ENGINE `80d0116a…`, RESOLVE `3ebb33fd…`, FULL_CORPUS `85eba970…` (moved to
  `f3efdb0d…` by the #115 election-loop fix below; ENGINE/RESOLVE and Class A/B hold), **Class A = 34
  / Class B = 42** — not the Phase-3 tip (`8f45c628…`/`6eed6ba7…`/`17e806fa…`), which was the
  built-then-deferred state. See the ADR 0003 2026-11-20 and 2026-11-22 entries. Version bumped
  0.8.0 → **0.9.0** (lockstep).
- **Scope reduction (2026-11-22): the 7 combo-certified families DEFERRED to #90.** After three
  adversarial pre-merge reviews each found the corpus false-positives concentrated entirely in the
  family layer (Cayth / Ezuri / Shiko / Xenagos + executability / quantity / role / event-mapping
  sub-bugs), the owner deferred all 7 families (§4). The combo-certified promotion is removed:
  `combo_promotion.py`, `data/combo_templates.json`, and `combo_win.unbounded_intermediate_events`
  are deleted, and both public views (`compute_facts`/`resolve()` and `engine_report`) pass
  `combo_event_edges=None`, so **zero** family edge reaches the SCC walk. What ships is Phase 1
  (framework) + Phase 2 (lifetrade relays) + Phase 4 (metric); the fid-adapter data corrections
  (30-pump→null, #107) stay (correct data feeding `combo_win`/the metric) but, without promotion,
  no longer move resolve/corpus output. All three composites revert BYTE-FOR-BYTE to the Phase-2
  (framework+lifetrade) values — ENGINE `8f45c628…→80d0116a…`, RESOLVE `6eed6ba7…→3ebb33fd…`,
  FULL_CORPUS `7b59008f…→85eba970…` (the two per-deck baseline JSONs are byte-identical to the
  Phase-2 commit `8ec1417`); reproduced across PYTHONHASHSEED 0 and 4242. The ~36 combo-certified
  family loops are gone (deferred); **Class A 26 → 34, Class B 42 (unchanged)** — the 8
  family-reconciled decks return to the "loops to author" queue. Framework recursion + lifetrade
  INTACT (Queza closes `{lifegain, life-loss-drain, draw-a-card}`; celes/elenda/karlov/liesa
  reconcile; blink/aristocrat/proliferate P1 loops remain). The 5 named FP decks carry no family
  recursive label. Proof: the ADR 0003 2026-11-22 entry + the drift-ledger "families deferred"
  section. Version stays **0.9.0** (framework + lifetrade is still the breaking change).
- **Election-loop fix (2026-11-22, #115 / PR #114 review): a pure-event-cycle lifetrade loop
  is no longer dropped.** `elect_engine_units` enumerated engine units only for events in
  `ev_prod` (events with an admitted attr producer–consumer pair), but a lifetrade event made
  recursive by the §1 relays alone — its only enablers the sub-threshold gate carriers Sanguine
  Bond (`gain-to-drain`) + Exquisite Blood (`drain-to-gain`) — has no such pair, so it was in the
  recursive SCC yet emitted no unit (a deck whose one loop was that pair returned `engines=[]`).
  The walk now emits a `recursive-engine` unit for each event in `recursive_events - ev_prod`
  using its live gate carriers as evidence. Exactly 3/170 gauntlet decks move — Karlov, Elenda,
  Edgar Markov — each a genuine Sanguine Bond + Exquisite Blood loop with both gate directions
  present; ENGINE + RESOLVE byte-identical, FULL_CORPUS `85eba970…→f3efdb0d…`, Class A/B unchanged
  at 34/42. See the ADR 0003 2026-11-22 (#115) entry.
- **Emission metadata fixes (2026-11-22, #114 review A): 4 defects in the pure-event-cycle
  unit's REPORTED metadata (the unit set, mass, kind, and the mass election are unchanged).**
  (1) **Store-hijack (report path):** the A-emission keyed gate liveness on `set(attr_weight)`,
  which `engine_report` salts with store (haiku) slugs by name+query match — a same-named,
  wrong-role slug could revive a catalog gate and mint a phantom recursive engine. Liveness tests
  a ROLE-MATCHED live set (see the 2026-11-22 #114-review role-matched correction below), threaded
  as `elect_engine_units`'s `gate_carriers` argument; `None` falls back to `set(attr_weight)` for
  the store-free `compute_facts` path. (2) **Hub** = the loop's RETURN-PATH gate (a relay whose `from_event` is inside the
  event's own SCC), not the heaviest live gate. (3) **commander_coupled** counts the commander's
  synthesized relay-gate attrs (`SYNTH_ALWAYS`, dropped by the raw-otag rebuild). (4)
  **formation_mode** gives the producer-only loop its own `recursive-relay-loop` mode (ADR 0014)
  instead of `bipartite`/`terminal-producer`. Exactly 3/170 decks move (Karlov, Elenda, Celes),
  ONLY `formation_mode` + the raw `relay_loop` marker; ENGINE/RESOLVE byte-identical, FULL_CORPUS
  `f3efdb0d…→c85e4ae5…`, Class A/B unchanged at 34/42. See the ADR 0003 2026-11-22 (#114 review A)
  entry + ADR 0014's `recursive-relay-loop` amendment.
- **Role-matched store-carrier liveness (2026-11-22, #114 review — Astra + Fable): the store-hijack
  fix (metadata-fix (1) above) is corrected from NATIVE-ONLY to ROLE-MATCHED.** Freezing gate
  liveness to the native carrier set killed the Queza/Yargle hijacks but ALSO dropped a *legitimate*
  store-backed relay: a correct-role matched haiku gate (e.g. a `draw-to-drain` entry whose role
  `draw-a-card → life-loss-drain` patches a synth-regex miss on damage-on-draw cards) was excluded,
  so its pure-event-cycle loop no longer closed. The predicate is now: **a store entry makes a catalog
  relay gate live iff it matched ≥1 card AND its declared role `(trigger_event, produced_event)` equals
  the catalog relay direction `(from_event, owning_event)`** for a gate of that name (the single
  predicate `relay.catalog_relay_directions`). `identify_engines` threads `native_gate_carriers |
  role_matched_store_gates` as `gate_carriers`, used by BOTH the SCC walk's `gate_present` AND
  `build_relay_augmented_event_edges`'s catalog-relay injection (which previously tested bare
  `ref.gate in nodes`, so a wrong-role store slug ADMITTED into `nodes` as a commander carrier still
  supplied the Queza phantom return leg there). A correct-role matched store edge now closes its loop
  through these role-matched relay edges; a wrong-role or zero-match slug stays dead. Report-path ONLY
  (`resolve()` has no store), so ENGINE/RESOLVE/FULL_CORPUS are byte-identical (`c85e4ae5…` held).
  Regression tests: `test_relay.py::TestWrongRoleStoreSlugDoesNotHijackCatalogRelay` (hijack → 0) +
  `::TestCorrectRoleStoreSlugRestoresLifetradeLoop` (correct role → loop closes), both alongside the
  retained `TestStoreSlugDoesNotHijackCatalogGate`.
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
