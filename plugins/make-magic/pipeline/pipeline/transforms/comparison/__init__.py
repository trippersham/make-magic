"""Shared deck-comparison primitives — the ONE home for deck comparison.

So nothing downstream grows a parallel comparator, every card-list and
semantic-dump comparison routes through this package:

* :mod:`.card_diff` — the generic card-set diff (added / removed / changed /
  unchanged over two keyed card sets), consumed by the ``audit-decks`` /
  ``recover-decks`` drift path and any deck-vs-deck comparison.
* :mod:`.dump_diff` — the generic graph-level semantic-dump diff + its
  three-class classifier, re-exported by the reproducibility instrument.
* :mod:`.identity_diff` — layer ① archetype-identity vectors + cosine / L1 / the
  ranked reweight gap over two semantic dumps.
* :mod:`.execution_diff` — layer ③ execution (play-difference) deltas: degree
  axes + power profile + mana-curve.
* :mod:`.cache` — the shared, content-addressed ``semantic_dump`` cache (extends
  the engine cache's key scheme), read by the card-recommendation effort too.
* :mod:`.deck_semantics` — the lake-coupled resolve bridge: a deck reference →
  a cached :class:`~.cache.DeckDump`.
"""

from __future__ import annotations

from pipeline.transforms.comparison.cache import DeckDump, SemanticDumpCache
from pipeline.transforms.comparison.card_diff import CardSetDiff, diff_card_sets
from pipeline.transforms.comparison.deck_semantics import (
    LakeContext,
    cached_deck_dump,
    resolve_reference,
)
from pipeline.transforms.comparison.dump_diff import (
    LAYERS,
    classify_layer,
    diff_dumps,
    exit_code,
    layer_detail,
    render_report,
)
from pipeline.transforms.comparison.execution_diff import (
    ExecutionDiff,
    deck_curve,
    diff_execution,
)
from pipeline.transforms.comparison.identity_diff import (
    IdentityDiff,
    diff_identity,
)

__all__ = [
    'LAYERS',
    'CardSetDiff',
    'DeckDump',
    'ExecutionDiff',
    'IdentityDiff',
    'LakeContext',
    'SemanticDumpCache',
    'cached_deck_dump',
    'classify_layer',
    'deck_curve',
    'diff_card_sets',
    'diff_dumps',
    'diff_execution',
    'diff_identity',
    'exit_code',
    'layer_detail',
    'render_report',
    'resolve_reference',
]
