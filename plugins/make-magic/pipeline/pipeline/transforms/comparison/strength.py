"""Per-card strength — the two entry points the card-recommendation sibling calls.

Ranking / strength is OWNED here (the recommendation side generates candidates; this
side evaluates them — co-development-plan). Two public entry points, both returning the
shared typed contracts from :mod:`.contracts`:

* :func:`strength` — scores an EXTERNAL candidate pool for a role and returns
  ``dict[oracle_id, StrengthRecord]``. Axis B (deck-agnostic) is the backbone here:
  the ``edhrec_rank`` popularity signal (from the Scryfall cache — missing ⇒ UNKNOWN,
  never weak), the promote-only ``game_changer`` / ``combo`` flags, and the popularity-free
  ``value_density`` corrective (role-scoped to the anchor event's producer family). Axis A
  (deck-relative) carries a light structural-fit signal; the heavy engine-participation
  delta is the separate :func:`marginal_participation` call.
* :func:`marginal_participation` — the engine-participation delta of adding ONE prospective
  card. It injects the candidate's attributes by adding it to the deck and **re-electing
  through the real engine** (``identify_engines`` → ``elect_engine_units_with_relay``),
  reading the before/after isolation rather than duplicating the graph walk (design §6 /
  co-development-plan request 2). The deferred combo-certified ``combo_event_edges`` seam
  stays ``None`` (families are #90), exactly as the shipped engine passes it.

**Offline + deterministic.** Neither entry point hits the network: axis B reads the
Scryfall cache populated by :mod:`.strength_fetch` (authoring-time), and the engine reads
the lake / bundled assets. All identity joins are by **oracle_id**, never name.
"""

from __future__ import annotations

import math
from typing import Any

from pipeline.transforms.comparison.contracts import (
    MarginalParticipation,
    StrengthAgnostic,
    StrengthRecord,
    StrengthRelative,
)
from pipeline.transforms.comparison.strength_fetch import StrengthData, load_cache, strength_cache_path
from pipeline.transforms.comparison.value_density import value_density

# --------------------------------------------------------------------------- #
# Candidate / deck resolution helpers.
# --------------------------------------------------------------------------- #


