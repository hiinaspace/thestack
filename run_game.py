"""CLI entrypoint: run a game between two persona-driven LLM players."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from cards.decks import DECK_NAMES, default_deck_for, get_deck
from game.events import ACTION, AUTOPASS, ENGINE_EVENT, GAME_OVER, OBSERVATION, EventLog
from llm import argentum
from llm.autopass import autopass_action_id, should_track_no_progress
from llm.client import DEFAULT_MODEL, make_client
from llm.commentator import CommentatorAgent
from llm.meta import reflect_after_game, write_pre_game_strategy
from llm.persona import Persona
from llm.player import PlayerAgent
from llm.random_agent import RandomAgent


def run_game(
    persona_a_name: str,
    persona_b_name: str,
    deck_a_name: str,
    deck_b_name: str,
    model: str,
    max_steps: int,
    game_id: str,
    verbose: bool,
    random_agents: bool = False,
    persona_a_backend: str = "ollama",
    persona_b_backend: str = "ollama",
    persona_a_model: str | None = None,
    persona_b_model: str | None = None,
    library_seed: int | None = None,
) -> None:
    persona_a = Persona(persona_a_name)
    persona_b = Persona(persona_b_name)
    deck_a = get_deck(deck_a_name)
    deck_b = get_deck(deck_b_name)
    deck_for_player: dict[str, str] = {
        persona_a.name: deck_a_name,
        persona_b.name: deck_b_name,
    }

    game_dir = Path("games") / game_id
    game_dir.mkdir(parents=True, exist_ok=True)
    log_path = game_dir / "game.jsonl"
    event_log = EventLog(game_id, log_path)

    print(f"Starting game {game_id}")
    print(f"  {persona_a.name} ({deck_a_name}) vs {persona_b.name} ({deck_b_name})")
    if random_agents:
        mode = "random"
    else:
        a_label = f"{persona_a_backend}:{persona_a_model or model}"
        b_label = f"{persona_b_backend}:{persona_b_model or model}"
        mode = f"LLM ({persona_a.name}={a_label} / {persona_b.name}={b_label})"
    print(f"  Mode: {mode} | Max steps: {max_steps}")
    print(f"  Log: {log_path}\n")

    if not argentum.health():
        print("ERROR: Argentum gym-server not reachable at", argentum.ARGENTUM_HOST)
        print("Start it with: JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 just gym-server")
        return

    client = None if random_agents else make_client()
    commentator = None
    sdk_agents: list = []  # Track ClaudePlayerAgents for explicit close() at game end

    if random_agents:
        agents: dict[str, object] = {
            persona_a.name: RandomAgent(persona_a.name, event_log, seed=1),
            persona_b.name: RandomAgent(persona_b.name, event_log, seed=2),
        }
    else:
        # Snapshot the personas' state at game start so the replay viewer can
        # show what each agent knew going in (independent of any post-game edits).
        persona_a.snapshot_to(game_dir / "personas" / persona_a.name)
        persona_b.snapshot_to(game_dir / "personas" / persona_b.name)

        # Pre-game strategy session for each persona. Writes to strategy.md.
        # Strategist + commentator + reflector stay on Ollama regardless of
        # the player backend — they're auxiliary voices that the player
        # backend choice shouldn't change.
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

        agents = {}
        for persona, opponent_name, deck, backend, model_override in (
            (persona_a, persona_b.name, deck_a, persona_a_backend, persona_a_model),
            (persona_b, persona_a.name, deck_b, persona_b_backend, persona_b_model),
        ):
            agent_model = model_override or model
            if backend == "anthropic_sdk":
                from llm.claude_agent_sdk_backend import ClaudePlayerAgent

                agent = ClaudePlayerAgent(
                    persona,
                    opponent_name,
                    agent_model,
                    event_log,
                    deck=deck,
                    verbose=verbose,
                )
                sdk_agents.append(agent)
            elif backend == "ollama":
                agent = PlayerAgent(
                    persona, opponent_name, agent_model, client, event_log, deck=deck
                )
            else:
                raise ValueError(
                    f"Unknown backend {backend!r} for {persona.name}; "
                    "expected 'ollama' or 'anthropic_sdk'."
                )
            agents[persona.name] = agent
        commentator = CommentatorAgent(client, event_log, model=model)

    env_id, obs = argentum.create_env(
        persona_a.name,
        deck_a,
        persona_b.name,
        deck_b,
        reveal_all=True,
        skip_mulligans=False,
        library_seed=library_seed,
    )
    event_log.append(OBSERVATION, {"obs": obs})

    if verbose:
        print(f"\nEnv created: {env_id}")
        going_first = next((p["name"] for p in obs["players"] if p.get("isActive")), "?")
        print(f"Going first: {going_first}\n")

    current_turn = obs.get("turnNumber", 0)
    all_player_names: list[str] = [p["name"] for p in obs.get("players", [])]
    step_count = 0
    stop_reason = "step_limit"
    winner_name: str | None = None
    # Actions taken this turn — drained when the commentator runs.
    turn_actions: list[dict] = []
    # Bounded public feed injected into each player's next decision prompt.
    public_action_history: list[dict] = []
    # If an action returns the exact same state digest, force a safe "done"
    # action on the repeat rather than paying for another identical LLM choice.
    no_progress_positions: set[tuple[str, str]] = set()
    # Pending react hooks — populated by engine_events from advance(), drained
    # when the relevant player's next decision-or-autopass moment comes up.
    # Draws are per-player (mapping name → CardsDrawn event); combat reactions
    # fire on both players in priority order after COMBAT_DAMAGE resolves.
    pending_draw_react: dict[str, dict] = {}
    pending_combat_react_for: set[str] = set()
    combat_react_events: list[dict] = []
    interrupted = False

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
                if commentator is not None and current_turn > 0:
                    commentator.comment_on_turn(
                        obs, current_turn, recent_actions=turn_actions, verbose=verbose
                    )
                turn_actions = []
                current_turn = new_turn
                # Clear per-turn state (turn_plan) on every agent's toolbox so
                # each player starts the new turn without a stale plan.
                for ag in agents.values():
                    tb = getattr(ag, "toolbox", None)
                    if tb is not None and hasattr(tb, "reset_for_new_turn"):
                        tb.reset_for_new_turn()
                if verbose:
                    lives = {p["name"]: p["lifeTotal"] for p in obs["players"]}
                    print(f"\n{'=' * 60}")
                    print(f"TURN {current_turn} | {phase}/{step_name} | {lives}")

            agent = agents.get(acting_name)
            if agent is None:
                stop_reason = "unknown_acting_player"
                break

            # ---- React hooks (draw + post-combat) ----------------------------
            # Fire BEFORE autopass/choose_action so the LLM gets the beat even
            # when the immediate next decision would otherwise be autopassed.
            # No new event types — react emits MONOLOGUE / TABLE_TALK via the
            # standard tool dispatcher and an ACTION row tagged as a reaction.
            if hasattr(agent, "react"):
                # Draw reaction: fires once per turn when the acting player
                # first reaches the main phase after their DRAW step. Skipped
                # on turn 1 (mulligan dominates that headspace).
                if acting_name in pending_draw_react and str(step_name).upper() == "PRECOMBAT_MAIN":
                    draw_event = pending_draw_react.pop(acting_name)
                    if current_turn > 1:
                        try:
                            agent.react(obs, "draw", [draw_event], verbose=verbose)
                        except Exception as e:
                            print(f"  [warn] {acting_name} draw react failed: {e}")
                # Combat reaction: fires once per combat for each player as
                # their priority window arrives after COMBAT_DAMAGE resolved.
                # Runs even on whiffs (no damage, no kills) per the cartoon
                # quip pattern — see plan deep-honking-gadget.
                if (
                    acting_name in pending_combat_react_for
                    and str(step_name).upper() != "COMBAT_DAMAGE"
                ):
                    pending_combat_react_for.discard(acting_name)
                    try:
                        agent.react(
                            obs, "combat_resolution", list(combat_react_events), verbose=verbose
                        )
                    except Exception as e:
                        print(f"  [warn] {acting_name} combat react failed: {e}")
                    if not pending_combat_react_for:
                        combat_react_events = []

            legal_actions = obs.get("legalActions", [])
            pending_decision = obs.get("pendingDecision") or {}
            # Capture any surfaced structured decision — pendingDecision can
            # appear with legal_actions too (engine enumerates each option as
            # a DECISION-kind legal action for short choice lists like
            # SELECT_CARDS over a 16-card library). The structured-only branch
            # below still requires legal_actions to be empty so a numbered
            # pick takes precedence when both shapes are available.
            if pending_decision.get("requiresStructuredResponse"):
                _capture_decision_fixture(
                    game_dir,
                    obs,
                    acting_name,
                    deck_for_player.get(acting_name, ""),
                )
            if not legal_actions and pending_decision.get("requiresStructuredResponse"):
                if verbose:
                    print(
                        f"\n  [{acting_name}] {phase}/{step_name} — "
                        f"structured {pending_decision.get('kind', 'decision')}"
                    )
                if not hasattr(agent, "choose_decision"):
                    stop_reason = "structured_decision_without_agent_support"
                    break
                if isinstance(agent, PlayerAgent):
                    decision_response = agent.choose_decision(
                        obs,
                        verbose=verbose,
                        recent_public_actions=public_action_history,
                    )
                else:
                    decision_response = agent.choose_decision(obs, verbose=verbose)
                toolbox = getattr(agent, "toolbox", None)
                action_record = {
                    "player": acting_name,
                    "action_id": None,
                    "description": (
                        f"{pending_decision.get('kind', 'DECISION')}: "
                        f"{pending_decision.get('prompt', '')}"
                    ),
                    "reasoning": getattr(agent, "last_reasoning", ""),
                    "monologues": list(getattr(toolbox, "turn_monologues", []) or []),
                    "table_talk": list(getattr(toolbox, "turn_table_talk", []) or []),
                    "decision_response": decision_response,
                    "turn_number": obs.get("turnNumber"),
                    "phase_step": f"{phase}/{step_name}",
                }
                try:
                    player_names_by_id = {p["id"]: p["name"] for p in obs.get("players", [])}
                    advance = argentum.submit_decision(
                        env_id,
                        decision_response,
                        auto_resolve_decisions=False,
                    )
                    obs = advance["observation"]
                    engine_events = advance.get("events") or []
                    action_record["player_names_by_id"] = player_names_by_id
                    if engine_events:
                        action_record["engine_events"] = engine_events
                        event_log.append(
                            ENGINE_EVENT,
                            {
                                "action_id": None,
                                "player": acting_name,
                                "events": engine_events,
                            },
                        )
                        _capture_react_triggers(
                            engine_events,
                            player_names_by_id,
                            all_player_names,
                            pending_draw_react,
                            pending_combat_react_for,
                            combat_react_events,
                        )
                    turn_actions.append(action_record)
                    public_action_history.append(action_record)
                    public_action_history = public_action_history[-48:]
                except Exception as e:
                    stop_reason = f"argentum_error: {e}"
                    break

                event_log.append(OBSERVATION, {"obs": obs})
                step_count += 1
                continue

            if not legal_actions:
                # True dead end or unsupported decision shape. Stop cleanly so
                # the reflector still runs for whatever we did get done.
                pd = obs.get("pendingDecision") or {}
                stop_reason = (
                    f"structured_decision: {pd.get('kind', '?')} ({pd.get('sourceName', '?')})"
                    if pd.get("requiresStructuredResponse")
                    else "no_legal_actions"
                )
                break

            # Skip the LLM call when there's no meaningful choice.
            no_progress_guard = no_progress_fallback_action_id(
                obs, acting_name, no_progress_positions
            )
            autopass = no_progress_guard or autopass_action_id(obs)
            if autopass is not None:
                action_id, reason = autopass
                event_log.append(
                    AUTOPASS,
                    {"player": acting_name, "action_id": action_id, "reason": reason},
                )
                chosen = next((a for a in legal_actions if a["actionId"] == action_id), None)
                action_record = {
                    "player": acting_name,
                    "action_id": action_id,
                    "description": chosen.get("description") if chosen else None,
                    "reasoning": f"[autopass] {reason}",
                    "turn_number": obs.get("turnNumber"),
                    "phase_step": f"{phase}/{step_name}",
                }
                # Still log a thin ACTION event so the timeline reads cleanly.
                event_log.append(ACTION, action_record)
                if verbose:
                    print(f"  [{acting_name}] {phase}/{step_name} — autopass ({reason})")
            else:
                if verbose:
                    print(f"\n  [{acting_name}] {phase}/{step_name} — {len(legal_actions)} actions")
                if isinstance(agent, PlayerAgent):
                    action_id = agent.choose_action(
                        obs,
                        verbose=verbose,
                        recent_public_actions=public_action_history,
                    )
                else:
                    action_id = agent.choose_action(obs, verbose=verbose)
                chosen = next((a for a in legal_actions if a["actionId"] == action_id), None)
                toolbox = getattr(agent, "toolbox", None)
                action_record = {
                    "player": acting_name,
                    "action_id": action_id,
                    "description": chosen.get("description") if chosen else str(action_id),
                    "reasoning": getattr(agent, "last_reasoning", ""),
                    "monologues": list(getattr(toolbox, "turn_monologues", []) or []),
                    "table_talk": list(getattr(toolbox, "turn_table_talk", []) or []),
                    "turn_number": obs.get("turnNumber"),
                    "phase_step": f"{phase}/{step_name}",
                }
                if verbose:
                    desc = chosen["description"] if chosen else str(action_id)
                    print(f"  [{acting_name}] -> {desc}")

            try:
                previous_digest = obs.get("stateDigest")
                player_names_by_id = {p["id"]: p["name"] for p in obs.get("players", [])}
                x_value, damage_distribution = (
                    argentum.cast_overrides_for(chosen) if chosen else (None, None)
                )
                advance = argentum.advance(
                    env_id,
                    action_id,
                    auto_resolve_decisions=False,
                    x_value=x_value,
                    damage_distribution=damage_distribution,
                )
                obs = advance["observation"]
                engine_events = advance.get("events") or []
                action_record["player_names_by_id"] = player_names_by_id
                if engine_events:
                    action_record["engine_events"] = engine_events
                    event_log.append(
                        ENGINE_EVENT,
                        {"action_id": action_id, "player": acting_name, "events": engine_events},
                    )
                    _capture_react_triggers(
                        engine_events,
                        player_names_by_id,
                        all_player_names,
                        pending_draw_react,
                        pending_combat_react_for,
                        combat_react_events,
                    )
                turn_actions.append(action_record)
                public_action_history.append(action_record)
                public_action_history = public_action_history[-48:]
                if (
                    previous_digest
                    and obs.get("stateDigest") == previous_digest
                    and not obs.get("terminated")
                    and should_track_no_progress(obs)
                ):
                    no_progress_positions.add((str(previous_digest), acting_name))
            except Exception as e:
                stop_reason = f"argentum_error: {e}"
                break

            event_log.append(OBSERVATION, {"obs": obs})
            step_count += 1

    except KeyboardInterrupt:
        interrupted = True
        stop_reason = "interrupted"

    finally:
        if commentator is not None and not interrupted:
            commentator.comment_on_turn(
                obs, current_turn, recent_actions=turn_actions, verbose=verbose
            )

        print(f"\n{'=' * 60}")
        if obs.get("terminated"):
            winner_id = obs.get("winnerId")
            winner_name = next((p["name"] for p in obs["players"] if p["id"] == winner_id), None)
            print(f"GAME OVER — Winner: {winner_name or 'draw'}")
            event_log.append(GAME_OVER, {"winner": winner_name, "reason": "normal"})
            if commentator is not None:
                commentator.comment_on_game_over(
                    obs,
                    winner_name=winner_name,
                    turn=current_turn,
                    recent_actions=turn_actions,
                    verbose=verbose,
                )
        else:
            print(f"Game stopped after {step_count} steps ({stop_reason})")
            event_log.append(GAME_OVER, {"winner": None, "reason": stop_reason})

        print(f"Steps taken: {step_count}")
        print(f"Replay log: {log_path}")

        # Post-game reflection: only run on a normal termination, so the
        # reflector doesn't write a confused memory entry about a half-game.
        # Skipped for random-agent runs (no persona memory to update).
        if stop_reason == "normal" and not random_agents:
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
                    final_obs=obs,
                    recent_public_actions=public_action_history,
                    client=client,
                    event_log=event_log,
                    model=model,
                    verbose=verbose,
                )

        for sdk_agent in sdk_agents:
            try:
                sdk_agent.close()
            except Exception as e:
                print(f"  [warn] SDK agent close failed: {e}")

        event_log.close()
        argentum.dispose(env_id)


def _capture_decision_fixture(
    game_dir: Path,
    obs: dict,
    perspective_name: str,
    deck_name: str,
) -> None:
    """Dump a fixture-shaped JSON of a surfaced structured decision.

    Writes to ``<game_dir>/decisions/<turn>-<kind>-<source>.json`` with the
    same ``_meta`` + obs envelope the hand-crafted fixtures under
    ``tests/fixtures/*/*.json`` use. Two purposes:

    1. Keep the fixture format honest — captured-from-real-engine obs is the
       ground truth; if the hand-crafted ones drift from this shape they
       should be regenerated from captures.
    2. Make it cheap to promote a real game-state to a regression test:
       cp the captured file under tests/fixtures/<kind>/.

    Dev side-file per the dev-vs-spectator rule (not in game.jsonl / viewer).
    """
    import json as _json

    pd = obs.get("pendingDecision") or {}
    if not pd.get("requiresStructuredResponse"):
        return
    decisions_dir = game_dir / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    turn = obs.get("turnNumber", "?")
    kind = (pd.get("kind") or "DECISION").lower()
    source = (pd.get("sourceName") or "unknown").replace(" ", "_").replace("'", "")
    seq_suffix = len(list(decisions_dir.glob(f"t{turn}-{kind}-{source}*.json")))
    fname = f"t{turn}-{kind}-{source}"
    if seq_suffix:
        fname += f"-{seq_suffix}"
    fname += ".json"
    fixture = {
        "_meta": {
            "fixture": fname.removesuffix(".json"),
            "kind": pd.get("kind"),
            "scenario": (
                f"Auto-captured from live game: {perspective_name} faces "
                f"{pd.get('kind')} from {pd.get('sourceName', '?')} on turn {turn}."
            ),
            "perspective_player_name": perspective_name,
            "deck": deck_name,
            "captured_at": "live",
        },
        **obs,
    }
    with (decisions_dir / fname).open("w", encoding="utf-8") as f:
        _json.dump(fixture, f, indent=2)


def _capture_react_triggers(
    engine_events: list[dict],
    player_names_by_id: dict[str, str],
    all_player_names: list[str],
    pending_draw_react: dict[str, dict],
    pending_combat_react_for: set[str],
    combat_react_events: list[dict],
) -> None:
    """Scan engine_events for react-hook triggers; mutate pending state.

    - CardsDrawn → register pending draw react for the drawing player.
    - DamageDealt / LifeChanged / CreatureDestroyed / PlayerLost → register
      pending combat react for BOTH players (each fires when their priority
      window arrives after COMBAT_DAMAGE resolves).

    Caller owns the mutable containers; this function appends/mutates in
    place to keep the call sites at run_game.py's advance() hooks terse.
    """
    # Combat-damage events (DamageDealt / LifeChanged / CreatureDestroyed)
    # also fire for non-combat spells (Lava Axe, Vampiric Touch, Pyroclasm).
    # Distinguish by anchoring on the COMBAT_DAMAGE step transition that
    # always precedes real combat resolution in the same engine_events batch.
    combat_step_entered = any(
        ev.get("type") == "StepChanged" and "COMBAT_DAMAGE" in str(ev.get("text", ""))
        for ev in engine_events
    )
    for ev in engine_events:
        et = ev.get("type")
        if et == "CardsDrawn":
            for pid in ev.get("playerIds") or []:
                pname = player_names_by_id.get(pid)
                if pname:
                    pending_draw_react[pname] = ev
        elif combat_step_entered and et in {
            "DamageDealt",
            "LifeChanged",
            "CreatureDestroyed",
            "PlayerLost",
        }:
            combat_react_events.append(ev)
    if combat_step_entered:
        for name in all_player_names:
            pending_combat_react_for.add(name)


def no_progress_fallback_action_id(
    obs: dict,
    acting_name: str,
    no_progress_positions: set[tuple[str, str]],
) -> tuple[int, str] | None:
    """Pick the safest declaration/pass action after a prior no-op at this state."""
    digest = obs.get("stateDigest")
    if not digest or (str(digest), acting_name) not in no_progress_positions:
        return None

    legal_actions = obs.get("legalActions", [])
    preferred = (
        "No blocks",
        "Skip blocks",
        "Skip combat",
        "Pass priority",
    )
    for label in preferred:
        for action in legal_actions:
            desc = action.get("description", "")
            if desc == label or desc.startswith(f"{label} "):
                return action["actionId"], f"no progress after prior action; choosing {desc}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an LLM MTG game via Argentum Engine")
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--persona-a", default="mira", help="persona slug (dir in personas/)")
    parser.add_argument("--persona-b", default="noct", help="persona slug (dir in personas/)")
    parser.add_argument(
        "--deck-a",
        default=None,
        choices=DECK_NAMES,
        help="override deck for persona-a (defaults to the persona's archetype deck)",
    )
    parser.add_argument(
        "--deck-b",
        default=None,
        choices=DECK_NAMES,
        help="override deck for persona-b (defaults to the persona's archetype deck)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="default LLM model id")
    parser.add_argument(
        "--persona-a-backend",
        choices=("ollama", "anthropic_sdk"),
        default="ollama",
        help="LLM backend for persona-a (default: ollama).",
    )
    parser.add_argument(
        "--persona-b-backend",
        choices=("ollama", "anthropic_sdk"),
        default="ollama",
        help="LLM backend for persona-b (default: ollama).",
    )
    parser.add_argument(
        "--persona-a-model",
        default=None,
        help="override --model for persona-a (e.g. 'haiku' / 'sonnet' / 'opus' for anthropic_sdk).",
    )
    parser.add_argument(
        "--persona-b-model",
        default=None,
        help="override --model for persona-b.",
    )
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--quiet", action="store_false", dest="verbose")
    parser.add_argument(
        "--random",
        action="store_true",
        help="use deterministic RandomAgent instead of LLM (fast harness smoke test)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "pin the per-game library-shuffle PRNG (any int). Default is "
            "non-deterministic. Use to replay an exact opening hand / draw "
            "order across runs — e.g. for regression tests or seeding a "
            "specific card into early turns."
        ),
    )
    args = parser.parse_args()

    game_id = args.game_id or str(uuid.uuid4())
    deck_a_name = args.deck_a or default_deck_for(args.persona_a) or "white_aegis"
    deck_b_name = args.deck_b or default_deck_for(args.persona_b) or "black_attrition"
    run_game(
        persona_a_name=args.persona_a,
        persona_b_name=args.persona_b,
        deck_a_name=deck_a_name,
        deck_b_name=deck_b_name,
        model=args.model,
        max_steps=args.max_steps,
        game_id=game_id,
        verbose=args.verbose,
        random_agents=args.random,
        persona_a_backend=args.persona_a_backend,
        persona_b_backend=args.persona_b_backend,
        persona_a_model=args.persona_a_model,
        persona_b_model=args.persona_b_model,
        library_seed=args.seed,
    )


if __name__ == "__main__":
    main()
