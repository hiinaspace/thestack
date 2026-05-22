"""Tool definitions and dispatch for player LLM actions."""

from __future__ import annotations

from game.events import (
    ATTACK,
    BLOCK,
    CARD_DIES,
    CARD_PLAYED,
    DAMAGE,
    DRAW,
    LIFE_CHANGE,
    MANA_TAPPED,
    PRIORITY_PASS,
    EventLog,
)
from game.rules import (
    can_attack,
    can_block,
    can_cast_spell,
    can_play_land,
    parse_mana_cost,
    spend_mana_for_cost,
)
from game.state import CardInstance, GameState, PlayerState

# OpenAI-format tool schemas
PLAYER_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "play_land",
            "description": "Play a land card from your hand onto the battlefield. You can only play one land per turn, during your main phase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_name": {
                        "type": "string",
                        "description": "Exact name of the land card in your hand",
                    }
                },
                "required": ["card_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cast_spell",
            "description": "Cast a spell (creature, instant, sorcery, enchantment) from your hand. Mana will be tapped automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_name": {
                        "type": "string",
                        "description": "Exact name of the card to cast",
                    },
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Card names or player names this spell targets (empty if no targets)",
                    },
                },
                "required": ["card_name", "targets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_attackers",
            "description": "Declare which of your creatures will attack this turn. Only call during your combat phase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Names of your creatures that will attack",
                    }
                },
                "required": ["card_names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_blockers",
            "description": "Declare how your creatures block attacking creatures. Call during the opponent's combat phase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "assignments": {
                        "type": "object",
                        "description": 'Mapping of attacker name to list of blocker names. Example: {"Goblin Piker": ["Grizzly Bears"]}',
                        "additionalProperties": {"type": "array", "items": {"type": "string"}},
                    }
                },
                "required": ["assignments"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pass_priority",
            "description": "Pass priority / end your action for this phase. Use when you have no more actions to take.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "concede",
            "description": "Concede the game.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_game_state",
            "description": "Get the current public game state (life totals, battlefields, graveyards).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hand",
            "description": "Get the cards currently in your hand.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "appeal",
            "description": "Request a judge ruling on a disputed game situation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "situation": {
                        "type": "string",
                        "description": "Describe the rules question or dispute in detail",
                    }
                },
                "required": ["situation"],
            },
        },
    },
]


class ActionError(Exception):
    pass


