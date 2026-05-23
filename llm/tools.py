"""Tools registered with Ollama for player agents to invoke during their turn.

Each tool is a small Python function plus an Ollama tool-schema entry.
The Toolbox owns per-turn mutable state (scratchpad, pending action choice)
and dispatches tool calls coming back from the model.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Toolbox:
    """Holds per-game scratchpad and per-turn action choice for one player agent."""

    name: str
    scratchpad: list[str] = field(default_factory=list)
    # Per-turn: set when the model calls submit_action
    chosen_action_id: int | None = None
    chosen_reasoning: str = ""
    # Provided fresh each turn by PlayerAgent.choose_action
    _valid_action_ids: set[int] = field(default_factory=set)

    # ------------------------------------------------------------------ tools

    def take_note(self, note: str) -> str:
        self.scratchpad.append(note.strip())
        return f"Noted ({len(self.scratchpad)} notes total)."

    def recall_strategy(self) -> str:
        if not self.scratchpad:
            return "No notes yet."
        numbered = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(self.scratchpad))
        return f"Your strategy notes so far:\n{numbered}"

    def submit_action(self, action_id: int, reasoning: str) -> str:
        if action_id not in self._valid_action_ids:
            valid = sorted(self._valid_action_ids)
            return (
                f"ERROR: action_id {action_id} is not in the legal action list "
                f"(valid: {valid}). Pick a different one."
            )
        self.chosen_action_id = int(action_id)
        self.chosen_reasoning = reasoning.strip()
        return "Action recorded."

    # ----------------------------------------------------------------- runtime

    def reset_turn(self, valid_action_ids: set[int]) -> None:
        self.chosen_action_id = None
        self.chosen_reasoning = ""
        self._valid_action_ids = valid_action_ids

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        fn = _DISPATCH.get(name)
        if fn is None:
            return f"ERROR: unknown tool {name!r}"
        try:
            return fn(self, **args)
        except TypeError as e:
            return f"ERROR: bad arguments to {name}: {e}"


# Map tool-name -> bound dispatcher. Kept at module scope so the schema and
# the runtime dispatch stay in lockstep.
_DISPATCH: dict[str, Callable[..., str]] = {
    "take_note": lambda tb, note: tb.take_note(note),
    "recall_strategy": lambda tb: tb.recall_strategy(),
    "submit_action": lambda tb, action_id, reasoning: tb.submit_action(action_id, reasoning),
}


# Ollama / OpenAI-schema tool definitions. Single source of truth — keep in
# sync with the _DISPATCH map above.
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "take_note",
            "description": (
                "Save a short strategic note to your scratchpad. Use this to record "
                "your plan, reads on the opponent, or reminders you'll want on later "
                "turns. Notes persist for the rest of the game."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "The note to save (one or two sentences).",
                    }
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_strategy",
            "description": "Return all strategy notes you've saved so far this game.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_action",
            "description": (
                "Commit to one of the numbered legal actions for this decision point. "
                "MUST be called exactly once per decision; ends your turn-thinking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action_id": {
                        "type": "integer",
                        "description": "The numeric id of the chosen legal action.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "One or two sentences of natural-language reasoning, "
                            "spoken in-character for the spectator transcript."
                        ),
                    },
                },
                "required": ["action_id", "reasoning"],
            },
        },
    },
]


def serialize_tool_args(args: Any) -> dict:
    """Normalize tool-call arguments to a plain dict.

    Ollama returns either a dict or (occasionally) a JSON string. Be liberal.
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return {}
