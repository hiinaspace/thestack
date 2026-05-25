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
        "Skeletal Crocodile": 2,  # 5/1 for {3}{B}
        "Gravedigger": 3,  # 2/2, return creature from graveyard for {3}{B}
        "Arrogant Vampire": 3,  # 4/3 flying for {3}{B}{B}
        "Hand of Death": 4,  # destroy target nonblack creature for {2}{B}
        "Vampiric Touch": 3,  # drain opponent for 2 for {2}{B}
        "Mind Rot": 2,  # target player discards two for {2}{B}
    },
    "red_rush": {
        "Mountain": 24,
        "Goblin Bully": 4,  # 2/1 for {1}{R}
        "Hulking Goblin": 4,  # 2/2 can't block for {1}{R}
        "Craven Giant": 4,  # 4/1 can't block for {2}{R}
        "Minotaur Warrior": 4,  # 2/3 for {2}{R}
        "Raging Cougar": 4,  # 2/2 haste for {2}{R}
        "Raging Minotaur": 4,  # 3/3 haste for {2}{R}{R}
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
    # Adds stack interaction (counterspell), ETB triggers with targeting,
    # bounce, card draw, and a sorcery tutor on top of the basic creature
    # game. Stresses the harness's structured-decision path far more than
    # the four base decks do.
    "blue_tempo": {
        "Island": 24,
        "Cloud Pirates": 3,  # 1/1 flying for {U}
        "Storm Crow": 3,  # 1/2 flying for {1}{U}
        "Owl Familiar": 2,  # 1/1 flying, ETB loot for {1}{U}
        "Wind Drake": 4,  # 2/2 flying for {2}{U}
        "Man-o'-War": 4,  # 2/2, ETB bounce target creature for {2}{U}
        "Cloud Spirit": 2,  # 3/1 flying for {2}{U}
        "Snapping Drake": 3,  # 3/2 flying for {3}{U}
        "Cloud Dragon": 2,  # 5/4 flying for {5}{U}
        "Mystic Denial": 4,  # counter creature/sorcery spell for {1}{U}{U}
        "Command of Unsummoning": 3,  # bounce-creature instant for {2}{U}
        "Time Ebb": 2,  # put target creature on top of library for {2}{U}
        "Touch of Brilliance": 2,  # draw 2 for {3}{U}
        "Ancestral Memories": 1,  # look at top of library and keep for {2}{U}{U}{U}
        "Personal Tutor": 1,  # tutor a sorcery (SearchLibrary decision) for {U}
    },
    # Green tutor-heavy ramp. Forces the harness through SEARCH_LIBRARY and
    # REORDER_LIBRARY structured decisions far more than the base decks do;
    # Natural Order also exercises sacrifice-as-additional-cost.
    "green_tutor": {
        "Forest": 24,
        "Wood Elves": 4,  # 1/1 for {2}{G}, ETB tutor a Forest to battlefield
        "Sylvan Tutor": 4,  # tutor a creature to top of library for {G}
        "Nature's Lore": 4,  # tutor a Forest to battlefield for {1}{G}
        "Untamed Wilds": 4,  # tutor any basic land to battlefield for {2}{G}
        "Natural Order": 2,  # sac green creature, tutor green creature for {2}{G}{G}
        "Elvish Ranger": 4,  # 4/1 for {2}{G}
        "Rowan Treefolk": 4,  # 3/4 for {3}{G}
        "Spined Wurm": 4,  # 5/4 for {4}{G} — Natural Order target
        "Charging Rhino": 2,  # 4/4 unblockable-by-more-than-one for {3}{G}{G}
        "Monstrous Growth": 4,  # +4/+4 pump for {1}{G}
    },
    # Black removal + activated abilities + multi-target instant. King's
    # Assassin stresses the activated-ability path; Wicked Pact uses TWO
    # independent CHOOSE_TARGETS slots in one spell. Cruel Tutor adds
    # SEARCH_LIBRARY at deck-wide breadth.
    "black_removal": {
        "Swamp": 24,
        "King's Assassin": 3,  # 1/1 for {1}{B}{B}; {T}: destroy tapped creature
        "Bog Imp": 4,  # 1/1 flying for {1}{B}
        "Feral Shadow": 4,  # 2/1 flying for {2}{B}
        "Gravedigger": 3,  # 2/2 for {3}{B}, ETB return creature from graveyard
        "Arrogant Vampire": 3,  # 4/3 flying for {3}{B}{B}
        "Wicked Pact": 3,  # destroy 2 target nonblack creatures, lose 5 for {1}{B}{B}
        "Hand of Death": 4,  # destroy target nonblack for {2}{B}
        "Vampiric Touch": 4,  # drain 2 for {2}{B}
        "Cruel Tutor": 4,  # tutor any card to top of library, lose 2 for {2}{B}
        "Mind Rot": 4,  # target player discards 2 for {2}{B}
    },
    # White defensive control. Activated + triggered abilities (Stern
    # Marshal pump on tap, Seasoned Marshal tap-on-attack), conditional
    # tutor (Gift of Estates), and Breath of Life recursion. Fewer outright
    # finishers than white_aegis but a much richer decision tree.
    "white_control": {
        "Plains": 24,
        "Armored Pegasus": 4,  # 1/2 flying for {1}{W}
        "Knight Errant": 4,  # 2/2 for {1}{W}
        "Border Guard": 4,  # 1/4 for {2}{W}
        "Stern Marshal": 3,  # 2/2 for {2}{W}; {T}: target creature +2/+2 EOT
        "Seasoned Marshal": 3,  # 2/2 for {2}{W}{W}; on attack, may tap target creature
        "Ardent Militia": 2,  # 2/5 vigilance for {4}{W}
        "Angelic Blessing": 3,  # target creature +3/+3 and flying for {2}{W}
        "Path of Peace": 3,  # destroy target creature, owner gains 4 for {3}{W}
        "Vengeance": 3,  # destroy target tapped creature for {3}{W}
        "Gift of Estates": 3,  # if opp has more lands, search up to 3 Plains for {1}{W}
        "Breath of Life": 4,  # return creature card from graveyard to battlefield for {3}{W}
    },
    # Red with X-cost burn and divided damage on top of the standard rush
    # creatures. Exercises the X-cost shape and the DISTRIBUTE structured
    # decision (Forked Lightning splits 4 damage across 1-3 targets).
    "red_bolt": {
        "Mountain": 24,
        "Raging Goblin": 4,  # 1/1 haste for {R}
        "Goblin Bully": 4,  # 2/1 for {1}{R}
        "Hulking Goblin": 3,  # 2/2 can't block for {1}{R}
        "Minotaur Warrior": 4,  # 2/3 for {2}{R}
        "Raging Minotaur": 4,  # 2/2 for {2}{R}
        "Scorching Spear": 3,  # 1 damage any target for {R}
        "Volcanic Hammer": 4,  # 3 damage to any target for {1}{R}
        "Forked Lightning": 4,  # distribute 4 damage among 1-3 creatures for {3}{R}
        "Blaze": 3,  # X damage to any target for {X}{R}
        "Lava Axe": 3,  # 5 damage to a player for {4}{R}
    },
}

