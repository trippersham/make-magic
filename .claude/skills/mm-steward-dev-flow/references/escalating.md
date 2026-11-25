# Escalating: make a stalled or gated card loud

The escalate valve is **always allowed** — it is the one valve the governor never gates, because surfacing a problem is never itself a jam. Escalating **does not move the card**; it makes the card's status loud to the human and asks for a decision.

> Built in Phase 2 — the escalate valve is a decide/act split: the sweep *proposes* a batch of label/assignee/comment writes, a thin idempotent apply-step enacts them, and the apply-step is gated by propose-mode until the action type graduates (staged autonomy). Do not enact escalations directly from the reasoning pass.

## What the valve writes

When a card parks at a gate, or trips `blocked`/`stalled`, the steward proposes — on the ticket itself, because per-card truth lives on the board, not in the roll-up:

1. the board **label** that names the state (`blocked`, `needs-info`, `stalled`);
2. the **assignee** who owns the decision (proposed — assigning a *person* is human-gated, so this ships in propose-mode);
3. **one escalation comment** on the ticket: the human-readable "what this is waiting on," in plain language, plus a **link to the working session** so the human can jump straight in;

then **folds the item into the next roll-up** (`references/rollup.md`) so the human sees it batched, not as a notification.

<board-is-truth>
**Why on the ticket, not only in the roll-up:** the board is the durable, per-card source of truth; the roll-up is a synthesized, disposable view of it. If the escalation comment lived only in the digest, the state would vanish the moment the digest scrolled past. Writing it to the ticket means the human can open the board at any time and see exactly what each card waits on.
</board-is-truth>

## Escalate once; apply pressure in the roll-up

The steward posts **one** escalation comment per escalation event — and then **never re-escalates by appending more comments** to the ticket's history. One still-stalled issue earns one comment, not a growing thread.

<escalate-once-rationale>
**Why pressure lives in the roll-up, not the thread:** a single ticket comment is a durable, one-time "here is what this waits on," and it belongs on the card because per-card truth lives there. But *rising urgency* is a property of the whole backlog over time, not of one ticket, so it belongs where the human reads the backlog as a whole — the steward-session roll-up, where unresolved escalations rise in prominence over successive standups (`references/rollup.md`). Re-commenting on the ticket instead would bury the one durable record under repetition and train the human to mute the thread, destroying the signal the escalation exists to raise.
</escalate-once-rationale>

## Escalate is not expedite

These two are constantly confused; keep them distinct.

| | Escalate | Expedite |
|---|---|---|
| **What it is** | surfacing — "a human needs to decide / prioritize this" | a priority override — jump the queue |
| **Mechanism** | label + assignee + one ticket comment + roll-up entry | the `expedite` label |
| **Effect on WIP** | none — the card does not move | **overrides WIP limits** |
| **Cap** | none (surfacing is cheap) | **hard-capped at one in flight** |
| **Who decides** | the steward surfaces; the human decides | the human sets `expedite`; the steward honors the cap |

<escalate-vs-expedite-rationale>
**Why the one-in-flight expedite cap:** an expedite that overrides WIP is a deliberate hole in the anti-jam rule. One hole is a sanctioned exception; several at once is just "no WIP limit," which reintroduces the jam the governor exists to prevent. Escalation has no cap because it changes nothing about flow — it only asks.
</escalate-vs-expedite-rationale>

## Where an escalation routes

- **"Needs a human decision" / ambiguous** → the escalation target is `/collaborating` (the rae-flow skill for interactive, judgment-call work).
- **Multiple humans** → single human today; `.github/areas.json` (the repo's reserved phase-2 reviewer auto-assign map) is the routing source when it is not. *(> Built in a later phase; `.github/areas.json` does not exist yet — do not reference it as present.)*

## Do / do not

| Do | Do not |
|---|---|
| Escalate freely — it is never gated by flow health | Move the card to a different Status as part of escalating |
| Post one escalation comment (with a session link), then batch it into the roll-up | Fire a separate notification per escalation |
| Let unresolved pressure rise in the roll-up over successive standups | Re-escalate a still-stalled issue by appending more comments to its ticket thread |
| Honor the one-in-flight expedite cap | Let the steward set `expedite` itself to jump a queue (that is human-gated re-prioritization) |
| Pause the aging clock while `blocked`/`needs-info` is set | Escalate on calendar age before the debounced stall signal fires |
