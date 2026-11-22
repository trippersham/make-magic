"""Component 3 — the per-deck, per-layer differ + three-class classifier.

The dump-diff machinery is now homed in :mod:`pipeline.transforms.comparison.dump_diff`
(the single shared comparator). This module re-exports it so the reproducibility
harness, the instrument tests, and the ``-m`` CLI keep their import paths.

See :mod:`pipeline.transforms.comparison.dump_diff` for the classification rules
and exit codes.

CLI::

    uv run python -m pipeline.transforms.strategy.tests.repro.semantic_diff a.json b.json \
        --report out.md [--json out.json]
"""

from __future__ import annotations

from pipeline.transforms.comparison.dump_diff import (
    BENIGN,
    CARD_UNIVERSE,
    CLASSES,
    LAYERS,
    SUBSTANTIVE,
    classify_layer,
    diff_dumps,
    exit_code,
    layer_detail,
    main,
    render_report,
)

__all__ = [
    'BENIGN',
    'CARD_UNIVERSE',
    'CLASSES',
    'LAYERS',
    'SUBSTANTIVE',
    'classify_layer',
    'diff_dumps',
    'exit_code',
    'layer_detail',
    'main',
    'render_report',
]


if __name__ == '__main__':
    raise SystemExit(main())