DECK_NAMES = list(DECKS.keys())


def get_deck(name: str) -> dict[str, int]:
    if name not in DECKS:
        raise ValueError(f"Unknown deck '{name}'. Available: {DECK_NAMES}")
    return DECKS[name]


# Each persona's identity is written around one mono-color archetype; pair
# them with the matching deck unless the CLI explicitly overrides. Keeps
# Aria red and Bryn green when you swap personas without remembering to also
# pass --deck-a / --deck-b. The persona identity files in personas/<name>/
# describe a play STYLE (color, tempo, voice) rather than specific cards,
# so any deck in the persona's color archetype is a legal pairing —
# e.g. `--persona-a aria --deck-a red_bolt` puts Aria on a more complex
# burn list while keeping her voice and play style.
PERSONA_DEFAULT_DECK: dict[str, str] = {
    "aria": "red_rush",
    "bryn": "green_might",
    "mira": "white_aegis",
    "noct": "black_attrition",
}


def default_deck_for(persona_name: str) -> str | None:
    """Return the default deck slug for a persona, or None if unmapped.

    CLI override: pass --deck-a / --deck-b on run_game.py to use any deck
    regardless of persona. Personas are intentionally decoupled from
    specific deck lists so you can mix and match.
    """
    return PERSONA_DEFAULT_DECK.get(persona_name)
