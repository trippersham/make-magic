# 0027 Card strength is popularity + heuristics, never win-rate — reported with its basis

| Field | Value |
|---|---|
| Status | Accepted. Ships in the `comparison/` package (Phase 2 of deck-comparison-primitives, branch `feat/comparison-p2`): the two strength axes, the `StrengthRecord` DTO's honest-limit fields, the promote-only `game_changer`/`combo` flags, and the value-density corrective. |
| Review | Agent-authored from the owner-approved design (`~/.claude/plans/trippersham/magic-tools/2026-11-19-deck-comparison-primitives/design.md` §4, §9) + two research briefs (`research/deck-agnostic-strength-research.md`, `research/attribute-event-value-research.md`). The value-density prior table carries an **owner-ratification item** — see §4 and the branch outbox Open Questions. |
| Test | From `plugins/make-magic/pipeline`: `uv run --extra dev pytest -k "strength or value_density or marginal" -q`. The popularity-bias-correction acceptance is `test_comparison_strength.test_value_density_corrects_popularity_bias` + `test_comparison_value_density.test_repeatable_draw_no_longer_wrong_signed`. |
| Code paths | [`comparison/contracts.py`](../../pipeline/pipeline/transforms/comparison/contracts.py) (`StrengthRecord`/`StrengthRelative`/`StrengthAgnostic`/`MarginalParticipation`), [`comparison/strength.py`](../../pipeline/pipeline/transforms/comparison/strength.py) (`strength`, `marginal_participation`), [`comparison/strength_fetch.py`](../../pipeline/pipeline/transforms/comparison/strength_fetch.py) (Scryfall cache), [`comparison/value_density.py`](../../pipeline/pipeline/transforms/comparison/value_density.py) + [`data/attribute_values.json`](../../pipeline/pipeline/transforms/comparison/data/attribute_values.json), authored by [`tools/fit_attribute_values.py`](../../pipeline/tools/fit_attribute_values.py). |

## Context

The card-recommendation effort asked this package for a per-card **strength** score so a
role slot with many interchangeable candidates can be ranked. The honest problem: **no
per-card EDH win-rate data exists anywhere.** EDHREC measures *play* (popularity), not
*performance*; there is no Commander equivalent of 17Lands' per-card GIH-WR. Every signal we
can actually get — `edhrec_rank`, inclusion rate, price, `game_changer`, combo membership,
an attribute value-per-mana model — is popularity- or heuristic-based. A score that *looked*
like a quality number would mislead every downstream consumer into treating a popularity
signal as a quality one.

The spike (`research/spike/FINDINGS.md`) also found the three proxies **disagree**: on the
Wakanda pair, popularity favored the precon's additions, value-per-mana favored the cuts,
and commander synergy favored the precon — three defensible "more powerful" answers. There
is no single true ordering to collapse them into.

## Decision

**Strength is reported as popularity + heuristics, explicitly, with its basis on the wire —
never as a single authoritative "power" number, and never as win-rate.**

### 1. Two orthogonal axes, never summed

`StrengthRecord` carries `strength_relative` (axis A — does this card serve THIS deck:
engine participation + commander-conditioned EDHREC synergy) and `strength_agnostic` (axis B
— of the plan-serving cards, which is strongest). The consumer gates/weights axis B by axis
A; it never reads one scalar. There is deliberately **no aggregate `power`/`score` field**
on the record (a schema test asserts its absence), because the three proxies can disagree
and did.

### 2. Honest-limit flags are first-class, always-serialized fields

Not a docstring the caller may skip: `basis='popularity-not-winrate'` states the limit on
every record; `proxies_may_disagree=True` is structurally always true; `rank_known` is
**derived** from `edhrec_rank` by a validator so it can never drift. A missing `edhrec_rank`
(a banned card, or a brand-new crossover set) is **UNKNOWN, not weak** — `rank_known=False`
means unranked, and the consumer leans on the flags and value-density instead of demoting.

### 3. `game_changer` and combo are ASYMMETRIC, promote-only

Both are present ⇒ promote, absent ⇏ demote. They are flags, never weights in an additive
sum: `game_changer` is an official WotC high-power bit (~53 cards) and combo membership is a
win-con-participation bit — each can only ever *raise* a card. Folding either into a score as
a weight would let its absence silently dock a card that simply is not flagged.

### 4. Value-density is the popularity-free corrective — a HYBRID, with a surfaced modeling call

`edhrec_rank` is a popularity signal and a global one (not role-conditioned). The
value-density corrective, `Σ(role-scoped attribute value) ÷ (MV + 1)` (Chapin's *Rate =
Effect / Cost*), answers "how much engine-relevant value per mana" independent of play-rate,
and ranks cheap-premium underplayed draw (Mystic Remora) above ubiquitous-but-mediocre draw
(Commander's Sphere) for the draw slot.

The attribute→value weights (`attribute_values.json`) are **reference data**, authored
offline (the runtime is pure table-lookup, no numpy). They are a **HYBRID**: Chapin theory
priors in mana-equivalent units, nudged (±30%) by a sign-AGREEING MV-controlled ridge of
`−log(edhrec_rank)` on attribute dummies. **Pure-data fitting is not shippable** — it
produced both exploded magnitudes (an MV-bucket scaling artifact) and **genuinely
sign-flipped coefficients** on enabler/interaction attributes (`sacrifice-outlet` −40,
`utility-mana-rock` −59, `repeatable-draw` negative): the documented collinearity /
interaction / popularity confounds (an enabler reads weak against a play-rate proxy because
it is worthless without its payoff). Where the ridge sign **contradicts** the theory prior,
the authoring script **defers to the prior** and the disagreement is recorded in the table's
`meta.data_theory_disagreements` (13 attributes) and **surfaced to the owner for
ratification** (the branch outbox Open Questions): the choice to treat these as
theory-authoritative — rather than pursue group-lasso / explicit interaction terms / an
inclusion-rate target — is the one open modeling decision, made conservatively and visibly
rather than silently picked.

**Owner ratification (2026-11-22):** theory-prior-dominance on the 13 contested attributes is
**accepted** as the shipped default; the data-louder alternatives (group-lasso / interaction
terms / EDHREC inclusion-rate target) are deferred as an optional fast-follow, to be revisited
only if the behavioral rankings (P6) read wrong. The shipped `attribute_values.json` stands.

### 5. Strength is OWNED here; the recommendation side embeds the DTO verbatim

Ranking lives in `comparison/`; the recommendation package generates candidates and embeds
`StrengthRecord` into `Rationale.strength` by a pointer — one model, no fork. The DTO is
identity-free (`strength()` returns `dict[oracle_id, StrengthRecord]`); all joins are by
**oracle_id, never name**.

## Consequences

- A consumer can never mistake strength for win-rate or read one "power" number — the record
  structurally refuses to provide one, and says so on the wire.
- UNKNOWN crossover cards are never silently treated as weak.
- The value-density weights are auditable and regenerable (`tools/fit_attribute_values.py`),
  and the one modeling judgment (theory-over-data on sign conflicts) is recorded in the data
  and surfaced, not buried.
- Follow-up: if make-magic's own simulation corpus ever grows a per-card GIH-WR analog, it
  becomes a real quality axis to add alongside (not replace) these popularity/heuristic ones.
