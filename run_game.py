"""CLI entrypoint: run a game between two LLM players."""

from __future__ import annotations

import argparse
import random
import uuid
from pathlib import Path

from cards.loader import make_player
from game.actions import resolve_combat_damage
from game.events import DRAW, GAME_OVER, INSTANT_WINDOW, TURN_START, EventLog
from game.rules import has_instant_options
from game.state import GameState
from llm.client import DEFAULT_MODEL, make_client
from llm.commentator import CommentatorAgent
from llm.player import PlayerAgent
from llm.prompts import format_game_state_for_player


def _clear_mana_pools(state: GameState) -> None:
    for p in state.players.values():
        p.mana_pool = {}


def _offer_instant_window(
    responder_name: str,
    responder_agent: PlayerAgent,
    state: GameState,
    event_log: EventLog,
    context_prefix: str,
    verbose: bool,
) -> None:
    """Offer responder a priority window to cast instants. No-ops if nothing castable."""
    responder = state.players[responder_name]
    if not has_instant_options(responder, state, responder_name):
        return
    event_log.append(INSTANT_WINDOW, {"player": responder_name})
    game_state_str = format_game_state_for_player(state, responder_name)
    context = (
        f"INSTANT WINDOW\n\n{game_state_str}\n\n"
        f"{context_prefix}\n"
        f"Your hand: {[c.name for c in responder.hand]}\n\n"
        "You may cast instants now, or pass_priority."
    )
    if verbose:
        print(f"\n  [INSTANT WINDOW] {responder_name} may respond")
    responder_agent.take_action(state, context, verbose=verbose)


def initialize_game(
    player_a: str, player_b: str, deck_a: str, deck_b: str, game_id: str
) -> GameState:
    pa = make_player(player_a, deck_a)
    pb = make_player(player_b, deck_b)

    # Each player draws 7 cards
    for p in (pa, pb):
        for _ in range(7):
            if p.library:
                card = p.library.pop(0)
                p.hand.append(card)

    order = [player_a, player_b]
    random.shuffle(order)

    state = GameState(
        game_id=game_id,
        players={player_a: pa, player_b: pb},
        player_order=order,
        turn=0,
        active_player=order[0],
        phase="main1",
    )
    return state


