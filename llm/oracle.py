"""Oracle text lookup, sourced from Argentum's bundled scryfall-portal.json.

Used by the pre-game Strategist to give an agent its full deck list with
rules text, since at that point there is no Argentum observation to read.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

# Override with ORACLE_JSON env var if running outside the dev tree.
DEFAULT_PATH = Path(
    os.environ.get("ORACLE_JSON", "/home/s/lib/argentum-engine/scryfall-portal.json")
)


@lru_cache(maxsize=1)
def _load(path: Path = DEFAULT_PATH) -> dict[str, dict]:
    raw = json.loads(path.read_text())
    return {c["name"]: c for c in raw}


def card(name: str) -> dict | None:
    return _load().get(name)


def deck_listing(deck: dict[str, int]) -> str:
    """Format a deck as a card-by-card listing with mana cost, type, oracle text."""
    cards = _load()
    lines: list[str] = []
    for card_name, qty in deck.items():
        c = cards.get(card_name)
        if c is None:
            lines.append(f"{qty}x {card_name} (no oracle data)")
            continue
        type_line = c.get("type_line", "")
        cost = c.get("mana_cost", "")
        if "Land" in type_line:
            lines.append(f"{qty}x {card_name} — {type_line}")
            continue
        oracle = c.get("oracle_text", "").replace("\n", "; ")
        pt = ""
        if c.get("power") is not None:
            pt = f" {c['power']}/{c['toughness']}"
        lines.append(f"{qty}x {card_name} {cost}{pt} — {type_line}: {oracle}")
    return "\n".join(lines)
