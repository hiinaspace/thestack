"""Append-only JSONL event log."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# Event type constants
TURN_START = "turn_start"
PHASE_CHANGE = "phase_change"
DRAW = "draw"
CARD_PLAYED = "card_played"
ABILITY = "ability"
ATTACK = "attack"
BLOCK = "block"
DAMAGE = "damage"
LIFE_CHANGE = "life_change"
CARD_DIES = "card_dies"
STACK_RESOLVE = "stack_resolve"
PRIORITY_PASS = "priority_pass"
INSTANT_WINDOW = "instant_window"
JUDGE_APPEAL = "judge_appeal"
JUDGE_RULING = "judge_ruling"
COMMENTARY = "commentary"
REASONING = "reasoning"  # natural language narration (shown in spectator view)
THINKING = "thinking"  # internal chain-of-thought from think= block (logged, collapsed in UI)
GAME_OVER = "game_over"
MANA_TAPPED = "mana_tapped"
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

    def get_public_transcript(self, n_turns: int | None = None) -> list[dict]:
        """Return events safe to show both players (no library peek)."""
        events = self.all_events()
        if n_turns is not None:
            cutoff_turn = max(0, self._turn - n_turns)
            events = [e for e in events if e.get("turn", 0) >= cutoff_turn]
        # Strip REASONING events (those stay hidden from opponents)
        return [e for e in events if e["event"] != REASONING]

    def format_for_display(self, events: list[dict] | None = None) -> str:
        """Human-readable summary of events."""
        if events is None:
            events = self.get_public_transcript()
        lines = []
        for e in events:
            ev = e["event"]
            t = e.get("turn", "?")
            _p = e.get("phase", "")
            if ev == TURN_START:
                lines.append(f"\n=== Turn {t}: {e.get('active_player', '')} ===")
            elif ev == DRAW:
                lines.append(f"  {e['player']} draws {e['card']}")
            elif ev == CARD_PLAYED:
                targets = f" targeting {e['targets']}" if e.get("targets") else ""
                lines.append(f"  {e['player']} plays {e['card']}{targets}")
            elif ev == ATTACK:
                lines.append(f"  {e['player']} attacks with: {', '.join(e['attackers'])}")
            elif ev == BLOCK:
                lines.append(f"  {e['player']} blocks: {e['assignments']}")
            elif ev == DAMAGE:
                lines.append(f"  Damage: {e['source']} -> {e['target']} for {e['amount']}")
            elif ev == LIFE_CHANGE:
                lines.append(f"  {e['player']} life: {e['before']} -> {e['after']}")
            elif ev == CARD_DIES:
                lines.append(f"  {e['card']} dies")
            elif ev == PRIORITY_PASS:
                lines.append(f"  {e['player']} passes priority")
            elif ev == INSTANT_WINDOW:
                lines.append(f"  [instant window offered to {e.get('player', '?')}]")
            elif ev == JUDGE_RULING:
                lines.append(f"\n[JUDGE] {e['ruling']}")
            elif ev == COMMENTARY:
                lines.append(f"\n[COMMENTARY] {e['text']}")
            elif ev == GAME_OVER:
                lines.append(f"\n*** GAME OVER — Winner: {e['winner']} (turn {t}) ***")
            elif ev == MANA_TAPPED:
                pass  # too noisy for display
        return "\n".join(lines)