def dispatch_tool(
    tool_name: str,
    args: dict,
    state: GameState,
    player_name: str,
    event_log: EventLog,
) -> str:
    """Execute a tool call. Returns a result string for the LLM. May raise ActionError."""
    player = state.players[player_name]

    if tool_name == "get_game_state":
        import json

        return json.dumps(state.to_public_dict(), indent=2)

    if tool_name == "get_hand":
        import json

        return json.dumps(player.to_private_dict()["hand"], indent=2)

    if tool_name == "pass_priority":
        event_log.append(PRIORITY_PASS, {"player": player_name})
        return "Priority passed."

    if tool_name == "concede":
        opponent = state.opponent_of(player_name)
        state.game_over = True
        state.winner = opponent
        event_log.append("concede", {"player": player_name, "winner": opponent})
        return f"{player_name} concedes. {opponent} wins."

    if tool_name == "play_land":
        card_name = args.get("card_name", "")
        card = player.hand_card_by_name(card_name)
        if card is None:
            return f"Error: '{card_name}' not found in your hand. Your hand: {[c.name for c in player.hand]}"
        if not card.definition.is_land():
            return f"Error: '{card_name}' is not a land."
        ok, reason = can_play_land(state, player_name)
        if not ok:
            return f"Error: {reason}"
        player.hand.remove(card)
        player.battlefield.append(card)
        player.lands_played_this_turn += 1
        event_log.append(
            CARD_PLAYED, {"player": player_name, "card": card.name, "targets": [], "type": "land"}
        )
        return f"Played {card.name} onto the battlefield."

    if tool_name == "cast_spell":
        card_name = args.get("card_name", "")
        targets = args.get("targets", [])
        card = player.hand_card_by_name(card_name)
        if card is None:
            return f"Error: '{card_name}' not found in your hand. Your hand: {[c.name for c in player.hand]}"
        if card.definition.is_land():
            return "Error: use play_land to play lands."
        ok, reason = can_cast_spell(state, player_name, card)
        if not ok:
            return f"Error: {reason}"

        cost = parse_mana_cost(card.definition.mana_cost)
        paid = spend_mana_for_cost(player, cost)
        if not paid:
            return f"Error: failed to pay mana cost {card.definition.mana_cost}"

        event_log.append(MANA_TAPPED, {"player": player_name, "cost": card.definition.mana_cost})
        player.hand.remove(card)

        result_msg = f"Cast {card.name}."
        event_log.append(
            CARD_PLAYED,
            {
                "player": player_name,
                "card": card.name,
                "targets": targets,
                "type": card.definition.type_line,
            },
        )

        # Resolve immediately for creatures/permanents; for instants/sorceries apply effects
        if (
            card.definition.is_creature()
            or card.definition.is_enchantment()
            or card.definition.is_artifact()
        ):
            card.entered_this_turn = True
            player.battlefield.append(card)
            result_msg += f" {card.name} enters the battlefield."
        else:
            # Apply spell effects and put in graveyard
            effect_msg = _apply_spell_effect(card, targets, state, player_name, event_log)
            player.graveyard.append(card)
            result_msg += f" {effect_msg}"

        return result_msg

    if tool_name == "declare_attackers":
        card_names = args.get("card_names", [])
        if state.phase != "combat":
            return "Error: not in combat phase."
        if state.active_player != player_name:
            return "Error: not your turn."

        attackers: list[CardInstance] = []
        for name in card_names:
            c = player.battlefield_card_by_name(name)
            if c is None:
                return f"Error: '{name}' not found on your battlefield."
            ok, reason = can_attack(c, state, player_name)
            if not ok:
                return f"Error: {name} cannot attack: {reason}"
            attackers.append(c)

        # Tap attackers (unless they have vigilance)
        for c in attackers:
            if "Vigilance" not in c.definition.keywords:
                c.tapped = True
            state.declared_attackers.append(c.instance_id)

        event_log.append(ATTACK, {"player": player_name, "attackers": [c.name for c in attackers]})
        if not attackers:
            return "No attackers declared."
        return f"Declared attackers: {', '.join(c.name for c in attackers)}"

    if tool_name == "declare_blockers":
        assignments = args.get("assignments", {})
        opponent_name = state.opponent_of(player_name)
        if state.phase != "combat":
            return "Error: not in combat phase."
        if state.active_player != opponent_name:
            return "Error: can only block on opponent's combat phase."

        for attacker_name, blocker_names in assignments.items():
            # Find attacker by name
            attacker = None
            for inst_id in state.declared_attackers:
                for c in state.players[opponent_name].battlefield:
                    if c.instance_id == inst_id and c.name.lower() == attacker_name.lower():
                        attacker = c
                        break
            if attacker is None:
                return f"Error: '{attacker_name}' is not an attacker."

            for blocker_name in blocker_names:
                blocker = player.battlefield_card_by_name(blocker_name)
                if blocker is None:
                    return f"Error: '{blocker_name}' not found on your battlefield."
                ok, reason = can_block(blocker, attacker)
                if not ok:
                    return f"Error: {blocker_name} can't block {attacker_name}: {reason}"
                state.declared_blockers.setdefault(attacker.instance_id, []).append(
                    blocker.instance_id
                )

        event_log.append(BLOCK, {"player": player_name, "assignments": assignments})
        return f"Blockers declared: {assignments}"

    if tool_name == "appeal":
        return "_appeal_"  # sentinel; handled by caller

    return f"Error: unknown tool '{tool_name}'"


