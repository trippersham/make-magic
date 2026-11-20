"""Internal engine⟷combo reconciliation metric (ADR 0026 §5).

A pipeline QUALITY metric, never user-facing: the per-corpus-run loop-disagreement
set between the two independent closure signals the strategy engine already computes
for every deck.

* **Class A** — a deck whose combo detector reports a WIN (``combo_win.walk_is_win``,
  surfaced on the record as ``n_win_combos > 0``) but whose engine graph sees NO
  recursive loop: no ``recursive-engine`` unit AND no closing commander loop. These
  are the "loops to author" queue (ADR 0022's self-healing direction) — a real win
  the engine's loop model has not yet captured.
* **Class B** — a deck whose engine graph sees a loop (a ``recursive-engine`` unit OR
  a closing commander loop) but whose combo detector reports NO win.

This is the **dumb honest diff** of the two signals (ADR 0026 §5): it REUSES the
``combo_win`` verdict (``n_win_combos``) and the elected engine units
(``facts['engines']['engine_units']``) exactly as ``resolve()`` already computed them.
There is NO new ``win_kind`` predicate and NO scoring. The metric only READS existing
record fields, so it adds ZERO fields to the ``resolve()`` record and structurally
cannot move any engine/resolve output — it is a pure, after-the-fact reader.

It is emitted into the corpus-run summary/log (:func:`.run._summary`), NEVER into a
``resolve()`` record. It measures model error today and is the future trigger for
inference-based self-healing: the Class-A set is the queue of loops to author
(``research/class-a-census.md`` in the #83 plan dir hand-verified it to 26 decks at
the Phase-3 tip — 23 genuinely-terminal-or-unmodelled plus the 3 self-mill/GY decks
that Phase-3b reconciled and the 3b revert restored).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ReconciliationMetric:
    """The engine⟷combo loop-disagreement over one corpus run (ADR 0026 §5).

    ``class_a`` / ``class_b`` are the disagreeing decks' labels, sorted for
    determinism; a slug shared by two distinct builds appears once per build (the
    count is deck-accurate, never slug-deduplicated).
    """

    #: Decks with a combo win but no engine loop — the "loops to author" queue.
    class_a: tuple[str, ...]
    #: Decks with an engine loop but no combo win.
    class_b: tuple[str, ...]
    #: Total decks the metric scanned (the corpus-run size).
    n_decks: int

    @property
    def n_class_a(self) -> int:
        return len(self.class_a)

    @property
    def n_class_b(self) -> int:
        return len(self.class_b)


def _deck_label(record: dict) -> str:
    """A stable human key for a deck record: its ``name``, else its first commander."""
    name = record.get('name')
    if name:
        return str(name)
    commanders = record.get('commanders') or []
    return str(commanders[0]) if commanders else '<unnamed>'


def _combo_reports_win(record: dict) -> bool:
    """The combo detector's WIN signal — at least one walk-certified win combo.

    ``n_win_combos`` counts ``[c for c in detected if walk_is_win(c)]`` (see
    ``strategy_graph.compute_facts``), so this is exactly ``combo_win``'s verdict for
    the deck, read off the record with no re-detection.
    """
    return bool(record.get('n_win_combos'))


def _engine_units(record: dict) -> dict:
    """The elected engine-units payload, defensively (``{}`` when absent)."""
    engines = (record.get('facts') or {}).get('engines') or {}
    return engines.get('engine_units') or {}


def _engine_sees_loop(record: dict) -> bool:
    """The engine's loop signal — a ``recursive-engine`` unit OR a closing commander loop.

    This is the symmetric complement of the combo win signal: the census defines
    Class A as a win with neither a recursive unit nor a closing commander loop, so
    "the engine sees a loop" is precisely the disjunction of those two engine signals.
    """
    units = _engine_units(record)
    if any(unit.get('kind') == 'recursive-engine' for unit in units.get('engines', ())):
        return True
    loop = units.get('commander_loop')
    return bool(loop and loop.get('closes'))


def reconciliation_metric(records: Iterable[dict]) -> ReconciliationMetric:
    """Compute the Class-A / Class-B loop-disagreement over corpus-run records.

    *records* are ``resolve()`` records (e.g. from ``run.recalc_corpus``). The metric
    reads only ``n_win_combos`` and ``facts['engines']['engine_units']`` — never a new
    field — so it cannot perturb any engine or resolve output.
    """
    records = list(records)
    class_a: list[str] = []
    class_b: list[str] = []
    for record in records:
        win = _combo_reports_win(record)
        loop = _engine_sees_loop(record)
        if win and not loop:
            class_a.append(_deck_label(record))
        elif loop and not win:
            class_b.append(_deck_label(record))
    return ReconciliationMetric(
        class_a=tuple(sorted(class_a)),
        class_b=tuple(sorted(class_b)),
        n_decks=len(records),
    )


def format_reconciliation(metric: ReconciliationMetric) -> str:
    """Render the metric for the corpus-run summary/log (ADR 0026 §5, summary-only).

    Names the Class-A decks (the "loops to author" queue); Class B is counted only, as
    its decks are legitimately loop-bearing non-wins (value engines without a combo
    kill), not an authoring backlog.
    """
    lines = [
        '  reconciliation (engine<->combo loop-disagreement; ADR 0026 §5, summary/log-only, NOT in resolve()):',
        f'    Class A (combo win, engine sees no loop) = {metric.n_class_a}  [the "loops to author" queue]',
        f'    Class B (engine loop, no combo win)      = {metric.n_class_b}',
    ]
    lines.extend(f'    A: {name}' for name in metric.class_a)
    return '\n'.join(lines)
