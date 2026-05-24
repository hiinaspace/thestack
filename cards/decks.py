"""Portal-set deck configs for the Argentum Engine gym-server.

Argentum's gym `/step` endpoint takes only an `actionId`, so any LegalAction
that the engine emits as a "shell" awaiting client-side binding (cast targets,
attacker/blocker assignments) is no-op for us unless the gym pre-expands it.
The local fork in `~/lib/argentum-engine` carries that expansion patch
(`gym/contract/LegalActionExpander.kt`), so single-target spells like
Volcanic Hammer and Lava Axe work end-to-end.

Still avoided here: spells whose targeting needs a structured decision the
gym doesn't fold (e.g. Forked Lightning's distribute-damage and Nature's
Lore's library search).
"""

from __future__ import annotations

# Deck configs: card name -> quantity.
# All cards must be implemented in ~/lib/argentum-engine/mtg-sets/definitions/por/.
DECKS: dict[str, dict[str, int]] = {
    "red_rush": {
        "Mountain": 24,
        "Goblin Bully": 4,  # 2/1 for {1}{R}
        "Hulking Goblin": 4,  # 2/2 can't block for {1}{R}
        "Craven Giant": 4,  # 4/1 can't block for {2}{R}
        "Minotaur Warrior": 4,  # 2/3 for {2}{R}
        "Charging Bandits": 4,  # 2/2 for {2}{R}
        "Raging Minotaur": 4,  # 2/2 for {2}{R}
        "Volcanic Hammer": 4,  # 3 damage to any target for {1}{R}
        "Lava Axe": 4,  # 5 damage to target player for {4}{R}
        "Pyroclasm": 4,  # 2 damage to each creature for {1}{R}
    },
    "green_might": {
        "Forest": 24,
        "Jungle Lion": 4,  # 2/1 for {G}
        "Gorilla Warrior": 4,  # 2/2 for {1}{G}{G}
        "Elvish Ranger": 4,  # 4/1 for {2}{G}
        "Rowan Treefolk": 4,  # 3/4 for {3}{G}
        "Panther Warriors": 4,  # 3/2 for {2}{G}
        "Charging Rhino": 4,  # 4/4 unblockable-by-more-than-one for {3}{G}{G}
        "Spined Wurm": 4,  # 5/4 for {4}{G}
        "Summer Bloom": 4,  # play up to 3 extra lands this turn for {1}{G}
        "Monstrous Growth": 4,  # target creature gets +4/+4 for {1}{G}
    },
}

DECK_NAMES = list(DECKS.keys())


def get_deck(name: str) -> dict[str, int]:
    if name not in DECKS:
        raise ValueError(f"Unknown deck '{name}'. Available: {DECK_NAMES}")
    return DECKS[name]
