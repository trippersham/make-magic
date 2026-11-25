# Roll-up: how the human is told

The roll-up is the steward's one output to the human: a **low-noise, high-signal, actionable synthesis** delivered in the standing steward session. It is deliberately **not** a mirror of GitHub's notification firehose.

> Built in Phase 2 — the digest is driven by a `/loop` or `/schedule` heartbeat. The synthesis is skill content (here); the heartbeat and the escalation apply-step are built in Phase 2.

## When it fires

- On a **configured cadence** (e.g. morning standup / after lunch / EOD — set in `.claude/steward.yml`).
- **On demand** when the human says "standup me" (or asks "what's blocked", "what's waiting on me", "is work flowing").

The same `/loop` or `/schedule` heartbeat drives the background flow sweep and the cadenced digest.

## What it looks like

A few lines, ordered by what the human must *act on* first:

> "3 cards wait on you: prioritize #45; approve the plan for #50; #38 is blocked on a Scryfall schema answer. Flow: Ready-for-Execution at limit (3/3) — intake paused. 1 finished session archived (#31)."

Shape: **what waits on the human** (the action list) → **flow health** (where the funnel is jammed and what the governor did about it) → **what the steward did autonomously** (hygiene, override-lane results, auto-unmapped sessions).

## The rules

<rollup-rationale>
**Why low-noise, not a notification mirror:** GitHub already sends a notification per event. A second firehose adds nothing and buries the two or three things that actually need a human. The roll-up's entire value is that the human can trust it to surface *only* what requires them — so it must batch, dedupe, and lead with the action list. A noisy roll-up trains the human to ignore it, which collapses the steward's only communication channel.
</rollup-rationale>

- **Per-card truth stays on the board** (label + assignee + one escalation comment, from `references/escalating.md`); the session *synthesizes* it. The roll-up is a disposable view, not the store.
- **Unresolved escalations rise in prominence over successive standups.** This is where escalating pressure is applied — *not* in ticket comment threads, which get exactly one comment per escalation event (`references/escalating.md`). An item the human has not acted on climbs the action list each roll-up until it is the loudest thing in the digest.
- **Batch everything.** Group related moves and escalations into the one periodic digest; never fire a message per event.
- **Always surface auto-unmapped sessions** ("N new ad-hoc sessions auto-unmapped") so the human can correct a reconciliation miss directly (the session map lives in the `mm-flow-sessions` skill).
- **Name the flow state, not just the cards** — "Ready at limit, intake paused" tells the human *why* nothing new is moving, which is itself signal.

<rising-pressure-rationale>
**Why the roll-up carries the pressure, not the ticket:** the ticket gets one durable escalation comment and no more — repetition there is noise that buries the record. Urgency instead accrues across the backlog over time, so the only place it can *grow* without adding noise is the synthesized digest the human reads whole. Letting a stalled item climb the action list each standup is the steward's lever for making an ignored decision progressively impossible to ignore, without ever touching the ticket thread again.
</rising-pressure-rationale>

## Do / do not

| Do | Do not |
|---|---|
| Lead with the human's action list; batch the rest | Mirror GitHub's per-event notifications |
| Let unresolved escalations climb the action list each standup | Apply escalating pressure by re-commenting on the ticket thread |
| Synthesize from board truth; keep the digest disposable | Store state only in the digest (it scrolls away) |
| Surface auto-unmapped sessions for human correction | Hide the steward's autonomous reconciliation choices |
| Fire on cadence and on "standup me" | Interrupt the human per escalation outside the cadence |
