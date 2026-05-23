"""Commentator agent — narrates each turn from public state only.

Like PlayerAgent, the commentator keeps one persistent conversation across the
game so it can build a narrative arc (callbacks, archetype reads, turning-point
recognition) instead of restarting cold each turn.
"""

from __future__ import annotations

import ollama

from game.events import COMMENTARY, EventLog
from llm.agent import Agent
from llm.client import DEFAULT_MODEL
from llm.prompts import build_commentator_system_prompt, format_public_state_for_commentator


class CommentatorAgent:
    def __init__(
        self, client: ollama.Client, event_log: EventLog, model: str = DEFAULT_MODEL
    ) -> None:
        self.event_log = event_log
        self.agent = Agent(
            name="Commentator",
            model=model,
            client=client,
            event_log=event_log,
            system_prompt=build_commentator_system_prompt(),
            toolbox=None,
            temperature=0.9,
            think=False,
            log_content_as=None,  # commentary is logged as COMMENTARY below
        )

    def comment_on_turn(self, obs: dict, turn: int, verbose: bool = False) -> str:
        prompt = (
            f"Turn {turn} just completed. Current public state:\n\n"
            f"{format_public_state_for_commentator(obs)}\n\n"
            "Give 2-4 sentences of commentary. Refer back to earlier turns when relevant."
        )
        response = self.agent.run(prompt, verbose=False)
        # The Agent already logged a REASONING event for the response content;
        # additionally publish it as a COMMENTARY event for the viewer.
        text = response.content
        if text:
            self.event_log.append(COMMENTARY, {"text": text, "turn": turn})
            if verbose:
                print(f"\n[COMMENTARY] {text}")
        return text
