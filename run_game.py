"""CLI entrypoint: run a game between two LLM players via Argentum Engine."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from cards.decks import DECK_NAMES, get_deck
from game.events import GAME_OVER, EventLog
from llm import argentum
from llm.client import DEFAULT_MODEL, make_client
from llm.commentator import CommentatorAgent
from llm.player import PlayerAgent


def run_game(
    player_a: str,
    player_b: str,
    deck_a: str,
    deck_b: str,
    model: str,
    max_steps: int,
    game_id: str,
    verbose: bool,
) -> None:
    log_path = Path("games") / f"{game_id}.jsonl"
    event_log = EventLog(game_id, log_path)

    print(f"Starting game {game_id}")
    print(f"  {player_a} ({deck_a}) vs {player_b} ({deck_b})")
    print(f"  Model: {model} | Max steps: {max_steps}")
    print(f"  Log: {log_path}\n")

    if not argentum.health():
        print("ERROR: Argentum gym-server not reachable at", argentum.ARGENTUM_HOST)
        print("Start it with: JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 just gym-server")
        return

    client = make_client()
    agents = {
        player_a: PlayerAgent(player_a, model, client, event_log),
        player_b: PlayerAgent(player_b, model, client, event_log),
    }
    commentator = CommentatorAgent(client, event_log, model=model)

    env_id, obs = argentum.create_env(
        player_a,
        get_deck(deck_a),
        player_b,
        get_deck(deck_b),
        reveal_all=True,
    )

    if verbose:
        print(f"Env created: {env_id}")
        print(f"Players: {[p['name'] for p in obs['players']]}")
        going_first = next((p["name"] for p in obs["players"] if p.get("isActive")), "?")
        print(f"Going first: {going_first}\n")

    current_turn = obs.get("turnNumber", 0)
    step_count = 0

    try:
        while step_count < max_steps:
            if obs.get("terminated"):
                break

            agent_id = obs.get("agentToAct")
            if agent_id is None:
                break

            # Resolve agent name from id
            acting_name = next((p["name"] for p in obs["players"] if p["id"] == agent_id), None)
            if acting_name is None:
                break

            # Turn boundary: fire commentator when turn increments
            new_turn = obs.get("turnNumber", 0)
            if new_turn > current_turn and current_turn > 0:
                commentator.comment_on_turn(obs, current_turn, verbose=verbose)
            if new_turn > current_turn:
                current_turn = new_turn
                if verbose:
                    phase = obs.get("phase", "?")
                    step = obs.get("step", "?")
                    lives = {p["name"]: p["lifeTotal"] for p in obs["players"]}
                    print(f"\n{'=' * 60}")
                    print(f"TURN {current_turn} | {phase}/{step} | {lives}")

            agent = agents.get(acting_name)
            if agent is None:
                break

            # Show phase header on transitions in verbose mode
            if verbose:
                phase = obs.get("phase", "?")
                step_name = obs.get("step", "?")
                legal_count = len(obs.get("legalActions", []))
                print(f"\n  [{acting_name}] {phase}/{step_name} — {legal_count} actions")

            action_id = agent.choose_action(obs, verbose=verbose)

            if verbose:
                chosen = next(
                    (a for a in obs.get("legalActions", []) if a["actionId"] == action_id),
                    None,
                )
                desc = chosen["description"] if chosen else str(action_id)
                print(f"  [{acting_name}] -> {desc}")

            obs = argentum.step(env_id, action_id)
            step_count += 1

    finally:
        # Final commentary
        commentator.comment_on_turn(obs, current_turn, verbose=verbose)

        print(f"\n{'=' * 60}")
        if obs.get("terminated"):
            winner_id = obs.get("winnerId")
            winner = next((p["name"] for p in obs["players"] if p["id"] == winner_id), None)
            print(f"GAME OVER — Winner: {winner or 'draw'}")
            event_log.append(GAME_OVER, {"winner": winner, "reason": "normal"})
        else:
            print(f"Game stopped after {step_count} steps (limit reached)")
            event_log.append(GAME_OVER, {"winner": None, "reason": "step_limit"})

        print(f"Steps taken: {step_count}")
        print(f"Replay log: {log_path}")
        event_log.close()
        argentum.dispose(env_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an LLM MTG game via Argentum Engine")
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--player-a", default="Aria")
    parser.add_argument("--player-b", default="Bryn")
    parser.add_argument("--deck-a", default="red_rush", choices=DECK_NAMES)
    parser.add_argument("--deck-b", default="green_might", choices=DECK_NAMES)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--quiet", action="store_false", dest="verbose")
    args = parser.parse_args()

    game_id = args.game_id or str(uuid.uuid4())
    run_game(
        player_a=args.player_a,
        player_b=args.player_b,
        deck_a=args.deck_a,
        deck_b=args.deck_b,
        model=args.model,
        max_steps=args.max_steps,
        game_id=game_id,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
