"""Judge LLM agent — invoked on appeal, issues rulings."""

from __future__ import annotations

import json

import ollama

from game.events import JUDGE_APPEAL, JUDGE_RULING, EventLog
from game.state import GameState
from llm.client import DEFAULT_MODEL
from llm.prompts import build_judge_system_prompt


class JudgeAgent:
    def __init__(
        self, client: ollama.Client, event_log: EventLog, model: str = DEFAULT_MODEL
    ) -> None:
        self.client = client
        self.event_log = event_log
        self.model = model
        self._system_prompt = build_judge_system_prompt()

    def rule(self, situation: str, state: GameState) -> str:
        """Issue a ruling on the situation. Returns ruling text."""
        self.event_log.append(JUDGE_APPEAL, {"situation": situation})

        public = state.to_public_dict()
        context = f"APPEAL: {situation}\n\nCurrent game state:\n{json.dumps(public, indent=2)}"

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": context},
                ],
                think=True,
                options={"temperature": 0.3},
            )
            ruling = response.message.content or "No ruling issued."
        except Exception as e:
            ruling = f"[Judge unavailable: {e}]"

        self.event_log.append(JUDGE_RULING, {"ruling": ruling, "situation": situation})
        return ruling
