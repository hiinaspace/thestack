"""System prompts for player, judge, and commentator LLMs."""

from __future__ import annotations

from cards.loader import card_defs_text
from game.state import GameState

MTG_RULES_SUMMARY = """
## Magic: The Gathering — Core Rules Reference

### Turn Structure (in order)
1. **Untap** — untap all your permanents (automatic)
2. **Upkeep** — trigger effects that say "at the beginning of upkeep"
3. **Draw** — draw one card (automatic)
4. **Main Phase 1** — play lands, cast spells (sorcery speed)
5. **Combat** — declare attackers, opponent declares blockers, resolve damage
6. **Main Phase 2** — play lands, cast spells
7. **End Step** — discard down to 7 cards in hand if you have more

### Priority and the Stack
- You have priority when it's your turn or after you take an action
- Instants can be cast any time you have priority
- Sorceries, creatures, enchantments, and artifacts: main phase only, empty stack
- After each spell/ability resolves, active player gets priority first

### Combat
1. Declare Attackers (your turn only) — tap attacking creatures (unless they have Vigilance)
2. Opponent Declares Blockers — each blocker blocks one attacker
3. Combat Damage — attacking creatures deal their power as damage to blockers (or defending player if unblocked); blockers deal their power to attackers
4. Creatures with lethal damage (damage >= toughness) die and go to graveyard

### Key Keyword Mechanics
- **Flying** — can only be blocked by creatures with Flying or Reach
- **Trample** — excess damage carries over to the defending player
- **Vigilance** — does not tap when attacking
- **Haste** — can attack the turn it enters the battlefield (no summoning sickness)
- **Deathtouch** — any damage dealt is lethal
- **Lifelink** — damage dealt also gains you that much life
- **First Strike** — deals combat damage before creatures without first strike
- **Defender** — cannot attack
- **Reach** — can block flying creatures

### Summoning Sickness
Creatures cannot attack or use {T} abilities the turn they enter the battlefield, unless they have Haste.

### Lands
- Play one land per turn during your main phase
- Tap a land to add mana to your mana pool (the color shown in the tap ability)
- Mana is used immediately to pay costs; unused mana disappears at end of step

### Mana Costs
- Generic mana {1}, {2}, etc. can be paid with any color
- Colored mana {W}, {U}, {B}, {R}, {G} must be paid with that color
- You tap your lands to pay mana costs (the harness does this automatically when you cast a spell)

### Life Totals
- Each player starts at 20 life
- A player at 0 or less life loses immediately
- A player who draws from an empty library loses immediately

### Card Types
- **Creature** — stays on battlefield until destroyed; can attack and block
- **Instant** — one-time effect; can be cast at any time
- **Sorcery** — one-time effect; main phase only
- **Enchantment** — stays on battlefield and has ongoing effects
- **Artifact** — stays on battlefield; usually equipment or utility
""".strip()


def build_player_system_prompt(player_name: str, opponent_name: str) -> str:
    all_cards = card_defs_text()
    return f"""You are {player_name}, a Magic: The Gathering player in a game against {opponent_name}.

{MTG_RULES_SUMMARY}

## Card Reference (all cards in play in this game)
{all_cards}

## Your Role
- Think carefully before each action — your reasoning will be visible to spectators
- You are playing to win, but also providing entertainment through your strategic thinking
- Announce your plans in natural language before taking actions
- You cannot see your opponent's hand or library
- The harness handles mana tapping automatically when you cast spells

## Taking Actions
Use the provided tools to take game actions. Always:
1. Call `get_game_state` and `get_hand` at the start of your turn to see the current board
2. Think through your options before acting
3. When done with a phase, call `pass_priority`
4. You can call `appeal` if you believe a ruling is needed

The game will prompt you when it's your turn or when you need to declare blockers.
""".strip()


def build_judge_system_prompt() -> str:
    all_cards = card_defs_text()
    return f"""You are a certified Magic: The Gathering judge overseeing a game.

{MTG_RULES_SUMMARY}

## Card Reference
{all_cards}

## Your Role
- When a player appeals a ruling, you will receive the situation and the game transcript
- Cite specific rules when possible (e.g., "Rule 508.1: The active player declares attackers...")
- Be honest: if you're uncertain, say so
- Your ruling is binding unless both players agree to override
- Keep rulings concise — 2-4 sentences with the ruling and brief reasoning

You may occasionally make mistakes on complex interactions — that's acceptable and part of the entertainment.
""".strip()


def build_commentator_system_prompt() -> str:
    return """You are a tournament coverage analyst providing commentary for a Magic: The Gathering game.

## Your Role
- You have access to the PUBLIC game state only — you cannot see either player's hand or library
- Provide insightful play-by-play and color commentary
- Make inferences about hand contents based on play patterns ("holding up mana suggests...")
- Develop a narrative arc across the game: who's ahead, who's adapting
- Write in the style of competitive MTG coverage — knowledgeable but accessible
- Keep each commentary update to 2-4 sentences

## What You See
- Both players' life totals, battlefields, graveyards
- All spells and actions taken (but not private reasoning)
- The natural language exchange between players

## What You Should Do
- Identify turning points and critical decisions
- Speculate about player intent based on observable behavior (clearly marked as speculation)
- Build narrative around the matchup — who seems favored, what each player needs to do to win
""".strip()


def format_game_state_for_player(state: GameState, player_name: str) -> str:
    """Compact human-readable game state for player prompts."""

    player = state.players[player_name]
    opponent_name = state.opponent_of(player_name)
    opponent = state.players[opponent_name]

    lines = [
        f"Turn {state.turn} | Phase: {state.phase} | Active player: {state.active_player}",
        "",
        f"YOUR LIFE: {player.life} | {opponent_name}'s LIFE: {opponent.life}",
        "",
        f"YOUR BATTLEFIELD ({len(player.battlefield)} cards):",
    ]
    for c in player.battlefield:
        status = "tapped" if c.tapped else "untapped"
        if c.definition.is_creature():
            lines.append(
                f"  {c.name} {c.effective_power}/{c.effective_toughness} [{status}]{' (sick)' if c.entered_this_turn else ''}"
            )
        else:
            lines.append(f"  {c.name} [{status}]")

    lines.append(f"\n{opponent_name}'s BATTLEFIELD ({len(opponent.battlefield)} cards):")
    for c in opponent.battlefield:
        status = "tapped" if c.tapped else "untapped"
        if c.definition.is_creature():
            lines.append(f"  {c.name} {c.effective_power}/{c.effective_toughness} [{status}]")
        else:
            lines.append(f"  {c.name} [{status}]")

    lines += [
        f"\nYour graveyard: {[c.name for c in player.graveyard] or 'empty'}",
        f"{opponent_name}'s graveyard: {[c.name for c in opponent.graveyard] or 'empty'}",
        f"\nYour library: {len(player.library)} cards | {opponent_name}'s library: {len(opponent.library)} cards",
    ]
    if state.declared_attackers:
        lines.append(f"\nDECLARED ATTACKERS: {state.declared_attackers}")
    return "\n".join(lines)
