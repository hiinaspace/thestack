"""Pre-game strategy + post-game reflection passes.

These are off-the-clock LLM calls that read and write a Persona's markdown
files. They run once before the main game loop (Strategist) and once after
(Reflector) per persona.
"""

from __future__ import annotations

import re

import ollama

from game.events import INFO, EventLog
from llm import dev_telemetry, oracle
from llm.client import DEFAULT_MODEL, chat_options
from llm.commentator import _format_engine_events
from llm.persona import Persona
from llm.prompts import format_public_state_for_commentator

# ---------------------------------------------------------------- Strategist


def write_pre_game_strategy(
    *,
    persona: Persona,
    opponent_name: str,
    deck: dict[str, int],
    client: ollama.Client,
    event_log: EventLog,
    model: str = DEFAULT_MODEL,
    verbose: bool = False,
) -> str:
    """Have the persona write strategy.md for the game about to start."""
    opp_notes = persona.opponent_entry(opponent_name)
    recent_memory = persona.recent_memory(max_chars=4000)
    deck_text = oracle.deck_listing(deck)

    system = (
        f"You are {persona.name}. Use your established voice below and write a "
        f"first-person strategy plan for the game you're about to play.\n\n"
        f"{persona.identity}"
    )
    user = (
        f"You are about to play against {opponent_name}.\n\n"
        f"## Your past notes on {opponent_name}\n{opp_notes or '(no entry yet — this is a new opponent)'}\n\n"
        f"## Recent memory from past games\n{recent_memory or '(none yet)'}\n\n"
        f"## Your deck this game ({sum(deck.values())} cards)\n{deck_text}\n\n"
        "Write 100–200 words of first-person strategy for this match: what's "
        "your win condition, which cards matter most, what do you fear from "
        f"{opponent_name}? Plain markdown, no preamble."
    )

    with dev_telemetry.call_span(
        "ollama_chat",
        agent=f"strategist:{persona.name}",
        model=model,
    ) as span:
        response = client.chat(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            think=False,
            options=chat_options(temperature=0.8),
        )
        dev_telemetry.record_ollama_response(span, response)
    plan = (response.message.content or "").strip()
    persona.write_strategy(plan)

    event_log.append(
        INFO,
        {"kind": "pre_game_strategy", "player": persona.name, "text": plan},
    )
    if verbose:
        preview = plan.replace("\n", " ")[:160]
        print(f"  [{persona.name} strategy] {preview}{'…' if len(plan) > 160 else ''}")
    return plan


# ----------------------------------------------------------------- Reflector


