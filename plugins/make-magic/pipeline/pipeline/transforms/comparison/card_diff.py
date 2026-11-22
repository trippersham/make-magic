"""Generic card-set diff — the shared card-level comparator.

One implementation of "what moved between two card collections": which identities
were added, removed, changed, or left intact when going from a *before* set to an
*after* set. It is deliberately generic — it diffs two mappings of ``identity →
value`` — so every caller that compares card lists (``audit-decks`` /
``recover-decks`` drift, deck-vs-deck comparison) routes through one core instead
of hand-rolling its own set algebra.

The caller owns the identity choice: key each side by whatever makes two cards
"the same" for its purpose (card name, ``oracle_id``, …) and supply a value whose
inequality means "changed" (the card row, a quantity, a role). The diff never
reorders: ``added`` / ``removed`` / ``changed`` / ``unchanged`` preserve each
side's input iteration order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CardSetDiff[K, V]:
    """The four-way partition of two keyed card sets.

    Attributes:
        added: ``key → after-value`` for keys present only in *after* (gained).
        removed: ``key → before-value`` for keys present only in *before* (lost).
        changed: ``key → (before-value, after-value)`` for keys in both whose
            value differs (the identity stayed, something about it moved).
        unchanged: ``key → value`` for keys in both whose value is equal.
    """

    added: dict[K, V]
    removed: dict[K, V]
    changed: dict[K, tuple[V, V]]
    unchanged: dict[K, V]

    @property
    def is_empty(self) -> bool:
        """True when nothing was added, removed, or changed."""
        return not (self.added or self.removed or self.changed)


def diff_card_sets[K, V](before: Mapping[K, V], after: Mapping[K, V]) -> CardSetDiff[K, V]:
    """Partition two keyed card sets into added / removed / changed / unchanged.

    Both sides are ``identity → value`` mappings the caller has already keyed by
    its chosen notion of card identity. ``added`` is keyed from *after*,
    ``removed`` from *before*; a key in both lands in ``changed`` when its values
    are unequal and ``unchanged`` otherwise. Output order follows input order:
    ``added`` follows *after*'s order, every other bucket follows *before*'s.
    """
    added = {k: after[k] for k in after if k not in before}
    removed = {k: before[k] for k in before if k not in after}
    changed = {k: (before[k], after[k]) for k in before if k in after and before[k] != after[k]}
    unchanged = {k: before[k] for k in before if k in after and before[k] == after[k]}
    return CardSetDiff(added=added, removed=removed, changed=changed, unchanged=unchanged)
