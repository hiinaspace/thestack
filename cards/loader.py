"""Load card definitions and build decks from starter_kit.json."""

from __future__ import annotations

import json
import random
from pathlib import Path

from game.state import CardDef, CardInstance, PlayerState

_DATA_PATH = Path(__file__).parent / "starter_kit.json"


def _load_data() -> dict:
    return json.loads(_DATA_PATH.read_text())


def get_card_defs() -> dict[str, CardDef]:
    data = _load_data()
    defs: dict[str, CardDef] = {}
    for name, raw in data["cards"].items():
        defs[name] = CardDef(
            name=raw["name"],
            scryfall_id=raw["scryfall_id"],
            mana_cost=raw["mana_cost"],
            cmc=raw["cmc"],
            type_line=raw["type_line"],
            oracle_text=raw["oracle_text"],
            power=raw["power"],
            toughness=raw["toughness"],
            keywords=raw["keywords"],
            colors=raw["colors"],
        )
    return defs


def build_library(deck_name: str, shuffle: bool = True) -> list[CardInstance]:
    data = _load_data()
    defs = get_card_defs()
    deck_list = data["decks"][deck_name]
    library: list[CardInstance] = []
    for card_name, qty in deck_list.items():
        defn = defs.get(card_name)
        if defn is None:
            raise ValueError(f"Card '{card_name}' not found in definitions")
        for _ in range(qty):
            library.append(CardInstance.from_def(defn))
    if shuffle:
        random.shuffle(library)
    return library


def make_player(name: str, deck_name: str, shuffle: bool = True) -> PlayerState:
    library = build_library(deck_name, shuffle=shuffle)
    return PlayerState(name=name, library=library)


def card_defs_text(card_names: list[str] | None = None) -> str:
    """Return a formatted card reference for use in LLM prompts."""
    defs = get_card_defs()
    if card_names:
        defs = {k: v for k, v in defs.items() if k in card_names}
    lines: list[str] = []
    for _name, d in sorted(defs.items()):
        if d.is_land():
            lines.append(f"- {d.name} | {d.type_line} | {d.oracle_text}")
        elif d.is_creature():
            p, t = d.power or "?", d.toughness or "?"
            oracle = d.oracle_text.replace("\n", "; ") if d.oracle_text else "Vanilla"
            lines.append(f"- {d.name} {d.mana_cost} | {d.type_line} {p}/{t} | {oracle}")
        else:
            oracle = d.oracle_text.replace("\n", "; ") if d.oracle_text else ""
            lines.append(f"- {d.name} {d.mana_cost} | {d.type_line} | {oracle}")
    return "\n".join(lines)