def reflect_after_game(
    *,
    persona: Persona,
    opponent_name: str,
    won: bool | None,
    turn_count: int,
    scratchpad: list[str],
    final_obs: dict | None = None,
    recent_public_actions: list[dict] | None = None,
    client: ollama.Client,
    event_log: EventLog,
    model: str = DEFAULT_MODEL,
    verbose: bool = False,
) -> None:
    """Run the post-game reflection and write back to memory.md + opponents.md."""
    outcome = "won" if won else ("lost" if won is False else "drew")
    pad = "\n".join(f"- {n}" for n in scratchpad) if scratchpad else "(no in-game notes)"
    final_state = (
        format_public_state_for_commentator(final_obs) if final_obs else "(final state unavailable)"
    )
    action_history = _format_reflection_actions(recent_public_actions or [])

    system = (
        f"You are {persona.name}, reflecting on a Magic: The Gathering game you "
        f"just finished. Stay in your established voice.\n\n{persona.identity}"
    )
    user = (
        f"You just {outcome} against {opponent_name} after {turn_count} turns.\n\n"
        f"## Your in-game scratchpad\n{pad}\n\n"
        f"## Authoritative final public state\n{final_state}\n\n"
        f"## Authoritative recent public actions\n{action_history}\n\n"
        f"## Your prior memory of {opponent_name}\n"
        f"{persona.opponent_entry(opponent_name) or '(none yet)'}\n\n"
        "Use only the authoritative state/actions and your scratchpad for "
        "concrete claims about this game. Do not invent cards, counterspells, "
        "hidden plays, or choices not shown above; if a detail is unclear, "
        "keep it general.\n\n"
        "Reply in three markdown sections, in this exact format:\n\n"
        "## TABLE TALK\n"
        "<1–3 first-person sentences spoken immediately after the game. Be "
        "entertaining, but anchor it in concrete cards or turns.>\n\n"
        "## MEMORY ENTRY\n"
        "<2–3 paragraphs reflecting on the game. Start with a heading like "
        f"`### Game vs {opponent_name} ({outcome}, T{turn_count})`. "
        "Talk about turning points, what worked, what didn't.>\n\n"
        f"## OPPONENT NOTE: {opponent_name}\n"
        f"<3–6 bullet points of what you now think about {opponent_name}'s "
        "playstyle, deck archetype, and tendencies. Build on prior notes if any. "
        "This block REPLACES the existing entry for them.>"
    )

    with dev_telemetry.call_span(
        "ollama_chat",
        agent=f"reflector:{persona.name}",
        model=model,
    ) as span:
        response = client.chat(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            think=False,
            options=chat_options(temperature=0.7),
        )
        dev_telemetry.record_ollama_response(span, response)
    text = (response.message.content or "").strip()
    table_talk, memory_block, opponent_block = _split_reflection(text, opponent_name)

    if memory_block:
        persona.append_memory(memory_block)
    if opponent_block:
        persona.upsert_opponent(opponent_name, opponent_block)

    event_log.append(
        INFO,
        {
            "kind": "post_game_reflection",
            "player": persona.name,
            "opponent": opponent_name,
            "outcome": outcome,
            "text": table_talk,
            "memory_added": bool(memory_block),
            "opponent_updated": bool(opponent_block),
            "memory_entry": memory_block,
            "opponent_note": opponent_block,
            "raw": text,
        },
    )
    if verbose:
        print(f"  [{persona.name} reflected on {opponent_name} — outcome: {outcome}]")


def _format_reflection_actions(actions: list[dict], max_lines: int = 36) -> str:
    """Compact public action history for grounding post-game memory writes."""
    lines = []
    for action in actions:
        who = action.get("player", "?")
        desc = action.get("description") or "?"
        phase = action.get("phase_step") or "?"
        turn = action.get("turn_number") or "?"
        result = _format_engine_events(
            action.get("engine_events") or [],
            action.get("player_names_by_id") or {},
        )
        is_pass = (action.get("reasoning") or "").startswith(
            "[autopass]"
        ) or desc == "Pass priority"
        if is_pass and not result:
            continue
        prefix = f"T{turn} {phase} - {who}"
        if is_pass:
            lines.append(f"- {prefix}: result after priority passed: {result}")
            continue
        line = f"- {prefix}: {desc}"
        if result:
            line += f" | Result: {result}"
        lines.append(line)
    return "\n".join(lines[-max_lines:]) if lines else "(no notable public actions captured)"


def _split_reflection(text: str, opponent_name: str) -> tuple[str, str, str]:
    """Pull the TABLE TALK, MEMORY ENTRY, and OPPONENT NOTE blocks out."""
    talk_re = re.compile(r"^##\s*TABLE TALK\s*$", re.MULTILINE)
    mem_re = re.compile(r"^##\s*MEMORY ENTRY\s*$", re.MULTILINE)
    opp_re = re.compile(rf"^##\s*OPPONENT NOTE:\s*{re.escape(opponent_name)}\s*$", re.MULTILINE)

    talk_match = talk_re.search(text)
    mem_match = mem_re.search(text)
    opp_match = opp_re.search(text)

    table_talk = ""
    if talk_match:
        talk_end = min([m.start() for m in (mem_match, opp_match) if m is not None] or [len(text)])
        table_talk = text[talk_match.end() : talk_end].strip()

    memory_block = ""
    if mem_match:
        mem_end = opp_match.start() if opp_match else len(text)
        memory_block = text[mem_match.end() : mem_end].strip()

    opponent_block = ""
    if opp_match:
        opponent_block = text[opp_match.end() :].strip()

    # Fallback: if no durable-memory headers were found, treat the whole reply
    # as both visible table talk and memory so the reflection is not lost.
    if not mem_match and not opp_match:
        table_talk = text.strip()
        memory_block = text.strip()

    if not table_talk and memory_block:
        table_talk = memory_block.split("\n\n", 1)[0].strip()

    return table_talk, memory_block, opponent_block