def _apply_spell_effect(
    card: CardInstance,
    targets: list[str],
    state: GameState,
    player_name: str,
    event_log: EventLog,
) -> str:
    """Apply effect of an instant/sorcery. Returns description string."""
    name = card.name.lower()

    # --- Damage spells ---
    if name in ("shock",):
        return _deal_damage(card.name, targets, 2, state, player_name, event_log)
    if name in ("lightning bolt",):
        return _deal_damage(card.name, targets, 3, state, player_name, event_log)
    if name in ("scorching dragonfire",):
        return _deal_damage(
            card.name, targets, 3, state, player_name, event_log, exile_on_death=True
        )
    if name in ("rabid bite",):
        # Target creature fights one of your creatures
        return _rabid_bite(card.name, targets, state, player_name, event_log)
    if name in ("giant growth",):
        return _buff_creature(targets, 3, 3, state, player_name, event_log)
    if name in ("murder",):
        return _destroy_creature(targets, state, player_name, event_log)
    if name in ("sign in blood",):
        target = targets[0] if targets else player_name
        return _draw_and_lose_life(target, 2, 2, state, event_log)
    if name in ("commune with nature",):
        # Look at top 5 cards, put a creature into hand
        return _commune_with_nature(state.players[player_name], event_log)
    if name in ("cemetery recruitment",):
        return _return_creature_from_graveyard(state.players[player_name], event_log)
    if name in ("dark ritual",):
        state.players[player_name].mana_pool["B"] = (
            state.players[player_name].mana_pool.get("B", 0) + 3
        )
        return "Added {B}{B}{B} to your mana pool."
    if name in ("raise the alarm",):
        return _create_tokens("Soldier", 1, 1, 2, state.players[player_name], event_log)
    if name in ("might of the masses",):
        # +1/+1 for each creature you control
        n = len(state.players[player_name].creatures())
        return _buff_creature(targets, n, n, state, player_name, event_log)

    # Default: no specific effect implemented
    return f"Effect of {card.name} resolved (effect not specifically implemented; judge may rule)."


def _deal_damage(
    source: str,
    targets: list[str],
    amount: int,
    state: GameState,
    player_name: str,
    event_log: EventLog,
    exile_on_death: bool = False,
) -> str:
    if not targets:
        return f"Error: {source} requires a target."
    target_name = targets[0]

    # Target is a player?
    if target_name.lower() in (n.lower() for n in state.players):
        target_player_name = next(n for n in state.players if n.lower() == target_name.lower())
        p = state.players[target_player_name]
        before = p.life
        p.life -= amount
        event_log.append(DAMAGE, {"source": source, "target": target_player_name, "amount": amount})
        event_log.append(
            LIFE_CHANGE, {"player": target_player_name, "before": before, "after": p.life}
        )
        if p.life <= 0:
            state.game_over = True
            state.winner = player_name
        return f"Dealt {amount} damage to {target_player_name}. They're at {p.life} life."

    # Target is a creature?
    for pname, pstate in state.players.items():
        target_card = pstate.battlefield_card_by_name(target_name)
        if target_card:
            target_card.damage_marked += amount
            event_log.append(DAMAGE, {"source": source, "target": target_name, "amount": amount})
            if target_card.is_dead:
                pstate.battlefield.remove(target_card)
                if exile_on_death:
                    pstate.exile.append(target_card)
                else:
                    pstate.graveyard.append(target_card)
                event_log.append(CARD_DIES, {"card": target_card.name, "controller": pname})
                return f"Dealt {amount} damage to {target_name}. It dies."
            return f"Dealt {amount} damage to {target_name} ({target_card.damage_marked}/{target_card.effective_toughness} damage marked)."

    return f"Error: target '{target_name}' not found."


