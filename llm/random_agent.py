"""No-LLM agent that picks a valid action with light heuristics.

Useful for exercising the harness end-to-end in seconds without paying for
Ollama calls. Same interface as PlayerAgent so run_game can swap them in.

Heuristics (ordered): prefer playing a land if one is available, then
prefer casting any spell, then a mana ability, then a non-pass action,
then pass. Within each bucket, picks uniformly at random.
"""

from __future__ import annotations

import random

from game.events import ACTION, EventLog


class RandomAgent:
    def __init__(self, name: str, event_log: EventLog, seed: int | None = None) -> None:
        self.name = name
        self.event_log = event_log
        self._rng = random.Random(seed)
        self.toolbox = _StubToolbox()
        self.last_reasoning = "[random]"

    def choose_action(self, obs: dict, verbose: bool = False) -> int:
        legal = [a for a in obs.get("legalActions", []) if a.get("affordable", True)]
        if not legal:
            return 0

        plays = [a for a in legal if a.get("description", "").startswith("Play ")]
        casts = [a for a in legal if a.get("kind") == "CastSpell"]
        manas = [a for a in legal if a.get("isManaAbility")]
        passes = [a for a in legal if "pass" in a.get("description", "").lower()]
        other = [a for a in legal if a not in plays + casts + manas + passes]

        for bucket in (plays, casts, other, manas, passes):
            if bucket:
                chosen = self._rng.choice(bucket)
                break
        else:
            chosen = legal[0]

        self.event_log.append(
            ACTION,
            {
                "player": self.name,
                "action_id": chosen["actionId"],
                "description": chosen.get("description"),
                "reasoning": "[random]",
            },
        )
        return chosen["actionId"]

    def choose_decision(self, obs: dict, verbose: bool = False) -> dict:
        from llm.player import _default_decision_response

        response = _default_decision_response(obs)
        self.event_log.append(
            ACTION,
            {
                "player": self.name,
                "action_id": None,
                "description": "structured decision",
                "reasoning": "[random/default]",
                "decision_response": response,
            },
        )
        return response


class _StubToolbox:
    """Empty stand-in so post-game reflection can read .scratchpad."""

    scratchpad: list = []
