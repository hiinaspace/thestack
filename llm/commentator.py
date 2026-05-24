"""Commentator agent — narrates each turn from public state only.

Like PlayerAgent, the commentator keeps one persistent conversation across the
game so it can build a narrative arc (callbacks, archetype reads, turning-point
recognition) instead of restarting cold each turn.
"""

from __future__ import annotations

import re

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
            "Give 1-4 sentences of commentary on what just happened this turn. "
            "Use one terse sentence if nothing much changed. Narrate concrete "
            "actions — what was cast, who attacked, and what actually died or "
            "survived. Treat each Result line as authoritative: never say a "
            "creature survived if the Result says it was destroyed. Do not "
            "infer a trade from a block unless the resulting state proves it. "
            "Refer back to earlier turns when relevant."
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

    def comment_on_game_over(
        self,
        obs: dict,
        *,
        winner_name: str | None,
        turn: int,
        recent_actions: list[dict] | None = None,
        verbose: bool = False,
    ) -> str:
        winner = winner_name or "draw"
        action_section = _format_recent_actions(recent_actions or [])
        prompt = (
            f"The game is over on turn {turn}. Winner: {winner}.\n\n"
            f"Final-turn public actions:\n{action_section}\n\n"
            f"Final public state:\n{format_public_state_for_commentator(obs)}\n\n"
            "Give a final 2-4 sentence wrap-up for the replay: why the winner "
            "won, the turning point, and one concrete lesson from the game. "
            "Treat the final-turn actions and final state as authoritative. "
            "Do not invent hidden cards, unseen choices, extra creatures, or "
            "unlisted spells."
        )
        response = self.agent.run(prompt, verbose=False)
        text = response.content
        if text:
            self.event_log.append(COMMENTARY, {"text": text, "turn": turn, "final": True})
            if verbose:
                print(f"\n[FINAL COMMENTARY] {text}")
        return text


def _format_recent_actions(actions: list[dict]) -> str:
    """Compact human-readable list of ACTION events from the past turn.

    Autopass / Pass-priority entries are usually noise, but combat damage and
    state-based deaths often occur after both players pass priority. Preserve
    those engine results as authoritative result-only lines.
    """
    lines = ["Actions taken this turn:"]
    wrote_any = False
    for a in actions:
        who = a.get("player", "?")
        desc = a.get("description", "?")
        result = _format_engine_events(
            a.get("engine_events") or [],
            a.get("player_names_by_id") or {},
        )
        is_pass = (a.get("reasoning") or "").startswith("[autopass]") or desc == "Pass priority"
        if is_pass:
            if result:
                lines.append(f"  - Result after priority passed: {result}")
                wrote_any = True
            continue
        lines.append(f"  - {who}: {desc}")
        if result:
            lines.append(f"    Result: {result}")
        wrote_any = True
    if not wrote_any:
        return "No notable actions this turn (both players mostly passed)."
    return "\n".join(lines)


def _format_engine_events(
    events: list[dict],
    player_names_by_id: dict[str, str] | None = None,
) -> str:
    """Summarize public rules-engine events without leaking hidden draws."""
    player_names_by_id = player_names_by_id or {}
    noise = {
        "PriorityChanged",
        "PhaseChanged",
        "StepChanged",
        "ManaAdded",
        "ManaSpent",
        "DecisionRequested",
        "DecisionSubmitted",
        "TurnChanged",
        "Untapped",
        "CommitCrime",
        "Resolved",
    }
    buckets: dict[str, list[str]] = {
        "Verified deaths": [],
        "Verified life changes": [],
        "Combat": [],
        "Board": [],
        "Game": [],
    }
    for event in events:
        event_type = event.get("type")
        if event_type in noise:
            continue
        text = _public_event_text(event, player_names_by_id)
        if not text:
            continue
        if event_type == "CreatureDestroyed":
            _append_unique(buckets["Verified deaths"], text)
        elif event_type == "LifeChanged":
            _append_unique(buckets["Verified life changes"], text)
        elif event_type in {"GameEnded", "PlayerLost"}:
            _append_unique(buckets["Game"], text)
        elif event_type in {"AttackersDeclared", "BlockersDeclared", "DamageDealt"}:
            _append_unique(buckets["Combat"], text)
        elif event_type in {"SpellCast", "ZoneChange", "Tapped"}:
            _append_unique(buckets["Board"], text)
        else:
            _append_unique(buckets["Board"], text)

    parts = []
    for label, texts in buckets.items():
        if texts:
            parts.append(f"{label}: {'; '.join(texts[:5])}")
    return " | ".join(parts)


