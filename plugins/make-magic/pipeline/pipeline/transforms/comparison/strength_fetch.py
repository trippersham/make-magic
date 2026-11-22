"""Scryfall strength fetch + cache — the authoring/cache-populate half of axis B.

The otag lake does **not** carry ``edhrec_rank`` / ``game_changer`` / prices
(design §3), so those three deck-agnostic strength signals are fetched from
Scryfall and cached. This module is the ONLY network surface of the comparison
package, and it is **authoring / cache-populate only**:

* the default offline test suite never calls the real network — it drives
  :func:`fetch_strength` through an ``httpx.MockTransport`` (see the tests); the
  one real-network test is marked ``live`` and deselected by default;
* the runtime scorer (:mod:`.strength`) reads the **cache**, never the network,
  so a `collection compare` / `strength()` call is deterministic and offline.

Three contract points the plan pins:

* **Join by oracle_id, never name.** Identifiers are sent as ``{"oracle_id": …}``
  and the response is keyed on each card's own ``oracle_id`` — a name join would
  collapse reprints / split cards and mis-attribute ranks.
* **Generic User-Agent.** The UA is a fixed project string with a project URL and
  carries no user identity (no name, no email, no machine id) — a privacy rule.
* **Missing rank ⇒ UNKNOWN, not weak.** A card Scryfall returns with no
  ``edhrec_rank`` (banned, or a brand-new crossover set) keeps ``edhrec_rank=None``;
  a card Scryfall does not return at all is simply absent. Both read as UNKNOWN
  downstream (``StrengthAgnostic.edhrec_rank is None``), never as low strength.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

#: Generic project User-Agent. Deliberately carries NO user identity (name, email,
#: machine) — only the project name/version and its public URL, per the privacy
#: rule. Scryfall asks clients to identify themselves generically; this does.
USER_AGENT = 'make-magic/0.9 (+https://github.com/trippersham/make-magic)'

#: Scryfall collection endpoint + its documented 75-identifier-per-request cap.
_COLLECTION_URL = 'https://api.scryfall.com/cards/collection'
_BATCH = 75

#: Cache location under the XDG data store (the same ``MAKE_MAGIC_DATA_DIR`` root
#: the lake uses). A flat JSON map ``{oracle_id: {edhrec_rank, game_changer, usd}}``.
_CACHE_RELPATH = Path('strength') / 'scryfall_strength.json'


@dataclass(frozen=True, slots=True)
class StrengthData:
    """The three deck-agnostic strength signals Scryfall carries for one card.

    ``edhrec_rank`` is 1-based (1 = most played; lower is stronger) and is ``None``
    for a rank-blind card (banned / brand-new) — UNKNOWN, not weak. ``game_changer``
    is the official WotC high-power flag (asymmetric / promote-only downstream).
    ``usd`` is a noisy secondary signal (price), kept for tiebreak/diagnostics only.
    """

    edhrec_rank: int | None = None
    game_changer: bool = False
    usd: float | None = None


def strength_cache_path(data_dir: str | os.PathLike[str] | None = None) -> Path:
    """The cache file path under the XDG data store.

    Honors ``MAKE_MAGIC_DATA_DIR`` (the lake root), falling back to the XDG default
    ``~/.local/share/make-magic`` — so the strength cache lives beside the lake it
    annotates and moves with it.
    """
    root = data_dir or os.environ.get('MAKE_MAGIC_DATA_DIR') or '~/.local/share/make-magic'
    return Path(root).expanduser() / _CACHE_RELPATH


# --------------------------------------------------------------------------- #
# Cache I/O (pure, offline).
# --------------------------------------------------------------------------- #


def _coerce_rank(value: object) -> int | None:
    """Coerce an ``edhrec_rank`` to a valid rank or ``None`` (UNKNOWN, never weak).

    ``edhrec_rank`` is 1-based and the contract constrains it with ``gt=0``; a cached or
    fetched ``<= 0`` (or non-int) rank would raise a ``ValidationError`` and abort the WHOLE
    pool's :func:`strength` instead of degrading that one card. Coerce anything invalid to
    ``None`` — rank-blind, UNKNOWN downstream, not a crash and not a fabricated rank.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def load_cache(path: str | os.PathLike[str]) -> dict[str, StrengthData]:
    """Read ``{oracle_id: StrengthData}`` from a cache file; missing file ⇒ empty.

    A cached ``edhrec_rank <= 0`` (or non-int) is coerced to ``None`` (UNKNOWN) rather than
    letting the ``gt=0`` contract abort every candidate when the record is later scored.
    """
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    return {
        oid: StrengthData(**{**rec, 'edhrec_rank': _coerce_rank(rec.get('edhrec_rank'))}) for oid, rec in raw.items()
    }