def run_turn(
    state: GameState,
    agents: dict[str, PlayerAgent],
    commentator: CommentatorAgent,
    event_log: EventLog,
    verbose: bool,
) -> None:
    active = state.active_player
    defending = state.opponent_of(active)
    agent = agents[active]
    def_agent = agents[defending]

    state.turn += 1
    event_log.set_context(state.turn, "untap")
    event_log.append(TURN_START, {"active_player": active, "turn": state.turn})

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"TURN {state.turn} — {active} (vs {defending})")
        print(
            f"  {active}: {state.players[active].life} life | {defending}: {state.players[defending].life} life"
        )
        print(f"  {active} battlefield: {[c.name for c in state.players[active].battlefield]}")
        print(
            f"  {defending} battlefield: {[c.name for c in state.players[defending].battlefield]}"
        )

    # 1. Untap
    state.phase = "untap"
    event_log.set_context(state.turn, "untap")
    player_state = state.players[active]
    for card in player_state.battlefield:
        card.tapped = False
        card.entered_this_turn = False
        card.damage_marked = 0
    player_state.lands_played_this_turn = 0
    player_state.mana_pool = {}

    # 2. Draw
    state.phase = "draw"
    event_log.set_context(state.turn, "draw")
    if player_state.library:
        drawn = player_state.library.pop(0)
        player_state.hand.append(drawn)
        event_log.append(DRAW, {"player": active, "card": drawn.name})
        if verbose:
            print(f"  {active} draws {drawn.name}")
    else:
        # Empty library = loss
        state.game_over = True
        state.winner = defending
        event_log.append(GAME_OVER, {"winner": defending, "reason": "empty_library"})
        return

    # 3. Main Phase 1
    state.phase = "main1"
    event_log.set_context(state.turn, "main1")
    if verbose:
        print(f"\n  [MAIN 1] {active}'s turn")
    game_state_str = format_game_state_for_player(state, active)
    hand_summary = [c.name for c in player_state.hand]
    context = (
        f"MAIN PHASE 1\n\n{game_state_str}\n\n"
        f"Your hand: {hand_summary}\n\n"
        "You may play a land, cast spells, or pass priority to proceed to combat."
    )
    agent.take_action(state, context, verbose=verbose)
    if state.game_over:
        return

    # Defender may respond with instants before combat
    _clear_mana_pools(state)
    _offer_instant_window(
        defending,
        def_agent,
        state,
        event_log,
        f"{active} passed priority after main phase 1. You may cast instants before combat begins.",
        verbose,
    )
    if state.game_over:
        return
    _clear_mana_pools(state)

    # 4. Combat
    state.phase = "combat"
    event_log.set_context(state.turn, "combat")
    state.declared_attackers = []
    state.declared_blockers = {}

    attackable_creatures = [c for c in player_state.battlefield if c.can_attack()]
    if attackable_creatures:
        if verbose:
            print(f"\n  [COMBAT] {active} may attack")
        game_state_str = format_game_state_for_player(state, active)
        context = (
            f"COMBAT PHASE\n\n{game_state_str}\n\n"
            f"Your hand: {[c.name for c in player_state.hand]}\n\n"
            f"Creatures that can attack: {[c.name for c in attackable_creatures]}\n"
            "Use declare_attackers to attack, or pass_priority to skip combat."
        )
        agent.take_action(state, context, verbose=verbose)
        if state.game_over:
            return

        if state.declared_attackers:
            # Defending player declares blockers
            if verbose:
                print(f"\n  [COMBAT] {defending} declares blockers")
            def_agent.ask_for_blockers(state, verbose=verbose)
            if state.game_over:
                return

            # Combat trick window: active player first, then defender
            _clear_mana_pools(state)
            attacker_names = [
                c.name
                for c in player_state.battlefield
                if c.instance_id in state.declared_attackers
            ]
            _offer_instant_window(
                active,
                agent,
                state,
                event_log,
                f"Blockers have been declared. Attacking with: {', '.join(attacker_names)}. You may cast combat tricks.",
                verbose,
            )
            if state.game_over:
                return
            _offer_instant_window(
                defending,
                def_agent,
                state,
                event_log,
                f"Blockers declared. {active} may cast combat tricks. You may also respond with instants.",
                verbose,
            )
            if state.game_over:
                return
            _clear_mana_pools(state)

            # Resolve damage
            result = resolve_combat_damage(state, event_log)
            if verbose:
                print(f"  {result}")
            if state.game_over:
                return

    _clear_mana_pools(state)

    # 5. Main Phase 2
    state.phase = "main2"
    event_log.set_context(state.turn, "main2")
    if verbose:
        print(f"\n  [MAIN 2] {active}'s turn")
    game_state_str = format_game_state_for_player(state, active)
    context = (
        f"MAIN PHASE 2\n\n{game_state_str}\n\n"
        f"Your hand: {[c.name for c in player_state.hand]}\n\n"
        "You may play a land (if you haven't), cast spells, or pass priority to end your turn."
    )
    agent.take_action(state, context, verbose=verbose)
    if state.game_over:
        return

    # Defender may respond with instants before end step
    _clear_mana_pools(state)
    _offer_instant_window(
        defending,
        def_agent,
        state,
        event_log,
        f"{active} passed priority after main phase 2. You may cast instants before the end step.",
        verbose,
    )
    if state.game_over:
        return
    _clear_mana_pools(state)

    # 6. End step — discard to 7
    state.phase = "end"
    event_log.set_context(state.turn, "end")
    while len(player_state.hand) > 7:
        # Discard the last card (LLM could choose, but for now auto-discard)
        discarded = player_state.hand.pop()
        player_state.graveyard.append(discarded)
        if verbose:
            print(f"  {active} discards {discarded.name} (hand size limit)")

    # End of turn — clear temporary effects (buffs from Giant Growth etc. are gone)
    for card in player_state.battlefield:
        card.power_mod = 0
        card.toughness_mod = 0

    # Commentary
    commentator.comment_on_turn(state, verbose=verbose)

    # Next player's turn
    idx = state.player_order.index(active)
    state.active_player = state.player_order[(idx + 1) % len(state.player_order)]

    # Check life totals
    for pname, pstate in state.players.items():
        if pstate.life <= 0:
            state.game_over = True
            state.winner = state.opponent_of(pname)
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an LLM MTG game")
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--player-a", default="Aria")
    parser.add_argument("--player-b", default="Bryn")
    parser.add_argument("--deck-a", default="forces_of_the_wild")
    parser.add_argument("--deck-b", default="chaos_of_the_undead")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.quiet:
        args.verbose = False

    game_id = args.game_id or str(uuid.uuid4())
    log_path = Path("games") / f"{game_id}.jsonl"

    print(f"Starting game {game_id}")
    print(f"  {args.player_a} ({args.deck_a}) vs {args.player_b} ({args.deck_b})")
    print(f"  Model: {args.model}")
    print(f"  Log: {log_path}\n")

    event_log = EventLog(game_id, log_path)
    state = initialize_game(args.player_a, args.player_b, args.deck_a, args.deck_b, game_id)

    client = make_client()
    agents = {
        args.player_a: PlayerAgent(args.player_a, args.model, client, event_log),
        args.player_b: PlayerAgent(args.player_b, args.model, client, event_log),
    }
    commentator = CommentatorAgent(client, event_log, model=args.model)

    print(f"Going first: {state.active_player}\n")

    turn_count = 0
    while not state.game_over and turn_count < args.max_turns:
        run_turn(state, agents, commentator, event_log, verbose=args.verbose)
        turn_count += 1

    if not state.game_over:
        print(f"\nGame ended after {args.max_turns} turns (draw/timeout)")
        event_log.append(GAME_OVER, {"winner": None, "reason": "turn_limit"})
    else:
        print(f"\n{'=' * 60}")
        print(f"GAME OVER — Winner: {state.winner}")
        print(f"Turns played: {turn_count}")
        event_log.append(GAME_OVER, {"winner": state.winner, "reason": "normal"})

    print(f"Replay log: {log_path}")
    event_log.close()


if __name__ == "__main__":
    main()