def _mv(x: Any) -> float:
    """Coerce a mana value to a finite non-negative float; missing / non-finite ⇒ 0.0.

    ``float(x or 0.0)`` is NOT enough: ``NaN`` is truthy, so it escapes the ``or`` guard and
    launders downstream (``value_density`` ⇒ NaN ⇒ the ``null`` "not computed" sentinel). A
    non-numeric, ``None``, ``NaN`` or ``inf`` MV is read as missing (0), never as a real cost.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _derive_attrs(otags: list[str], assets: Any, card: dict | None = None) -> set[str]:
    """otag closure → engine attribute set (the one-named derivation the engine uses)."""
    from pipeline.transforms.strategy.distill_strategies import synth_attrs_for
    from pipeline.transforms.strategy.strategy_graph import derive_card_attributes

    closure = {t.lower() for t in otags}
    attrs = set(derive_card_attributes(closure, assets))
    if card is not None:
        attrs |= set(synth_attrs_for(card, assets.synth_predicates))
    return attrs


def _candidate_card(
    oracle_id: str,
    *,
    card: dict | None,
    lake: Any,
    meta_by_oid: dict[str, dict] | None = None,
) -> dict:
    """Build a deck-card dict for a candidate oracle_id (injected ``card`` wins).

    Lake path: attrs/type_line from the lake's oracle_id index; ``name``/``cmc`` from
    ``meta_by_oid`` (an oracle_id → card-meta map, so value-density's MV denominator is real).
    """
    if card is not None:
        return {**card, 'oracle_id': oracle_id, 'role': None}
    slugs = sorted(lake.oid2slugs.get(oracle_id, set()))
    type_line = lake.oid2type.get(oracle_id, '')
    meta = (meta_by_oid or {}).get(oracle_id, {})
    return {
        'name': meta.get('name', oracle_id),
        'otags': slugs,
        'type_line': type_line or meta.get('type_line_full', ''),
        'cmc': _mv(meta.get('cmc')),
        'oracle_id': oracle_id,
        'role': None,
    }


def _as_deck(deck_or_dump: dict, *, lake: Any = None) -> dict:
    """Accept a deck dict (has ``cards``) or a semantic dump (has ``pool``)."""
    if 'cards' in deck_or_dump:
        return deck_or_dump
    pool = deck_or_dump.get('pool')
    if pool is None:
        raise ValueError("deck_or_dump must carry 'cards' (a deck) or 'pool' (a semantic dump)")
    if lake is None:
        from pipeline.transforms.strategy import corpus

        lake = corpus.Lake()
    cards = []
    for entry in pool:
        name, oid = (entry[0], entry[1]) if isinstance(entry, list | tuple) else (entry, None)
        slugs = sorted(lake.oid2slugs.get(str(oid), set())) if oid else []
        cards.append(
            {'name': name, 'otags': slugs, 'type_line': lake.oid2type.get(str(oid), ''), 'oracle_id': oid, 'role': None}
        )
    return {'cards': cards}


def _commander_attrs(deck: dict) -> set[str]:
    out: set[str] = set()
    for c in deck['cards']:
        if c.get('role') == 'commander':
            out |= {t.lower() for t in c.get('otags', [])}
    return out


def _commander_attrs_derived(deck: dict, assets: Any) -> set[str]:
    """Command-zone attributes DERIVED (otag closure + synth), the same engine derivation.

    Commander coupling must compare DERIVED candidate attrs against DERIVED commander attrs
    — raw otags would false-negative (``lifegain-matters`` derives to ``lifegain``, so a raw
    overlap misses a real coupling). This mirrors :func:`_derive_attrs` on each commander.
    """
    out: set[str] = set()
    for c in deck['cards']:
        if c.get('role') == 'commander':
            out |= _derive_attrs(c.get('otags', []), assets, card=c)
    return out


def _role_attrs(anchor_event: str | None, assets: Any) -> set[str] | None:
    """The attribute family that produces ``anchor_event`` — the role scope for value-density.

    ``None`` (no anchor) ⇒ a whole-card density (global). The producer list is the engine's
    own catalog (``events[event]['produced_by']``), so the role scope is never a hand-list.

    An unknown / typo ``anchor_event`` is a loud ``ValueError`` — NOT a silent fall-back to a
    whole-card reading, which would invert the ranking (a 7-attr kitchen-sink card beats a
    focused draw card). The catalog is validated against the engine's own event set.
    """
    if anchor_event is None:
        return None
    if anchor_event not in assets.events:
        raise ValueError(
            f'unknown anchor_event {anchor_event!r}: not in the event catalog '
            '(pass None for a whole-card reading, or a valid event name)'
        )
    ev = assets.events[anchor_event]
    return set(ev.get('produced_by', [])) | set(ev.get('consumed_by', []))


# --------------------------------------------------------------------------- #
# Entry point 1 — strength(): score an external candidate pool for a role.
# --------------------------------------------------------------------------- #


def strength(
    deck_or_dump: dict,
    candidate_oracle_ids: list[str],
    *,
    anchor_event: str | None = None,
    strength_data: dict[str, StrengthData] | None = None,
    cache_path: str | None = None,
    combo_oracle_ids: set[str] | None = None,
    candidate_cards: dict[str, dict] | None = None,
    assets: Any = None,
    lake: Any = None,
    value_table: dict[str, Any] | None = None,
) -> dict[str, StrengthRecord]:
    """Rank an external candidate pool for a role; returns ``{oracle_id: StrengthRecord}``.

    Parameters
    ----------
    deck_or_dump
        The deck the candidates are evaluated *against* (for the commander coupling /
        role context of axis A). A deck dict (``cards``) or a semantic dump (``pool``).
    candidate_oracle_ids
        oracle_ids of cards NOT in the deck — the pool to rank.
    anchor_event
        The event/role to scope to (e.g. ``'draw-a-card'``). Scopes value-density to that
        event's producer family and axis-A fit to that role. ``None`` ⇒ whole-card reading.
    strength_data
        Pre-loaded ``{oracle_id: StrengthData}`` (edhrec_rank / game_changer / price). If
        ``None``, read from the Scryfall cache at ``cache_path`` (default XDG location).
        A candidate absent from this map is rank-blind ⇒ UNKNOWN, not weak.
    combo_oracle_ids
        oracle_ids that participate in a known combo (promote-only flag). Default empty.
    candidate_cards
        Optional ``{oracle_id: card_dict}`` to resolve candidates without a lake (tests /
        injected pools). Otherwise the lake resolves attrs/MV by oracle_id.
    """
    if assets is None:
        from pipeline.transforms.strategy.assets import load_assets

        assets = load_assets()
    if strength_data is None:
        strength_data = load_cache(cache_path or strength_cache_path())
    combo_oracle_ids = combo_oracle_ids or set()
    candidate_cards = candidate_cards or {}

    meta_by_oid: dict[str, dict] = {}
    if not all(oid in candidate_cards for oid in candidate_oracle_ids):
        from pipeline.transforms.strategy import corpus

        if lake is None:
            lake = corpus.Lake()
        # oracle_id -> card meta (name/cmc), so the lake path has a real MV denominator.
        for m in corpus.load_card_meta().values():
            moid = m.get('oracle_id')
            if moid:
                meta_by_oid.setdefault(str(moid), m)

    deck = _as_deck(deck_or_dump, lake=lake)
    cmd_attrs = _commander_attrs(deck)
    role_attrs = _role_attrs(anchor_event, assets)

    out: dict[str, StrengthRecord] = {}
    for oid in candidate_oracle_ids:
        card = _candidate_card(oid, card=candidate_cards.get(oid), lake=lake, meta_by_oid=meta_by_oid)
        attrs = _derive_attrs(card.get('otags', []), assets, card=card)
        mv = _mv(card.get('cmc', card.get('mv')))

        sd = strength_data.get(oid, StrengthData())
        vd = value_density(attrs, mv, role_attrs=role_attrs, table=value_table)

        # Axis A — a LIGHT deck-relative structural-fit signal (the heavy engine delta is
        # marginal_participation). Non-negative: role-family overlap + commander coupling.
        fit_overlap = float(len(attrs & role_attrs)) if role_attrs else 0.0
        commander_coupled = bool(attrs & cmd_attrs)
        engine_participation = fit_overlap + (1.0 if commander_coupled else 0.0)

        rank_known = sd.edhrec_rank is not None
        out[oid] = StrengthRecord(
            strength_relative=StrengthRelative(
                anchor_event=anchor_event,
                engine_participation=engine_participation,
                edhrec_synergy=None,  # commander-conditioned EDHREC synergy is not available offline
            ),
            strength_agnostic=StrengthAgnostic(
                edhrec_rank=sd.edhrec_rank,
                game_changer=sd.game_changer,
                combo=oid in combo_oracle_ids,
                value_density=round(vd, 4),
            ),
            confidence='medium' if rank_known else 'low',
        )
    return out


# --------------------------------------------------------------------------- #
# Entry point 2 — marginal_participation(): engine delta of a prospective add.
# --------------------------------------------------------------------------- #


def _iso(deck: dict, *, store: Any, combos: list[dict] | None) -> dict:
    """Run the real engine and return its isolation dict (reused, never re-walked)."""
    from pipeline.transforms.strategy.engine_report import identify_engines

    return identify_engines(deck, store=store, combos=combos).raw_isolation


def _members_by_event(iso: dict) -> dict[str, set[str]]:
    return {e['event']: set(e.get('producers', [])) | set(e.get('consumers', [])) for e in iso.get('engines', [])}


def _role_mass(iso: dict, cand_attrs: set[str], anchor_event: str | None) -> float:
    """Total elected mass attributable to the role (anchor event, or units the card touches)."""
    units = iso.get('ranked_units', [])
    if anchor_event is not None:
        return float(sum(u['mass'] for u in units if u['event'] == anchor_event))
    mem = _members_by_event(iso)
    return float(sum(u['mass'] for u in units if mem.get(u['event'], set()) & cand_attrs))


def _closes(iso: dict) -> bool:
    cl = iso.get('commander_loop')
    return bool(cl and cl.get('closes'))


def _recursive_anchor(iso: dict, anchor_event: str | None) -> bool:
    return any(
        u.get('recursive') and (anchor_event is None or u['event'] == anchor_event) for u in iso.get('ranked_units', [])
    )


def marginal_participation(
    deck_or_dump: dict,
    candidate_oracle_id: str,
    *,
    anchor_event: str | None = None,
    candidate_card: dict | None = None,
    store: Any = None,
    combos: list[dict] | None = None,
    lake: Any = None,
    assets: Any = None,
) -> MarginalParticipation:
    """Engine-participation delta of adding ``candidate_oracle_id`` to the deck.

    Injects the candidate's attributes by appending it to the deck and **re-electing
    through the real engine** (``identify_engines`` runs ``elect_engine_units_with_relay``),
    then diffs the before/after isolation. No duplicated graph walk; the deferred
    ``combo_event_edges`` event→event seam stays ``None`` (families are #90).

    ``store`` defaults to an EMPTY provisional store (the committed base engine, matching
    ``resolve()``), so the delta is deterministic and independent of any machine_vocab on
    disk. Pass ``candidate_card`` (a card dict with ``otags``) to resolve without a lake.
    """
    if store is None:
        from pipeline.transforms.strategy.machine_vocab import empty_store

        store = empty_store()
    if candidate_card is None and lake is None:
        from pipeline.transforms.strategy import corpus

        lake = corpus.Lake()
    if assets is None:
        from pipeline.transforms.strategy.assets import load_assets

        assets = load_assets()

    deck = _as_deck(deck_or_dump, lake=lake)

    # R-B — idempotence: a card already in the deck adds nothing (Commander is singleton).
    # Re-electing with it duplicated would double-count its attrs for a bogus positive delta,
    # so short-circuit to an all-zero / all-False reading (the anchor echo is still honoured).
    deck_oids = {c.get('oracle_id') for c in deck['cards'] if c.get('oracle_id')}
    if candidate_oracle_id in deck_oids:
        return MarginalParticipation(anchor_event=anchor_event)

    cand = _candidate_card(candidate_oracle_id, card=candidate_card, lake=lake)
    # R-ROOT — compare DERIVED candidate attrs against DERIVED engine objects (relay hubs,
    # per-event producer/consumer families, commander attrs are ALL derived). Raw otags
    # false-negative commander coupling, under-count anchorless role mass, and can never
    # match a derived relay-hub name. This is the derivation strength() already uses.
    cand_attrs = _derive_attrs(cand.get('otags', []), assets, card=cand)
    cmd_attrs = _commander_attrs_derived(deck, assets)

    before = _iso(deck, store=store, combos=combos)
    after_deck = {**deck, 'cards': [*deck['cards'], cand]}
    after = _iso(after_deck, store=store, combos=combos)

    before_payoffs = {a for u in before.get('ranked_units', []) for a in u.get('payoff_archetypes', [])}
    after_payoffs = {a for u in after.get('ranked_units', []) for a in u.get('payoff_archetypes', [])}

    closes_loop = (_closes(after) and not _closes(before)) or (
        _recursive_anchor(after, anchor_event) and not _recursive_anchor(before, anchor_event)
    )

    return MarginalParticipation(
        anchor_event=anchor_event,
        delta_support=round(
            _role_mass(after, cand_attrs, anchor_event) - _role_mass(before, cand_attrs, anchor_event), 3
        ),
        delta_connectivity=float(len(after.get('forward_chains', [])) - len(before.get('forward_chains', []))),
        closes_loop=closes_loop,
        adds_payoff_axis=bool(after_payoffs - before_payoffs),
        becomes_relay=bool((set(after.get('relay_hubs', [])) - set(before.get('relay_hubs', []))) & cand_attrs),
        commander_coupled=bool(cand_attrs & cmd_attrs),
    )
