"""Shared deck-comparison primitives — the ONE home for deck comparison.

So nothing downstream grows a parallel comparator, every card-list and
semantic-dump comparison routes through this package:

* :mod:`.card_diff` — the generic card-set diff (added / removed / changed /
  unchanged over two keyed card sets), consumed by the ``audit-decks`` /
  ``recover-decks`` drift path and any deck-vs-deck comparison.
* :mod:`.dump_diff` — the generic graph-level semantic-dump diff + its
  three-class classifier, re-exported by the reproducibility instrument.
"""

from __future__ import annotations

from pipeline.transforms.comparison.card_diff import CardSetDiff, diff_card_sets
from pipeline.transforms.comparison.dump_diff import (
    LAYERS,
    classify_layer,
    diff_dumps,
    exit_code,
    layer_detail,
    render_report,
)

__all__ = [
    'LAYERS',
    'CardSetDiff',
    'classify_layer',
    'diff_card_sets',
    'diff_dumps',
    'exit_code',
    'layer_detail',
    'render_report',
]
