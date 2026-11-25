# Intake: groom work forward (only when flow is healthy)

The intake valve pulls raw Inbox issues down the funnel. It is the one valve **gated on flow health**: it opens *only when the governor says downstream has pull capacity*, so grooming never log-jams the top. Grooming is an activity the steward performs while issues sit in Inbox — there is no "Groomed" Status.

> Built in Phase 3 — intake delegates to `/managing-backlog` and `/planning`; the override lane delegates to `/planning`, `/executing`, and `/run-empirical-test`. The steward orchestrates these skills; it does not reimplement them. Board mutations around intake ship in propose-mode (decide/act split).

## The intake pipeline

For an Inbox issue, when and only when downstream is under WIP:

1. **Groom** — the steward reads the raw issue and delegates ticket shaping (triage, estimate, the three-level content model, `ready` gating) to `/managing-backlog`. It does **not** write tickets itself.
2. **Propose prioritization** (soft gate: Inbox → Prioritized) — present "is this worth planning now?" for human review. Propose-mode.
3. **Draft a plan** (soft gate: Prioritized → Ready) — invoke `/planning` to turn the design into an execution-ready plan with posture classification; present the *plan* for human review. This is the mid-funnel valve, invoked **only when Ready is under WIP**.
4. **Park at the next soft gate** — or, for an override-eligible item, drive it down the override lane (below).

<consume-not-detect>
**Why delegate every step:** `/managing-backlog` already owns estimation and `ready` gating; `/planning` already owns posture classification. The steward's job is to decide *which* issue enters the pipeline next and *when to stop feeding it* — the governor's capacity test — not to re-derive the estimate or re-classify the posture. Consume the signals these skills emit; do not re-detect them.
</consume-not-detect>

## The override lane

The override lane is the one exemption to propose-mode (SKILL.md primary constraint). For an issue that clears **all four** bars — XS/S estimate, low-risk/reversible (not an `mm-change` protected path/schema/release), zero open questions, downstream under WIP — the steward may **autonomously run the pipeline skills to drive it to In Review** as the first human touchpoint.

- The **work** (running `/planning` → `/executing` → `/run-empirical-test` for diagnosis) is autonomous — doing the work and presenting a result *is* the override lane.
- The **board mutations around it** (the Status moves, any escalation) still start in propose-mode.
- The **lane stops at In Review.** The In Review → Done hard gate has no override; a human merges.

<override-rationale>
**Why only four-bar items, and only to In Review:** the economic rule is `cost(human attention + cost-of-delay) > cost(just doing it)` — true only for a small, reversible, unambiguous item with somewhere to land. For anything larger or riskier, interrupting the human twice up-funnel is cheaper than an autonomous mistake on a protected path. Presenting at In Review (not Done) preserves the human's merge as the final check, exactly as `mm-change` requires.
</override-rationale>

## Starting a working session (two lanes)

| Lane | Who owns the session | How it starts |
|---|---|---|
| **Override / autonomous** | the steward (via a sub-agent) creates and **owns** the omnigent session — the sub-agent is the worker | fully hands-off kickoff (see the `mm-flow-sessions` skill) |
| **Human-driven (normal path)** | the human opens and drives the session | the steward **stages everything** (approved plan on the card, context ready) and only *messages* the session for check-ins |

Because identity is `local`, the steward can message a session the human created — proven empirically (design §12). Driving sessions, reading their history, and the many-to-many session↔issue map all live in the `mm-flow-sessions` skill.

## Do / do not

| Do | Do not |
|---|---|
| Open intake only when the governor confirms downstream pull capacity | Groom because Inbox looks big (see `references/governing.md`) |
| Delegate ticket writing to `/managing-backlog`, planning to `/planning` | Write tickets or classify posture yourself |
| Use the override lane only for all-four-bar items, stopping at In Review | Drive any item through the hard gate, or override a protected-path item |
| Stage the plan on the card for the human-driven lane | Open and drive a human's working session yourself on the normal path |
