# Governing: weighted task selection & jam detection

How the steward decides *what to do next* on each sweep. The governor adapts to board pressure — it never treats every ticket equally, and it never grooms the top while the bottom is jammed. All thresholds named here are fields in `.claude/steward.yml`; this reference gives the *logic*, the config gives the *numbers*.

> Built in Phase 2 — the governor runs over the Phase-1 measure views that the `mm-flow-metrics` skill owns. Until those views exist it has no inputs. Do not invent threshold numbers; read them from `.claude/steward.yml` once it is authored (Phase 0).

## The selection loop

On each sweep the steward chooses an action weighted by where the board hurts most, not by backlog size:

1. **Read board state + derived views** (current Status, labels, per-stage counts, aging) from the measure layer (`mm-flow-metrics`).
2. **Find the bottleneck stage** — the one fullest-relative-to-its-WIP-limit, or slowest-draining (Theory of Constraints). The bottleneck is the constraint; everything upstream of it should *slow down*, not speed up.
3. **Act on the bottleneck, not the biggest pile.** If Ready-for-Execution is at limit, the constraint is downstream — escalate capacity, do **not** add more ready work. A full Inbox behind a jammed bottom is *correct*, not a problem to groom away.
4. **Open the intake valve only when downstream has pull capacity** (see below). This is the single most important anti-jam rule.

## Per-stage WIP limits

Each Status has an integer cap (config). WIP limits exist to keep the number of things *in flight* small enough that each one actually moves.

| Do | Do not |
|---|---|
| Hold grooming when the downstream stage is at its WIP limit | Groom intake forward because Inbox looks big |
| Let Inbox grow behind a jammed bottleneck | "Balance" the columns by pushing work down into a full stage |
| Treat `expedite` as the one sanctioned WIP override, capped at one in flight | Let multiple expedites bypass limits at once |

**Why:** pushing work into a stage that is already at its limit does not increase throughput — it increases work-in-progress, lengthens cycle time for *everything* in that stage, and hides the real constraint. Limiting WIP is how a Kanban system makes flow faster, not slower.

## The stall signal: aging WIP

A card stalls when it sits **in its current Status** past that stage's SLE (service-level expectation) — *not* when it is simply old.

- **Measure time-in-*current*-stage**, not calendar age. A three-week-old card that entered Ready yesterday is not stalled.
- **Debounce: require ≥2 consecutive aging signals** before acting. One noisy sample is not a stall.
- **Pause the clock while `blocked` or `needs-info` is set.** Waiting on an answer is not stalling; the `mm-flow-metrics` skill holds the interval-subtraction math.

<aging-rationale>
**Why debounce + pause:** without them the steward fires false stalls — it escalates a card the moment a sweep catches it mid-transition, or nags about a card that is legitimately parked on a human answer. False stalls train the human to ignore the roll-up, which defeats the whole point of a low-noise digest.
</aging-rationale>

## Jam signals by level

The funnel jams in three distinct places; each has a different correct response.

| Level | Signal | Correct response |
|---|---|---|
| **Top** | ungroomed Inbox rising **while** downstream is at-limit | **Hold grooming.** The top is not the problem; the bottleneck is downstream. |
| **Mid** | Ready-for-Execution over WIP, or aging unused | The constraint is downstream — **escalate capacity**, do not add ready work. |
| **Bottom** | aging In-Progress / In-Review past SLE, stale branches, abandoned sessions | **Escalate the specific item** (escalate valve) and run hygiene on finished work. |

<red-flags>
If you catch yourself thinking:
- "Inbox has 40 items, I should groom them" — **stop.** Check the bottleneck first; grooming a jammed board makes it worse.
- "This card is old, I'll escalate it" — **stop.** Age ≠ stall. Measure time-in-stage, debounce, and confirm the clock isn't paused on `blocked`.
- "Ready is full, let me plan more tickets to keep the queue warm" — **stop.** A full Ready stage means the constraint is *downstream*; adding ready work deepens the jam.
</red-flags>

## Decision tree

```text
Is the bottleneck stage at/over its WIP limit?
  yes → is it the bottom (In Progress / In Review)?
          yes → escalate the aging item + run hygiene
          no  → escalate capacity downstream; hold intake
  no  → does an Inbox item clear the override bar AND downstream has pull?
          yes → open intake valve (groom → prioritize → plan → park/override)
          no  → park at the soft gate + escalate for human input
```
