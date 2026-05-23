"""Append-only JSONL event log."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# Agent-emitted event types (the only events The Stack writes itself —
# rules-engine events live in Argentum's observations).
REASONING = "reasoning"  # natural language narration spoken by an agent
THINKING = "thinking"  # internal chain-of-thought from Ollama think= block
TOOL_CALL = "tool_call"  # an agent invoked one of its registered tools
COMMENTARY = "commentary"  # commentator output for a turn
ACTION = "action"  # an agent committed to an Argentum legalAction
OBSERVATION = "observation"  # snapshot of Argentum game state (drives the viewer's board)
GAME_OVER = "game_over"
INFO = "info"


class EventLog:
    def __init__(self, game_id: str, path: Path) -> None:
        self.game_id = game_id
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._turn = 0
        self._phase = "setup"
        self._file = self.path.open("a", encoding="utf-8")

    def set_context(self, turn: int, phase: str) -> None:
        self._turn = turn
        self._phase = phase

    def append(self, event_type: str, data: dict[str, Any]) -> None:
        event = {
            "seq": self._seq,
            "game_id": self.game_id,
            "turn": self._turn,
            "phase": self._phase,
            "event": event_type,
            "ts": time.time(),
            **data,
        }
        self._file.write(json.dumps(event) + "\n")
        self._file.flush()
        self._seq += 1

    def close(self) -> None:
        self._file.close()

    def all_events(self) -> list[dict]:
        self._file.flush()
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
