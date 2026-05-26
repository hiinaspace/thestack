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
    # Per-decision: set when the model calls submit_action
    chosen_action_id: int | None = None
    chosen_decision_response: dict[str, Any] | None = None
    chosen_reasoning: str = ""
    # Per-decision voice channels. Drained into the action_record when
    # submit_action lands so the bounded public_action_history can quote
    # them on the opponent's next decision.
    turn_monologues: list[str] = field(default_factory=list)
    turn_table_talk: list[str] = field(default_factory=list)
    # Per-TURN plan (lives across all decisions of the active player's turn).
    # Cleared only by reset_for_new_turn(), NOT by reset_turn() (which is a
    # misnomer — that one resets per-decision state). Surfaced at the top of
    # every choose_action prompt after it's been set so subsequent decisions
    # execute against the plan instead of re-deriving it.
    turn_plan: dict[str, Any] | None = None
    # Provided fresh each decision by PlayerAgent.choose_action
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

    def set_turn_plan(
        self,
        intent: str,
        action_sequence: list[str] | None = None,
        notes: str = "",
    ) -> str:
        intent = (intent or "").strip()
        if not intent:
            return "ERROR: set_turn_plan requires a non-empty intent."
        self.turn_plan = {
            "intent": intent,
            "action_sequence": [s.strip() for s in (action_sequence or []) if s and s.strip()],
            "notes": (notes or "").strip(),
            "revisions": [],
        }
        return (
            "Turn plan committed. It will appear at the top of every decision "
            "for the rest of this turn — execute against it. Call "
            "update_turn_plan if something material changes."
        )

    def update_turn_plan(self, revised_intent: str, reason: str) -> str:
        revised_intent = (revised_intent or "").strip()
        reason = (reason or "").strip()
        if not revised_intent:
            return "ERROR: update_turn_plan requires a non-empty revised_intent."
        if not reason:
            return "ERROR: update_turn_plan requires a non-empty reason."
        if self.turn_plan is None:
            self.turn_plan = {
                "intent": revised_intent,
                "action_sequence": [],
                "notes": "",
                "revisions": [],
            }
        else:
            self.turn_plan["revisions"].append(
                {"reason": reason, "from": self.turn_plan["intent"], "to": revised_intent}
            )
            self.turn_plan["intent"] = revised_intent
        return "Turn plan updated."

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
        """Reset PER-DECISION state. Misnamed historically — does NOT clear
        the turn plan or scratchpad. Use reset_for_new_turn() at turn
        boundaries instead."""
        self.chosen_action_id = None
        self.chosen_decision_response = None
        self.chosen_reasoning = ""
        self.turn_monologues = []
        self.turn_table_talk = []
        self._valid_action_ids = valid_action_ids
        self._valid_decision_id = valid_decision_id

    def reset_for_new_turn(self) -> None:
        """Clear state that's scoped to a single MTG turn (just turn_plan
        today). Call from run_game.py when the active player's turn changes."""
        self.turn_plan = None

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
    "set_turn_plan": lambda tb, intent, action_sequence=None, notes="": tb.set_turn_plan(
        intent, action_sequence, notes
    ),
    "update_turn_plan": lambda tb, revised_intent, reason: tb.update_turn_plan(
        revised_intent, reason
    ),
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
            "name": "set_turn_plan",
            "description": (
                "Commit a plan for THIS MTG turn — your intent in one "
                "sentence, the rough sequence of actions you intend to take, "
                "and any tactical notes. Call ONCE on your first decision of "
                "the turn (or in your draw reaction). Subsequent decisions "
                "this turn will see the plan at the top of the prompt so you "
                "execute against it instead of re-deriving — keep voice "
                "terse on routine plays once the plan is set. Call "
                "update_turn_plan if something material changes mid-turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": (
                            "One-sentence intent for the turn (e.g. 'play "
                            "Mountain, cast Raging Cougar, attack with "
                            "everything')."
                        ),
                    },
                    "action_sequence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Ordered short labels for the actions you "
                            "intend (e.g. ['play Mountain', 'cast Raging "
                            "Cougar', 'attack all']). Empty list is fine "
                            "when intent alone is enough."
                        ),
                    },
                    "notes": {
                        "type": "string",
                        "description": (
                            "Freeform tactical context — what you're "
                            "watching for, contingencies, opponent reads. "
                            "Optional."
                        ),
                    },
                },
                "required": ["intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_turn_plan",
            "description": (
                "Revise your turn plan when something material happened "
                "mid-turn (opponent surprise, you drew a key card off a "
                "tutor, board shift after combat). Records the revision "
                "history so the spectator transcript shows the adjustment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "revised_intent": {
                        "type": "string",
                        "description": "The new one-sentence intent.",
                    },
                    "reason": {
                        "type": "string",
                        "description": ("Why the plan is changing (one short clause)."),
                    },
                },
                "required": ["revised_intent", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_action",
            "description": (
                "REQUIRED. Commit to one of the numbered legal actions for this "
                "decision. You MUST call this exactly once per decision; the "
                "harness will not advance until you do. Call it in the SAME "
                "response as your monologue/table_talk tool calls — do not "
                "split a single decision across multiple turns. `action_id` is "
                "an integer drawn verbatim from the 'Legal actions' list. The "
                "only valid tools at any decision are exactly: take_note, "
                "recall_strategy, monologue, table_talk, set_turn_plan, "
                "update_turn_plan, submit_action, submit_decision. Do not "
                "invent any other tool names (e.g. 'play_card', 'attack', "
                "'choose_action') — they will fail."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action_id": {
                        "type": "integer",
                        "description": (
                            "The numeric id of the chosen legal action, copied "
                            "verbatim from the 'Legal actions' list."
                        ),
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "One or two sentences of natural-language reasoning, "
                            "spoken in-character for the spectator transcript. "
                            "This is the public action declaration; keep it short."
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
