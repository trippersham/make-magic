"""Combo-certified relay promotion — the 7 resource-dependent families (ADR 0026 §4).

Issue #83 Phase 3. The unified event graph (ADR 0026) closes a within-card loop by
reading event→event relay edges. For the one clean family (lifetrade) those edges
are authored as catalog ``RelayEntry`` gate-queries (Phase 2). The other **seven**
families are resource/repeatability-dependent — ETB/death-recursion, mana-refund,
cast/copy, draw-wheel, untap-mana, counter, landfall — and the drift pressure-test
proved that **naive gate queries manufacture false circuits** for them: the ADR-0009
closed query vocabulary can match the cards but cannot express the net-mana /
repeatability / zone arithmetic that edge validity needs (Grim Monolith nets -1 mana
yet a gate matches it as an untap↔mana edge; a one-shot copy like Narset's Reversal
matches a cast/copy edge though it never recurs itself).

So these seven families get their event→event edges by **combo-certified promotion**:
the engine graph gains a family's edge ONLY from a loop ``combo_win`` has ALREADY
certified unbounded (:func:`.combo_win.unbounded_intermediate_events`). This reuses the
combo-detector's structural ground truth (ADR 0016) as the liveness filter — it is
correct by construction, because a family edge ``(a, b)`` is admitted only when BOTH
its endpoint events are certified unbounded **in the same combo**. A false carrier's
event is never certified unbounded (Grim Monolith's untap routes ``oneshot``, a finite
copy's ``spell-on-stack`` routes ``oneshot``), so its family edge can never fire — the
Grim-Monolith / one-shot-copy / token-ETB-false-draw circuits stay structurally open.

These are **per-deck runtime provisional edges**, not catalog-static relays: a deck
earns them from the combos it actually contains. They are injected into the one SCC
walk (``strategy_graph.recursive_event_set``) alongside the catalog lifetrade relays;
the SCC then closes the genuine loop so the event's engine unit reports
``recursive-engine``. The promotion only reclassifies the *kind* of an engine unit that
already exists (an event with in-deck producer AND consumer attrs); it never admits a
new node, so there is zero identity/strategy/community drift — only ``facts['engines']``
moves. (Combo detection itself — ``combos_in_deck``/``combo_win_verdict`` — is NOT
re-run here; this module consumes its already-computed output. Deck-specific combo
knowledge lives in the lake's combo data, never as a branch in this engine code.)
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

from pipeline.transforms.strategy.combo_win import unbounded_intermediate_events

_COMBO_TEMPLATES_PATH = Path(__file__).resolve().parent / 'data' / 'combo_templates.json'

#: The `--<ids>` suffix of a Commander Spellbook variant_id encodes the required
#: TEMPLATE ids (``2034-2744--5`` requires template 5; ``2034-5662--5--195`` requires
#: 5 and 195). This matches the raw variant's ``requires[].template.id`` set exactly
#: (verified 0 mismatches across the corpus), so the already-landed variant_id carries
#: the template requirement that ``combo_detect`` dropped at normalization.
_TEMPLATE_SUFFIX_RE = re.compile(r'--(.+)$')

_PERMANENT_TYPES: tuple[str, ...] = ('Creature', 'Artifact', 'Enchantment', 'Planeswalker', 'Battle', 'Land')


@cache
def _load_combo_templates() -> dict[int, dict]:
    """Load the template-requirement predicates (template id -> predicate spec).

    Keyed by int template id; the leading ``_doc`` key and any other non-numeric
    key are ignored. A template id absent from this map is NOT expressible and
    fails closed (see :func:`combo_templates_satisfied`).
    """
    with _COMBO_TEMPLATES_PATH.open() as f:
        raw = json.load(f)
    return {int(k): v['predicate'] for k, v in raw.items() if k.isdigit()}


def template_ids(variant_id: str) -> list[int]:
    """The Commander Spellbook template ids a variant requires, from its id suffix.

    Returns ``[]`` when the variant names no template (no ``--`` suffix).
    """
    m = _TEMPLATE_SUFFIX_RE.search(variant_id or '')
    if not m:
        return []
    return sorted(int(tok) for tok in m.group(1).split('-') if tok)


def _generic_castable(mana_cost: str) -> bool:
    """A non-empty mana cost with no colored (WUBRG) pip — castable with generic mana."""
    return bool(mana_cost) and not any(ch in mana_cost for ch in 'WUBRG')


def _card_satisfies(predicate: dict, card: dict) -> bool:
    """Does one deck card satisfy a template predicate (ALL present conditions hold)?"""
    kw_any = predicate.get('keyword_any')
    if kw_any is not None and not (set(kw_any) & set(card.get('keywords') or [])):
        return False
    type_substr = predicate.get('type_substr')
    if type_substr is not None and type_substr not in (card.get('type_line') or ''):
        return False
    otag_any = predicate.get('otag_any')
    if otag_any is not None and not (set(otag_any) & set(card.get('otags') or [])):
        return False
    max_cmc = predicate.get('max_cmc')
    if max_cmc is not None and (card.get('cmc') or 0) > max_cmc:
        return False
    if predicate.get('permanent'):
        tl = card.get('type_line') or ''
        if 'Instant' in tl or 'Sorcery' in tl or not any(t in tl for t in _PERMANENT_TYPES):
            return False
    return not (predicate.get('generic_castable') and not _generic_castable(card.get('mana_cost') or ''))


def template_satisfied(template_id: int, cards: list[dict]) -> bool:
    """Is a single template requirement satisfiable by the deck's cards?

    Expressible (in :data:`combo_templates.json`) iff ANY deck card satisfies its
    predicate; an inexpressible template id fails closed (``False``), since
    promotion asserts an executable loop and must never certify a piece it cannot
    confirm the deck holds.
    """
    predicate = _load_combo_templates().get(template_id)
    if predicate is None:
        return False
    return any(_card_satisfies(predicate, c) for c in cards)


def combo_templates_satisfied(variant_id: str, cards: list[dict]) -> bool:
    """Does the deck hold a card for EVERY template this win combo requires?

    A win combo with no template requirement is vacuously satisfied. A combo whose
    required template piece the deck lacks (Cayth's missing Persist Creature, #83
    BLOCKER 1) — or whose template this engine cannot express — is NOT satisfied,
    so :func:`combo_certified_event_edges` drops it before promotion. This is the
    uniform executability predicate over template data + deck cards (no per-deck
    branch): a template-partial combo detected on its concrete pieces alone is
    promoted ONLY when its template is actually present, never on the detection
    signal that ``combo_detect`` leaves template-blind.
    """
    return all(template_satisfied(tid, cards) for tid in template_ids(variant_id))


#: The seven resource-dependent relay families as declarative event→event edge rules.
#: Each rule is ``(family, (event_a, event_b))``: an undirected relay between the two
#: events, wired as a 2-cycle (both directions) when BOTH endpoints are certified
#: unbounded in one combo. Edges only — zero new events (every carrier already exists in
#: the 33-event catalog, per ``loop-coverage-audit.md`` §4). The pairs come directly from
#: that audit's §3 family inventory:
#:
#: * ETB/death-recursion — the aristocrat loop ``creature-enters ↔ dies/sacrificed`` (via
#:   recursion-from-graveyard; the recursion event is the mechanism, the ETB and the
#:   death/sac are the certified-unbounded endpoints).
#: * mana-refund — the ritual engine ``mana-produced ↔ spell-cast`` (produce mana, cast
#:   the refunder, produce again).
#: * cast/copy — magecraft/storm ``spell-cast ↔ spell-on-stack ↔ spell-copied`` (the full
#:   pairwise triangle among whichever of the three the combo makes unbounded).
#: * draw-wheel — ``draw-a-card ↔ discard`` (loot) and ``draw-a-card ↔ untap`` (untap-to-
#:   redraw).
#: * untap-mana — the untapper loop ``untap ↔ mana-produced``.
#: * counter — proliferate/untap counter loop ``counter-placed ↔ untap``.
#: * landfall — bounce-land recursion ``land-enters ↔ permanent-leaves`` and the mana a
#:   recurred land taps for ``land-enters ↔ mana-produced``.
#:
#: Because EVERY rule requires both endpoints certified unbounded, the whole classifier is
#: one uniform predicate (:func:`certified_family_pairs`); there is no per-family branch.
FAMILY_RELAY_EDGES: tuple[tuple[str, tuple[str, str]], ...] = (
    ('etb-death-recursion', ('creature-enters-battlefield', 'permanent-dies')),
    ('etb-death-recursion', ('creature-enters-battlefield', 'permanent-sacrificed')),
    ('mana-refund', ('mana-produced', 'spell-cast')),
    ('cast-copy', ('spell-cast', 'spell-on-stack')),
    ('cast-copy', ('spell-cast', 'spell-copied')),
    ('cast-copy', ('spell-on-stack', 'spell-copied')),
    ('draw-wheel', ('draw-a-card', 'discard')),
    ('draw-wheel', ('draw-a-card', 'untap')),
    ('untap-mana', ('untap', 'mana-produced')),
    ('counter', ('counter-placed', 'untap')),
    ('landfall', ('land-enters-battlefield', 'permanent-leaves-battlefield')),
    ('landfall', ('land-enters-battlefield', 'mana-produced')),
)


def certified_family_pairs(unbounded: set[str]) -> set[tuple[str, str]]:
    """The relay family pairs one combo earns, given its certified-unbounded events.

    A family edge ``(a, b)`` fires iff BOTH ``a`` and ``b`` are in *unbounded* — i.e.
    the one combo makes BOTH endpoint events infinite. This is the structural W1
    guarantee: a false carrier (Grim Monolith, a one-shot copy, a token maker with no
    real draw return) never certifies both endpoints unbounded, so its family edge is
    never admitted. Returns the canonical ``(a, b)`` pairs (``a``/``b`` in the order
    they appear in :data:`FAMILY_RELAY_EDGES`); the caller wires each as a 2-cycle.
    """
    return {edge for _family, edge in FAMILY_RELAY_EDGES if edge[0] in unbounded and edge[1] in unbounded}


def combo_certified_event_edges(win_combos: list[dict], cards: list[dict]) -> list[tuple[str, str]]:
    """Per-deck combo-certified event→event edges for the one SCC walk.

    *win_combos* is every WIN combo detected in the deck (the walk's
    ``combo_win_verdict`` already decided win-ness; this consumes that, it does not
    re-detect), each a dict carrying its ``variant_id`` and resolved ``_fids``.
    *cards* is the deck's attached cards (``keywords``/``type_line``/``cmc``/
    ``mana_cost``/``otags``), read ONLY to evaluate template satisfaction.

    A win combo contributes edges ONLY when the deck satisfies every TEMPLATE piece
    the combo requires (:func:`combo_templates_satisfied`) — a template-partial combo
    that ``combo_detect`` matched on its concrete pieces alone (Cayth's variant that
    needs a Persist Creature the deck lacks, #83 BLOCKER 1) is NOT an executable loop,
    so it is dropped before promotion. For each surviving win combo, the events it
    certifies unbounded (:func:`.combo_win.unbounded_intermediate_events`) select the
    family pairs (:func:`certified_family_pairs`); each pair becomes a directed 2-cycle
    (both ``(a, b)`` and ``(b, a)``) so the SCC closes it. Edges from all surviving win
    combos are unioned and returned sorted (determinism, ADR 0003).

    The per-combo scoping is load-bearing: two UNRELATED combos (one makes infinite
    untap, another infinite mana) must NOT compose into a spurious untap↔mana loop, so
    both endpoints of a family edge must be certified unbounded in the SAME combo.
    """
    pairs: set[tuple[str, str]] = set()
    for combo in win_combos:
        if not combo_templates_satisfied(combo.get('variant_id', ''), cards):
            continue
        pairs |= certified_family_pairs(unbounded_intermediate_events(combo.get('_fids') or []))
    edges: set[tuple[str, str]] = set()
    for a, b in pairs:
        edges.add((a, b))
        edges.add((b, a))
    return sorted(edges)
