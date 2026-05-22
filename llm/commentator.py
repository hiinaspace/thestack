"""Commentator LLM — receives public state, produces turn-by-turn commentary."""

from __future__ import annotations

import json

import ollama

from game.events import COMMENTARY, EventLog
from game.state import GameState
from llm.client import DEFAULT_MODEL
from llm.prompts import build_commentator_system_prompt


class CommentatorAgent:
    def __init__(
        self, client: ollama.Client, event_log: EventLog, model: str = DEFAULT_MODEL
    ) -> None:
        self.client = client
        self.event_log = event_log
        self.model = model
        self._system_prompt = build_commentator_system_prompt()

    def comment_on_turn(self, state: GameState, verbose: bool = False) -> str:
        """Generate commentary for the just-completed turn. Appends to event log."""
        public_events = self.event_log.get_public_transcript(n_turns=2)
        turn_events = [e for e in public_events if e.get("turn") == state.turn]

        if not turn_events:
            return ""

        event_summary = self.event_log.format_for_display(turn_events)
        board_summary = json.dumps(state.to_public_dict(), indent=2)

        prompt = (
            f"Turn {state.turn} just completed. Here's what happened:\n\n"
            f"{event_summary}\n\n"
            f"Current board state:\n{board_summary}\n\n"
            f"Provide 2-4 sentences of commentary."
        )

        try:
            # Commentator doesn't need thinking — its output IS the content
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
            self.event_log.append(COMMENTARY, {"text": text, "turn": state.turn})

        return text