def save_cache(data: dict[str, StrengthData], path: str | os.PathLike[str]) -> None:
    """Write the cache atomically (parents created, temp-then-rename)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {oid: asdict(rec) for oid, rec in sorted(data.items())}
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(p)


def merge_cache(base: dict[str, StrengthData], new: dict[str, StrengthData]) -> dict[str, StrengthData]:
    """Return ``base`` overlaid with ``new`` (new wins), without mutating either."""
    return {**base, **new}


# --------------------------------------------------------------------------- #
# Network fetch (authoring / cache-populate only).
# --------------------------------------------------------------------------- #


def _parse_card(card: dict) -> tuple[str, StrengthData] | None:
    """Join by the card's own oracle_id; drop a card with no oracle_id."""
    oid = card.get('oracle_id')
    if not oid:
        return None
    usd_raw = (card.get('prices') or {}).get('usd')
    try:
        usd = float(usd_raw) if usd_raw is not None else None
    except (TypeError, ValueError):
        usd = None
    return str(oid), StrengthData(
        edhrec_rank=_coerce_rank(card.get('edhrec_rank')),
        game_changer=bool(card.get('game_changer', False)),
        usd=usd,
    )


def fetch_strength(
    oracle_ids: list[str],
    *,
    client: httpx.Client | None = None,
    user_agent: str = USER_AGENT,
    pause: float = 0.1,
) -> dict[str, StrengthData]:
    """Fetch strength signals for ``oracle_ids`` from Scryfall, keyed by oracle_id.

    Sends ``{"oracle_id": …}`` identifiers in batches of 75 and joins the response
    on each card's own ``oracle_id`` (never name). A card absent from the response
    (``not_found``) is simply omitted — UNKNOWN downstream, never fabricated.

    Pass ``client`` (an ``httpx.Client``) to inject a transport; the offline tests
    pass a ``MockTransport``-backed client so no real network call is made. With no
    client, a real network request is issued — this path is authoring-only.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    out: dict[str, StrengthData] = {}
    try:
        for i in range(0, len(oracle_ids), _BATCH):
            batch = oracle_ids[i : i + _BATCH]
            resp = client.post(
                _COLLECTION_URL,
                json={'identifiers': [{'oracle_id': oid} for oid in batch]},
                headers={'User-Agent': user_agent, 'Accept': 'application/json'},
            )
            resp.raise_for_status()
            for card in resp.json().get('data', []):
                parsed = _parse_card(card)
                if parsed is not None:
                    out[parsed[0]] = parsed[1]
            if pause and i + _BATCH < len(oracle_ids):
                time.sleep(pause)  # be polite to Scryfall between batches
    finally:
        if owns_client:
            client.close()
    return out


def populate_cache(
    oracle_ids: list[str],
    *,
    path: str | os.PathLike[str] | None = None,
    client: httpx.Client | None = None,
    user_agent: str = USER_AGENT,
    pause: float = 0.1,
) -> dict[str, StrengthData]:
    """Fetch ``oracle_ids``, MERGE into the on-disk cache, persist, and return it.

    The authoring entry point: it is the only function that both hits the network
    and writes the cache. The runtime scorer reads :func:`load_cache` and never
    calls this.
    """
    cache_path = Path(path) if path is not None else strength_cache_path()
    existing = load_cache(cache_path)
    fetched = fetch_strength(oracle_ids, client=client, user_agent=user_agent, pause=pause)
    merged = merge_cache(existing, fetched)
    save_cache(merged, cache_path)
    return merged
