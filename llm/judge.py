"""Judge LLM agent — invoked on appeal, issues rulings."""

from __future__ import annotations

from openai import OpenAI

from game.events import JUDGE_APPEAL, JUDGE_RULING, EventLog
from game.state import GameState
from llm.client import DEFAULT_MODEL
from llm.prompts import build_judge_system_prompt


class JudgeAgent:
    def __init__(self, client: OpenAI, event_log: EventLog, model: str = DEFAULT_MODEL) -> None:
        self.client = client
        self.event_log = event_log
        self.model = model
        self._system_prompt = build_judge_system_prompt()

    def rule(self, situation: str, state: GameState) -> str:
        """Issue a ruling on the situation. Returns ruling text."""
        self.event_log.append(JUDGE_APPEAL, {"situation": situation})

        public = state.to_public_dict()
        import json

        context = f"APPEAL: {situation}\n\nCurrent game state:\n{json.dumps(public, indent=2)}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": context},
                ],
                temperature=0.3,
            )
            ruling = response.choices[0].message.content or "No ruling issued."
        except Exception as e:
            ruling = f"[Judge unavailable: {e}]"

        self.event_log.append(JUDGE_RULING, {"ruling": ruling, "situation": situation})
        return ruling
