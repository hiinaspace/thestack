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
    chosen_decision_response: dict[str, Any] | None = None
    chosen_reasoning: str = ""
    # Per-turn voice channels. Drained into the action_record when submit_action
    # lands so the bounded public_action_history can quote them on the
    # opponent's next decision.
    turn_monologues: list[str] = field(default_factory=list)
    turn_table_talk: list[str] = field(default_factory=list)
    # Provided fresh each turn by PlayerAgent.choose_action
    _valid_action_ids: set[int] = field(default_factory=set)
    _valid_decision_id: str | None = None

    # ------------------------------------------------------------------ tools

    def take_note(self, note: str) -> str:
        self.scratchpad.append(note.strip())
        return f"Noted ({len(self.scratchpad)} notes total)."

    def recall_strategy(self) -> str:
        if not self.scratchpad:
            return "No notes yet."
        numbered = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(self.scratchpad))
        return f"Your strategy notes so far:\n{numbered}"

    def monologue(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "ERROR: monologue text was empty."
        self.turn_monologues.append(stripped)
        return "Monologue logged (audience hears it; your opponent does not)."

    def table_talk(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "ERROR: table_talk text was empty."
        self.turn_table_talk.append(stripped)
        return "Table talk logged; your opponent will read this at the start of their next turn."

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

    def submit_decision(self, response: dict[str, Any], reasoning: str) -> str:
        if not isinstance(response, dict):
            return "ERROR: response must be a JSON object."
        if "type" not in response:
            return "ERROR: response.type is required, e.g. TargetsResponse."
        if "decisionId" not in response:
            return "ERROR: response.decisionId is required."
        if self._valid_decision_id and response.get("decisionId") != self._valid_decision_id:
            return (
                "ERROR: decisionId mismatch; expected "
                f"{self._valid_decision_id}, got {response.get('decisionId')}."
            )
        self.chosen_decision_response = response
        self.chosen_reasoning = reasoning.strip()
        return "Decision response recorded."

    # ----------------------------------------------------------------- runtime

    def reset_turn(
        self,
        valid_action_ids: set[int],
        valid_decision_id: str | None = None,
    ) -> None:
        self.chosen_action_id = None
        self.chosen_decision_response = None
        self.chosen_reasoning = ""
        self.turn_monologues = []
        self.turn_table_talk = []
        self._valid_action_ids = valid_action_ids
        self._valid_decision_id = valid_decision_id

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
    "monologue": lambda tb, text: tb.monologue(text),
    "table_talk": lambda tb, text: tb.table_talk(text),
    "submit_action": lambda tb, action_id, reasoning: tb.submit_action(action_id, reasoning),
    "submit_decision": lambda tb, response, reasoning: tb.submit_decision(response, reasoning),
}

# Tools whose call is itself a voice event; the Agent loop emits MONOLOGUE /
# TABLE_TALK in place of a generic TOOL_CALL so the viewer can render them
# as dialogue rather than mechanical book-keeping.
VOICE_TOOLS: dict[str, str] = {
    "monologue": "monologue",
    "table_talk": "table_talk",
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
            "name": "monologue",
            "description": (
                "Speak an in-character internal-monologue line. The spectator "
                "transcript shows it; your opponent does NOT read it. Use to "
                "set up bluffs, react in voice to your opponent's table talk, "
                "or build tension before a big play. May be called multiple "
                "times before submit_action. Keep each line short — one or "
                "two sentences."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The monologue line, in your persona's voice.",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "table_talk",
            "description": (
                "Speak an in-character line AT your opponent. Your opponent's "
                "agent will read these lines at the start of their next "
                "decision, so they are part of the conversation. Use sparingly; "
                "one pointed line beats a paragraph. May be called multiple "
                "times before submit_action."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What you say to your opponent, in your persona's voice.",
                    }
                },
                "required": ["text"],
            },
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
    {
        "type": "function",
        "function": {
            "name": "submit_decision",
            "description": (
                "Commit a structured Argentum DecisionResponse for a pending "
                "decision that is not represented by a numbered action. MUST "
                "be called exactly once when the prompt asks for a structured "
                "decision response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "object",
                        "description": (
                            "The DecisionResponse JSON object. Include type, "
                            "decisionId, and the fields required for that "
                            "response type."
                        ),
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "One or two sentences of natural-language reasoning, "
                            "spoken in-character for the spectator transcript."
                        ),
                    },
                },
                "required": ["response", "reasoning"],
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
