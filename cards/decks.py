"""Portal-set deck configs for the Argentum Engine gym-server."""

from __future__ import annotations

# Deck configs: card name -> quantity.
# All cards must be implemented in ~/lib/argentum-engine/mtg-sets/definitions/por/.
DECKS: dict[str, dict[str, int]] = {
    "red_rush": {
        "Mountain": 24,
        "Goblin Bully": 4,  # 2/1 for {1}{R}
        "Hulking Goblin": 4,  # 2/2 can't block for {1}{R}
        "Craven Giant": 4,  # 4/1 can't block for {2}{R}
        "Minotaur Warrior": 4,  # 2/3 for {2}{R} (Hill Giant is a Printing reprint, not registered)
        "Volcanic Hammer": 4,  # deal 3 to any target (sorcery) for {1}{R}
        "Lava Axe": 4,  # deal 5 to target player (sorcery) for {4}{R}
        "Forked Lightning": 4,  # 4 damage divided among up to 3 creatures for {3}{R}
        "Charging Bandits": 4,  # 2/2 for {2}{R}
        "Raging Minotaur": 4,  # 2/2 for {2}{R}
    },
    "green_might": {
        "Forest": 24,
        "Jungle Lion": 4,  # 2/1 for {G} (Grizzly Bears is a Printing reprint, not registered)
        "Gorilla Warrior": 4,  # 2/2 for {1}{G}{G}
        "Elvish Ranger": 4,  # 4/1 for {2}{G}
        "Rowan Treefolk": 4,  # 3/4 for {3}{G} (Giant Spider is a Printing reprint, not registered)
        "Panther Warriors": 4,  # 3/2 for {2}{G}
        "Charging Rhino": 4,  # 4/4 can't be blocked by more than one for {3}{G}{G}
        "Spined Wurm": 4,  # 5/4 for {4}{G}
        "Monstrous Growth": 4,  # +4/+4 until end of turn (sorcery) for {1}{G}
        "Nature's Lore": 4,  # search for Forest (sorcery) for {1}{G}
    },
}

DECK_NAMES = list(DECKS.keys())


def get_deck(name: str) -> dict[str, int]:
    if name not in DECKS:
        raise ValueError(f"Unknown deck '{name}'. Available: {DECK_NAMES}")
    return DECKS[name]
