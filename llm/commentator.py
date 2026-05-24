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

    def comment_on_turn(
        self,
        obs: dict,
        turn: int,
        recent_actions: list[dict] | None = None,
        verbose: bool = False,
    ) -> str:
        action_section = _format_recent_actions(recent_actions or [])
        prompt = (
            f"Turn {turn} just completed.\n\n"
            f"{action_section}\n\n"
            f"Resulting public state:\n{format_public_state_for_commentator(obs)}\n\n"
            "Give 2-4 sentences of commentary on what just happened this turn. "
            "Narrate the actions players took — what they cast, who attacked "
            "whom, what traded. Refer back to earlier turns when relevant."
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


def _format_recent_actions(actions: list[dict]) -> str:
    """Compact human-readable list of ACTION events from the past turn.

    Autopass / Pass-priority entries are dropped — they're noise; the
    interesting beats are casts, plays, attacks, and blocks.
    """
    interesting = [
        a
        for a in actions
        if not (a.get("reasoning") or "").startswith("[autopass]")
        and a.get("description") != "Pass priority"
    ]
    if not interesting:
        return "No notable actions this turn (both players mostly passed)."
    lines = ["Actions taken this turn:"]
    for a in interesting:
        who = a.get("player", "?")
        desc = a.get("description", "?")
        lines.append(f"  - {who}: {desc}")
    return "\n".join(lines)