def _rabid_bite(
    source: str, targets: list[str], state: GameState, player_name: str, event_log: EventLog
) -> str:
    if len(targets) < 2:
        return "Error: Rabid Bite requires two targets: your creature and the target creature."
    attacker_name, defender_name = targets[0], targets[1]
    player = state.players[player_name]
    attacker = player.battlefield_card_by_name(attacker_name)
    if attacker is None or not attacker.definition.is_creature():
        return f"Error: '{attacker_name}' not found on your battlefield."
    # Find defender on any battlefield
    for pname, pstate in state.players.items():
        defender = pstate.battlefield_card_by_name(defender_name)
        if defender:
            defender.damage_marked += attacker.effective_power
            event_log.append(
                DAMAGE,
                {
                    "source": attacker_name,
                    "target": defender_name,
                    "amount": attacker.effective_power,
                },
            )
            if defender.is_dead:
                pstate.battlefield.remove(defender)
                pstate.graveyard.append(defender)
                event_log.append(CARD_DIES, {"card": defender.name, "controller": pname})
                return f"{attacker_name} deals {attacker.effective_power} damage to {defender_name}. It dies."
            return f"{attacker_name} deals {attacker.effective_power} damage to {defender_name}."
    return f"Error: '{defender_name}' not found."


def _buff_creature(
    targets: list[str],
    power_mod: int,
    tough_mod: int,
    state: GameState,
    player_name: str,
    event_log: EventLog,
) -> str:
    if not targets:
        return "Error: requires a target creature."
    target_name = targets[0]
    player = state.players[player_name]
    card = player.battlefield_card_by_name(target_name)
    if card is None:
        return f"Error: '{target_name}' not found on your battlefield."
    card.power_mod += power_mod
    card.toughness_mod += tough_mod
    return f"{target_name} gets +{power_mod}/+{tough_mod} until end of turn."


def _destroy_creature(
    targets: list[str], state: GameState, player_name: str, event_log: EventLog
) -> str:
    if not targets:
        return "Error: Murder requires a target creature."
    target_name = targets[0]
    for pname, pstate in state.players.items():
        card = pstate.battlefield_card_by_name(target_name)
        if card:
            pstate.battlefield.remove(card)
            pstate.graveyard.append(card)
            event_log.append(CARD_DIES, {"card": card.name, "controller": pname})
            return f"{card.name} is destroyed."
    return f"Error: '{target_name}' not found on any battlefield."


def _draw_and_lose_life(
    target_player: str, draw_count: int, life_loss: int, state: GameState, event_log: EventLog
) -> str:
    if target_player not in state.players:
        target_player = next(iter(state.players))
    p = state.players[target_player]
    drawn = []
    for _ in range(draw_count):
        if p.library:
            card = p.library.pop(0)
            p.hand.append(card)
            drawn.append(card.name)
            event_log.append(DRAW, {"player": target_player, "card": card.name})
    before = p.life
    p.life -= life_loss
    event_log.append(LIFE_CHANGE, {"player": target_player, "before": before, "after": p.life})
    if p.life <= 0:
        state.game_over = True
        opp = state.opponent_of(target_player)
        state.winner = opp
    return f"{target_player} draws {draw_count} and loses {life_loss} life (now at {p.life})."


def _commune_with_nature(player: PlayerState, event_log: EventLog) -> str:
    top5 = player.library[:5]
    creatures = [c for c in top5 if c.definition.is_creature()]
    if creatures:
        chosen = creatures[0]
        player.library.remove(chosen)
        player.hand.append(chosen)
        # Put the rest back in random order
        import random

        non_chosen = [c for c in top5 if c is not chosen]
        random.shuffle(non_chosen)
        player.library = non_chosen + player.library[5 - len(non_chosen) :]
        event_log.append(DRAW, {"player": player.name, "card": chosen.name})
        return f"Found and added {chosen.name} to hand."
    return "No creatures in top 5 cards. Cards remain in library."


