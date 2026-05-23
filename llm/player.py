"""Player agent — picks from Argentum's legal-action list via tool calls.

Holds one persistent Ollama conversation across every decision in a game.
The model's scratchpad (take_note) is preserved between decisions; reasoning
is captured both as a free-text REASONING event (final assistant message) and
as the structured `reasoning` argument the model passes to submit_action.
"""

from __future__ import annotations

import re

import ollama

from game.events import ACTION, EventLog
from llm.agent import Agent
from llm.persona import Persona
from llm.prompts import (
    build_player_system_prompt,
    format_legal_actions,
    format_observation,
)
from llm.tools import Toolbox


class PlayerAgent:
    def __init__(
        self,
        persona: Persona,
        opponent_name: str,
        model: str,
        client: ollama.Client,
        event_log: EventLog,
    ) -> None:
        self.persona = persona
        self.name = persona.name
        self.event_log = event_log
        self.toolbox = Toolbox(name=persona.name)
        self.agent = Agent(
            name=persona.name,
            model=model,
            client=client,
            event_log=event_log,
            system_prompt=build_player_system_prompt(
                persona.name,
                opponent_name,
                identity=persona.identity,
                strategy=persona.strategy,
                opponent_notes=persona.opponent_entry(opponent_name),
                recent_memory=persona.recent_memory(),
            ),
            toolbox=self.toolbox,
        )

    def choose_action(self, obs: dict, verbose: bool = False) -> int:
        legal_actions = obs.get("legalActions", [])
        if not legal_actions:
            return 0

        valid_ids = {a["actionId"] for a in legal_actions}
        pass_id = _find_pass_id(legal_actions)
        self.toolbox.reset_turn(valid_ids)

        user_msg = (
            f"{format_observation(obs, self.name)}\n\n"
            f"{format_legal_actions(legal_actions)}\n\n"
            "Choose one of the numbered legal actions and call submit_action."
        )

        response = self.agent.run(user_msg, verbose=verbose)

        action_id = response.action_id
        if action_id is None:
            action_id = _parse_action_id_fallback(response.content, valid_ids)
        if action_id is None or action_id not in valid_ids:
            if verbose:
                print(f"  [{self.name}] no valid action chosen, defaulting to pass")
            action_id = pass_id

        chosen = next((a for a in legal_actions if a["actionId"] == action_id), None)
        self.event_log.append(
            ACTION,
            {
                "player": self.name,
                "action_id": action_id,
                "description": chosen.get("description") if chosen else None,
                "reasoning": response.reasoning,
            },
        )
        return action_id


def _find_pass_id(legal_actions: list[dict]) -> int:
    return next(
        (a["actionId"] for a in legal_actions if "pass" in a.get("description", "").lower()),
        legal_actions[0]["actionId"],
    )


def _parse_action_id_fallback(text: str, valid_ids: set[int]) -> int | None:
    """If the model wrote a number instead of calling submit_action, salvage it."""
    for m in re.finditer(r"\b(\d+)\b", text or ""):
        candidate = int(m.group(1))
        if candidate in valid_ids:
            return candidate
    return None
