"""Player LLM agent — picks from Argentum's legal-action list."""

from __future__ import annotations

import re

import ollama

from game.events import REASONING, THINKING, EventLog
from llm.prompts import (
    build_player_system_prompt,
    format_legal_actions,
    format_observation,
)

MAX_RETRIES = 3


class PlayerAgent:
    def __init__(self, name: str, model: str, client: ollama.Client, event_log: EventLog) -> None:
        self.name = name
        self.model = model
        self.client = client
        self.event_log = event_log
        self._system_prompt: str | None = None

    def _get_system_prompt(self, opponent_name: str) -> str:
        if self._system_prompt is None:
            self._system_prompt = build_player_system_prompt(self.name, opponent_name)
        return self._system_prompt

    def choose_action(self, obs: dict, verbose: bool = False) -> int:
        """
        Ask the LLM to pick a legal action from obs["legalActions"].
        Returns the chosen actionId.
        Falls back to the first action (usually pass priority) if parsing fails.
        """
        legal_actions = obs.get("legalActions", [])
        if not legal_actions:
            return 0

        # Find opponent name from observation
        opponent_name = next(
            (p["name"] for p in obs.get("players", []) if p["name"] != self.name),
            "Opponent",
        )
        system_prompt = self._get_system_prompt(opponent_name)

        game_state_str = format_observation(obs, self.name)
        actions_str = format_legal_actions(legal_actions)

        # Build the valid action IDs set for validation
        valid_ids = {a["actionId"] for a in legal_actions}
        pass_id = next(
            (a["actionId"] for a in legal_actions if "pass" in a.get("description", "").lower()),
            legal_actions[0]["actionId"],
        )

        user_msg = f"{game_state_str}\n\n{actions_str}\n\nChoose your action:"

        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.chat(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    think=True,
                    options={"temperature": 0.6},
                )
            except Exception as e:
                self.event_log.append(REASONING, {"player": self.name, "text": f"[LLM error: {e}]"})
                return pass_id

            msg = response.message

            if msg.thinking:
                self.event_log.append(THINKING, {"player": self.name, "text": msg.thinking})
                if verbose:
                    excerpt = msg.thinking[:200].replace("\n", " ")
                    print(
                        f"\n  [{self.name} thinking] {excerpt}{'...' if len(msg.thinking) > 200 else ''}"
                    )

            content = msg.content or ""
            if verbose:
                print(f"\n[{self.name}] {content}")
            if content:
                self.event_log.append(REASONING, {"player": self.name, "text": content})

            action_id = _parse_action_id(content, valid_ids)
            if action_id is not None:
                return action_id

            if verbose:
                print(f"  [{self.name}] couldn't parse action (attempt {attempt + 1}), retrying")

        if verbose:
            print(f"  [{self.name}] parse failed {MAX_RETRIES}x, defaulting to pass")
        return pass_id


def _parse_action_id(text: str, valid_ids: set[int]) -> int | None:
    """Extract the first integer from the response that is a valid action ID."""
    for m in re.finditer(r"\b(\d+)\b", text):
        candidate = int(m.group(1))
        if candidate in valid_ids:
            return candidate
    return None
