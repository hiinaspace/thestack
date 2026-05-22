"""Commentator LLM — receives public state, produces turn-by-turn commentary."""

from __future__ import annotations

import ollama

from game.events import COMMENTARY, EventLog
from llm.client import DEFAULT_MODEL
from llm.prompts import build_commentator_system_prompt, format_public_state_for_commentator


class CommentatorAgent:
    def __init__(
        self, client: ollama.Client, event_log: EventLog, model: str = DEFAULT_MODEL
    ) -> None:
        self.client = client
        self.event_log = event_log
        self.model = model
        self._system_prompt = build_commentator_system_prompt()

    def comment_on_turn(self, obs: dict, turn: int, verbose: bool = False) -> str:
        """Generate commentary for the just-completed turn."""
        board_summary = format_public_state_for_commentator(obs)

        prompt = (
            f"Turn {turn} just completed. Current board state:\n\n"
            f"{board_summary}\n\n"
            "Provide 2-4 sentences of commentary."
        )

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                think=False,
                options={"temperature": 0.9},
            )
            text = response.message.content or ""
        except Exception as e:
            text = f"[Commentator unavailable: {e}]"

        if text:
            if verbose:
                print(f"\n[COMMENTARY] {text}")
            self.event_log.append(COMMENTARY, {"text": text, "turn": turn})

        return text
