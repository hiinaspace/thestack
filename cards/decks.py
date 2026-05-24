"""Portal-set deck configs for the Argentum Engine gym-server.

The local fork in `~/lib/argentum-engine` exposes rich legal actions plus
structured decision prompts, so these decks can include targeted spells,
multi-target removal, and simple library-search effects.
"""

from __future__ import annotations

# Deck configs: card name -> quantity.
# All cards must be implemented in ~/lib/argentum-engine/mtg-sets/definitions/por/.
DECKS: dict[str, dict[str, int]] = {
    "white_aegis": {
        "Plains": 24,
        "Armored Pegasus": 4,  # 1/2 flying for {1}{W}
        "Knight Errant": 4,  # 2/2 for {1}{W}
        "Charging Paladin": 4,  # 2/2 that attacks as 2/5 for {2}{W}
        "Venerable Monk": 4,  # 2/2, gain 2 on ETB for {2}{W}
        "Border Guard": 3,  # 1/4 for {2}{W}
        "Ardent Militia": 3,  # 2/5 vigilance for {4}{W}
        "Angelic Blessing": 4,  # +3/+3 and flying for {2}{W}
        "Warrior's Charge": 3,  # team +1/+1 for {2}{W}
        "Path of Peace": 4,  # destroy target creature, owner gains 4 for {3}{W}
        "Vengeance": 3,  # destroy target tapped creature for {3}{W}
    },
    "black_attrition": {
        "Swamp": 24,
        "Muck Rats": 4,  # 1/1 for {B}
        "Bog Imp": 4,  # 1/1 flying for {1}{B}
        "Feral Shadow": 4,  # 2/1 flying for {2}{B}
        "Bog Raiders": 3,  # 2/2 for {2}{B}
        "Serpent Warrior": 4,  # 3/3, lose 3 on ETB for {2}{B}
        "Gravedigger": 3,  # 2/2, return creature from graveyard for {3}{B}
        "Arrogant Vampire": 3,  # 4/3 flying for {3}{B}{B}
        "Hand of Death": 4,  # destroy target nonblack creature for {2}{B}
        "Vampiric Touch": 3,  # drain opponent for 2 for {2}{B}
        "Mind Rot": 2,  # target player discards two for {2}{B}
        "Wicked Pact": 2,  # destroy two nonblack creatures, lose 5 for {1}{B}{B}
    },
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
