"""CLI entrypoint: run a game between two persona-driven LLM players."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from cards.decks import DECK_NAMES, get_deck
from game.events import GAME_OVER, EventLog
from llm import argentum
from llm.client import DEFAULT_MODEL, make_client
from llm.commentator import CommentatorAgent
from llm.meta import reflect_after_game, write_pre_game_strategy
from llm.persona import Persona
from llm.player import PlayerAgent


def run_game(
    persona_a_name: str,
    persona_b_name: str,
    deck_a_name: str,
    deck_b_name: str,
    model: str,
    max_steps: int,
    game_id: str,
    verbose: bool,
) -> None:
    persona_a = Persona(persona_a_name)
    persona_b = Persona(persona_b_name)
    deck_a = get_deck(deck_a_name)
    deck_b = get_deck(deck_b_name)

    game_dir = Path("games") / game_id
    game_dir.mkdir(parents=True, exist_ok=True)
    log_path = game_dir / "game.jsonl"
    event_log = EventLog(game_id, log_path)

    print(f"Starting game {game_id}")
    print(f"  {persona_a.name} ({deck_a_name}) vs {persona_b.name} ({deck_b_name})")
    print(f"  Model: {model} | Max steps: {max_steps}")
    print(f"  Log: {log_path}\n")

    if not argentum.health():
        print("ERROR: Argentum gym-server not reachable at", argentum.ARGENTUM_HOST)
        print("Start it with: JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 just gym-server")
        return

    client = make_client()

    # Snapshot the personas' state at game start so the replay viewer can
    # show what each agent knew going in (independent of any post-game edits).
    persona_a.snapshot_to(game_dir / "personas" / persona_a.name)
    persona_b.snapshot_to(game_dir / "personas" / persona_b.name)

    # Pre-game strategy session for each persona. Writes to strategy.md.
    if verbose:
        print(f"  [pre-game] {persona_a.name} planning strategy...")
    write_pre_game_strategy(
        persona=persona_a,
        opponent_name=persona_b.name,
        deck=deck_a,
        client=client,
        event_log=event_log,
        model=model,
        verbose=verbose,
    )
    if verbose:
        print(f"  [pre-game] {persona_b.name} planning strategy...")
    write_pre_game_strategy(
        persona=persona_b,
        opponent_name=persona_a.name,
        deck=deck_b,
        client=client,
        event_log=event_log,
        model=model,
        verbose=verbose,
    )

    # Re-snapshot now that strategy.md has been written, so the viewer's
    # "what they knew" view includes the strategy they walked in with.
    persona_a.snapshot_to(game_dir / "personas" / persona_a.name)
    persona_b.snapshot_to(game_dir / "personas" / persona_b.name)

    agents = {
        persona_a.name: PlayerAgent(persona_a, persona_b.name, model, client, event_log),
        persona_b.name: PlayerAgent(persona_b, persona_a.name, model, client, event_log),
    }
    commentator = CommentatorAgent(client, event_log, model=model)

    env_id, obs = argentum.create_env(
        persona_a.name,
        deck_a,
        persona_b.name,
        deck_b,
        reveal_all=True,
    )

    if verbose:
        print(f"\nEnv created: {env_id}")
        going_first = next((p["name"] for p in obs["players"] if p.get("isActive")), "?")
        print(f"Going first: {going_first}\n")

    current_turn = obs.get("turnNumber", 0)
    step_count = 0
    stop_reason = "step_limit"
    winner_name: str | None = None

    try:
        while step_count < max_steps:
            if obs.get("terminated"):
                stop_reason = "normal"
                break

            agent_id = obs.get("agentToAct")
            if agent_id is None:
                stop_reason = "no_agent_to_act"
                break

            acting_name = next((p["name"] for p in obs["players"] if p["id"] == agent_id), None)
            if acting_name is None:
                stop_reason = "unknown_acting_player"
                break

            phase = obs.get("phase", "?")
            step_name = obs.get("step", "?")
            event_log.set_context(turn=obs.get("turnNumber", 0), phase=f"{phase}/{step_name}")

            new_turn = obs.get("turnNumber", 0)
            if new_turn > current_turn:
                if current_turn > 0:
                    commentator.comment_on_turn(obs, current_turn, verbose=verbose)
                current_turn = new_turn
                if verbose:
                    lives = {p["name"]: p["lifeTotal"] for p in obs["players"]}
                    print(f"\n{'=' * 60}")
                    print(f"TURN {current_turn} | {phase}/{step_name} | {lives}")

            agent = agents.get(acting_name)
            if agent is None:
                stop_reason = "unknown_acting_player"
                break

            legal_actions = obs.get("legalActions", [])
            if not legal_actions:
                # Argentum is waiting on a structured decision (pendingDecision)
                # which our harness does not yet implement.
                stop_reason = "pending_decision_unsupported"
                break

            if verbose:
                print(f"\n  [{acting_name}] {phase}/{step_name} — {len(legal_actions)} actions")

            action_id = agent.choose_action(obs, verbose=verbose)

            if verbose:
                chosen = next((a for a in legal_actions if a["actionId"] == action_id), None)
                desc = chosen["description"] if chosen else str(action_id)
                print(f"  [{acting_name}] -> {desc}")

            try:
                obs = argentum.step(env_id, action_id)
            except Exception as e:
                stop_reason = f"argentum_error: {e}"
                break

            step_count += 1

    finally:
        commentator.comment_on_turn(obs, current_turn, verbose=verbose)

        print(f"\n{'=' * 60}")
        if obs.get("terminated"):
            winner_id = obs.get("winnerId")
            winner_name = next((p["name"] for p in obs["players"] if p["id"] == winner_id), None)
            print(f"GAME OVER — Winner: {winner_name or 'draw'}")
            event_log.append(GAME_OVER, {"winner": winner_name, "reason": "normal"})
        else:
            print(f"Game stopped after {step_count} steps ({stop_reason})")
            event_log.append(GAME_OVER, {"winner": None, "reason": stop_reason})

        print(f"Steps taken: {step_count}")
        print(f"Replay log: {log_path}")

        # Post-game reflection: only run on a normal termination, so the
        # reflector doesn't write a confused memory entry about a half-game.
        if stop_reason == "normal":
            for persona, opponent, agent in (
                (persona_a, persona_b.name, agents[persona_a.name]),
                (persona_b, persona_a.name, agents[persona_b.name]),
            ):
                won: bool | None = None if winner_name is None else winner_name == persona.name
                if verbose:
                    print(f"  [post-game] {persona.name} reflecting...")
                reflect_after_game(
                    persona=persona,
                    opponent_name=opponent,
                    won=won,
                    turn_count=current_turn,
                    scratchpad=list(agent.toolbox.scratchpad),
                    client=client,
                    event_log=event_log,
                    model=model,
                    verbose=verbose,
                )

        event_log.close()
        argentum.dispose(env_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an LLM MTG game via Argentum Engine")
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--persona-a", default="aria", help="persona slug (dir in personas/)")
    parser.add_argument("--persona-b", default="bryn", help="persona slug (dir in personas/)")
    parser.add_argument("--deck-a", default="red_rush", choices=DECK_NAMES)
    parser.add_argument("--deck-b", default="green_might", choices=DECK_NAMES)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--quiet", action="store_false", dest="verbose")
    args = parser.parse_args()

    game_id = args.game_id or str(uuid.uuid4())
    run_game(
        persona_a_name=args.persona_a,
        persona_b_name=args.persona_b,
        deck_a_name=args.deck_a,
        deck_b_name=args.deck_b,
        model=args.model,
        max_steps=args.max_steps,
        game_id=game_id,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