def _append_unique(items: list[str], text: str) -> None:
    if text not in items:
        items.append(text)


def _public_event_text(event: dict, player_names_by_id: dict[str, str]) -> str:
    event_type = event.get("type")
    names = _event_player_names(event, player_names_by_id)
    who = ", ".join(names) if names else "A player"
    if event_type == "CardsDrawn":
        amount = event.get("amount") or "some"
        return f"{who} drew {amount} card(s)"
    if event_type == "LifeChanged" and event.get("text"):
        return f"{who}: {_format_life_change(event['text'])}"
    if event_type == "DamageDealt":
        source = _event_card_name(event)
        target = _damage_target_name(event, player_names_by_id)
        return f"{source} dealt {event.get('amount') or '?'} damage to {target}"
    if event_type == "Tapped":
        return f"{_event_card_name(event)} tapped"
    if event_type == "ZoneChange":
        return _format_zone_change(event)
    if event_type == "PlayerLost":
        return f"{_player_from_raw_text(event.get('text') or '', player_names_by_id) or who} lost"
    return event.get("text") or ""


def _event_player_names(event: dict, player_names_by_id: dict[str, str]) -> list[str]:
    names = []
    for player_id in event.get("playerIds") or []:
        name = player_names_by_id.get(player_id)
        if name and name not in names:
            names.append(name)
    return names


def _event_card_name(event: dict) -> str:
    return next(iter(event.get("cardNames") or []), "A source")


def _damage_target_name(event: dict, player_names_by_id: dict[str, str]) -> str:
    for entity_id in (event.get("entityIds") or [])[1:]:
        if entity_id in player_names_by_id:
            return player_names_by_id[entity_id]
    card_names = event.get("cardNames") or []
    if len(card_names) > 1:
        return card_names[1]
    m = re.search(r"\bto ([0-9a-f-]{36}|Player)$", event.get("text") or "", re.IGNORECASE)
    if m and m.group(1) in player_names_by_id:
        return player_names_by_id[m.group(1)]
    return "player"


def _format_life_change(text: str) -> str:
    m = re.match(r"Life changed (-?\d+) -> (-?\d+) \(([^)]+)\)", text)
    if not m:
        return text
    return f"{m.group(1)} -> {m.group(2)} ({m.group(3).lower()})"


def _format_zone_change(event: dict) -> str:
    card_name = _event_card_name(event)
    text = event.get("text") or ""
    m = re.match(r"^(.+?) moved from ([A-Z_]+) to ([A-Z_]+)$", text)
    if not m:
        return text
    destination = m.group(3)
    if destination == "BATTLEFIELD":
        return f"{card_name} entered battlefield"
    if destination == "GRAVEYARD":
        return f"{card_name} went to graveyard"
    return f"{card_name} moved {_fmt_zone(m.group(2))} -> {_fmt_zone(destination)}"


def _fmt_zone(zone: str) -> str:
    return zone.replace("_", " ").lower()


def _player_from_raw_text(text: str, player_names_by_id: dict[str, str]) -> str:
    m = re.search(r"playerId=([0-9a-f-]{36})", text, re.IGNORECASE)
    return player_names_by_id.get(m.group(1), "") if m else ""