def _return_creature_from_graveyard(player: PlayerState, event_log: EventLog) -> str:
    creatures = [c for c in player.graveyard if c.definition.is_creature()]
    if not creatures:
        return "No creatures in graveyard to return."
    chosen = creatures[0]
    player.graveyard.remove(chosen)
    player.hand.append(chosen)
    return f"Returned {chosen.name} from graveyard to hand."


def _create_tokens(
    token_type: str,
    power: int,
    toughness: int,
    count: int,
    player: PlayerState,
    event_log: EventLog,
) -> str:
    from game.state import CardDef, CardInstance

    for i in range(count):
        defn = CardDef(
            name=f"{token_type} Token",
            scryfall_id=f"token-{token_type.lower()}-{i}",
            mana_cost="",
            cmc=0,
            type_line=f"Token Creature — {token_type}",
            oracle_text="",
            power=str(power),
            toughness=str(toughness),
            keywords=[],
            colors=[],
        )
        token = CardInstance.from_def(defn)
        token.entered_this_turn = True
        player.battlefield.append(token)
    return f"Created {count} {power}/{toughness} {token_type} token(s)."


def resolve_combat_damage(state: GameState, event_log: EventLog) -> str:
    """Resolve damage for all declared attackers/blockers."""
    if not state.declared_attackers:
        return "No attackers."

    attacker_player = state.active_player
    defender_player = state.opponent_of(attacker_player)
    attacker_state = state.players[attacker_player]
    defender_state = state.players[defender_player]

    messages: list[str] = []

    for attacker_id in state.declared_attackers:
        attacker = next(
            (c for c in attacker_state.battlefield if c.instance_id == attacker_id), None
        )
        if attacker is None:
            continue

        blocker_ids = state.declared_blockers.get(attacker_id, [])
        blockers = [c for c in defender_state.battlefield if c.instance_id in blocker_ids]

        if not blockers:
            # Unblocked — damage goes to defending player
            before = defender_state.life
            defender_state.life -= attacker.effective_power
            event_log.append(
                DAMAGE,
                {
                    "source": attacker.name,
                    "target": defender_player,
                    "amount": attacker.effective_power,
                },
            )
            event_log.append(
                LIFE_CHANGE,
                {"player": defender_player, "before": before, "after": defender_state.life},
            )
            messages.append(
                f"{attacker.name} deals {attacker.effective_power} to {defender_player} (now {defender_state.life} life)"
            )
            if defender_state.life <= 0:
                state.game_over = True
                state.winner = attacker_player
        else:
            # Blocked — attacker and blockers deal damage to each other
            total_blocker_power = sum(b.effective_power for b in blockers)
            attacker.damage_marked += total_blocker_power

            for blocker in blockers:
                blocker.damage_marked += attacker.effective_power
                event_log.append(
                    DAMAGE,
                    {
                        "source": attacker.name,
                        "target": blocker.name,
                        "amount": attacker.effective_power,
                    },
                )

            event_log.append(
                DAMAGE,
                {
                    "source": ", ".join(b.name for b in blockers),
                    "target": attacker.name,
                    "amount": total_blocker_power,
                },
            )

            # Check deaths
            if attacker.is_dead:
                attacker_state.battlefield.remove(attacker)
                attacker_state.graveyard.append(attacker)
                event_log.append(CARD_DIES, {"card": attacker.name, "controller": attacker_player})
                messages.append(f"{attacker.name} dies in combat")

            dead_blockers = [b for b in blockers if b.is_dead]
            for b in dead_blockers:
                if b in defender_state.battlefield:
                    defender_state.battlefield.remove(b)
                    defender_state.graveyard.append(b)
                    event_log.append(CARD_DIES, {"card": b.name, "controller": defender_player})
                    messages.append(f"{b.name} dies in combat")

    # Clear combat state
    state.declared_attackers = []
    state.declared_blockers = {}

    if not messages:
        return "Combat resolved with no notable events."
    return "Combat: " + "; ".join(messages)
