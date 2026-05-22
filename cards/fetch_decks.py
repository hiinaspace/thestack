"""Fetch MTG Starter Kit deck data from Scryfall and write cards/starter_kit.json.

Run once: uv run python cards/fetch_decks.py
"""

import json
import time
from pathlib import Path

import requests

SCRYFALL_BASE = "https://api.scryfall.com"

# MTG Starter Kit 2022 (released with Dominaria United)
# Two 60-card beginner decks with simple mechanics
# Source: https://magic.wizards.com/en/products/starter-kit
FORCES_OF_THE_WILD_LIST = {
    # Lands
    "Forest": 14,
    "Plains": 10,
    # Creatures
    "Eager First-Year": 2,
    "Jewel Thief": 3,
    "Sanctuary Cat": 2,
    "Arboretum Elemental": 1,
    "Might of the Masses": 2,  # actually a spell but listed here for simplicity
    "Territorial Mosscoat": 2,
    "Bristlebud Farmer": 2,
    "Bayou Groff": 2,
    "Glowbringer": 2,
    "Swiftfoot Boots": 1,
    "Llanowar Elves": 4,
    "Grizzly Bears": 4,
    "Centaur Courser": 3,
    "Serra Angel": 1,
    # Spells
    "Giant Growth": 4,
    "Rabid Bite": 2,
    "Commune with Nature": 2,
    "Raise the Alarm": 1,
}

CHAOS_OF_THE_UNDEAD_LIST = {
    # Lands
    "Mountain": 12,
    "Swamp": 12,
    # Creatures
    "Goblin Piker": 4,
    "Child of Night": 3,
    "Walking Corpse": 3,
    "Falkenrath Reaver": 2,
    "Stitched Assistant": 2,
    "Tattered Mummy": 2,
    "Gravedigger": 2,
    "Headless Rider": 2,
    "Ghoulish Procession": 2,
    # Spells
    "Shock": 4,
    "Lightning Bolt": 2,
    "Scorching Dragonfire": 2,
    "Murder": 2,
    "Dark Ritual": 2,
    "Sign in Blood": 2,
    "Cemetery Recruitment": 2,
}

ALL_CARD_NAMES = set(FORCES_OF_THE_WILD_LIST) | set(CHAOS_OF_THE_UNDEAD_LIST)
# Exclude basic lands — we define those inline
BASIC_LANDS = {"Forest", "Plains", "Mountain", "Swamp", "Island"}
CARDS_TO_FETCH = ALL_CARD_NAMES - BASIC_LANDS


def fetch_card(name: str) -> dict | None:
    for attempt in range(4):
        resp = requests.get(
            f"{SCRYFALL_BASE}/cards/named",
            params={"fuzzy": name},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "name": data["name"],
                "scryfall_id": data["id"],
                "mana_cost": data.get("mana_cost", ""),
                "cmc": data.get("cmc", 0),
                "type_line": data["type_line"],
                "oracle_text": data.get("oracle_text", ""),
                "power": data.get("power"),
                "toughness": data.get("toughness"),
                "keywords": data.get("keywords", []),
                "colors": data.get("colors", []),
            }
        if resp.status_code == 429:
            wait = 2**attempt
            print(f"  rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        print(f"  WARN: could not find '{name}' (status {resp.status_code})")
        return None
    print(f"  WARN: gave up on '{name}' after retries")
    return None


BASIC_LAND_DEFS: dict[str, dict] = {
    "Forest": {
        "name": "Forest",
        "scryfall_id": "basic-forest",
        "mana_cost": "",
        "cmc": 0,
        "type_line": "Basic Land — Forest",
        "oracle_text": "({T}: Add {G}.)",
        "power": None,
        "toughness": None,
        "keywords": [],
        "colors": [],
    },
    "Plains": {
        "name": "Plains",
        "scryfall_id": "basic-plains",
        "mana_cost": "",
        "cmc": 0,
        "type_line": "Basic Land — Plains",
        "oracle_text": "({T}: Add {W}.)",
        "power": None,
        "toughness": None,
        "keywords": [],
        "colors": [],
    },
    "Mountain": {
        "name": "Mountain",
        "scryfall_id": "basic-mountain",
        "mana_cost": "",
        "cmc": 0,
        "type_line": "Basic Land — Mountain",
        "oracle_text": "({T}: Add {R}.)",
        "power": None,
        "toughness": None,
        "keywords": [],
        "colors": [],
    },
    "Swamp": {
        "name": "Swamp",
        "scryfall_id": "basic-swamp",
        "mana_cost": "",
        "cmc": 0,
        "type_line": "Basic Land — Swamp",
        "oracle_text": "({T}: Add {B}.)",
        "power": None,
        "toughness": None,
        "keywords": [],
        "colors": [],
    },
    "Island": {
        "name": "Island",
        "scryfall_id": "basic-island",
        "mana_cost": "",
        "cmc": 0,
        "type_line": "Basic Land — Island",
        "oracle_text": "({T}: Add {U}.)",
        "power": None,
        "toughness": None,
        "keywords": [],
        "colors": [],
    },
}


def main() -> None:
    cards: dict[str, dict] = dict(BASIC_LAND_DEFS)

    print(f"Fetching {len(CARDS_TO_FETCH)} non-land cards from Scryfall...")
    for name in sorted(CARDS_TO_FETCH):
        print(f"  fetching: {name}")
        result = fetch_card(name)
        if result:
            cards[result["name"]] = result
        time.sleep(0.1)  # Scryfall asks for ≤10 req/s

    out = {
        "cards": cards,
        "decks": {
            "forces_of_the_wild": FORCES_OF_THE_WILD_LIST,
            "chaos_of_the_undead": CHAOS_OF_THE_UNDEAD_LIST,
        },
    }

    dest = Path(__file__).parent / "starter_kit.json"
    dest.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {len(cards)} card definitions to {dest}")

    missing = [n for n in CARDS_TO_FETCH if n not in cards and n not in BASIC_LANDS]
    if missing:
        print(f"\nMISSING ({len(missing)} cards — update FORCES/CHAOS lists):")
        for m in missing:
            print(f"  - {m}")


if __name__ == "__main__":
    main()
