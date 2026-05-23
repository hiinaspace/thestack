"""Pre-game strategy + post-game reflection passes.

These are off-the-clock LLM calls that read and write a Persona's markdown
files. They run once before the main game loop (Strategist) and once after
(Reflector) per persona.
"""

from __future__ import annotations

import re

import ollama

from game.events import INFO, EventLog
from llm import oracle
from llm.client import DEFAULT_MODEL
from llm.persona import Persona

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

    response = client.chat(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        think=False,
        options={"temperature": 0.8},
    )
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
    client: ollama.Client,
    event_log: EventLog,
    model: str = DEFAULT_MODEL,
    verbose: bool = False,
) -> None:
    """Run the post-game reflection and write back to memory.md + opponents.md."""
    outcome = "won" if won else ("lost" if won is False else "drew")
    pad = "\n".join(f"- {n}" for n in scratchpad) if scratchpad else "(no in-game notes)"

    system = (
        f"You are {persona.name}, reflecting on a Magic: The Gathering game you "
        f"just finished. Stay in your established voice.\n\n{persona.identity}"
    )
    user = (
        f"You just {outcome} against {opponent_name} after {turn_count} turns.\n\n"
        f"## Your in-game scratchpad\n{pad}\n\n"
        f"## Your prior memory of {opponent_name}\n"
        f"{persona.opponent_entry(opponent_name) or '(none yet)'}\n\n"
        "Reply in two markdown sections, in this exact format:\n\n"
        "## MEMORY ENTRY\n"
        "<2–3 paragraphs reflecting on the game. Start with a heading like "
        f"`### Game vs {opponent_name} ({outcome}, T{turn_count})`. "
        "Talk about turning points, what worked, what didn't.>\n\n"
        f"## OPPONENT NOTE: {opponent_name}\n"
        f"<3–6 bullet points of what you now think about {opponent_name}'s "
        "playstyle, deck archetype, and tendencies. Build on prior notes if any. "
        "This block REPLACES the existing entry for them.>"
    )

    response = client.chat(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        think=False,
        options={"temperature": 0.7},
    )
    text = (response.message.content or "").strip()
    memory_block, opponent_block = _split_reflection(text, opponent_name)

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
            "memory_added": bool(memory_block),
            "opponent_updated": bool(opponent_block),
            "raw": text,
        },
    )
    if verbose:
        print(f"  [{persona.name} reflected on {opponent_name} — outcome: {outcome}]")


def _split_reflection(text: str, opponent_name: str) -> tuple[str, str]:
    """Pull the MEMORY ENTRY and OPPONENT NOTE blocks out of a reflector reply."""
    mem_re = re.compile(r"^##\s*MEMORY ENTRY\s*$", re.MULTILINE)
    opp_re = re.compile(rf"^##\s*OPPONENT NOTE:\s*{re.escape(opponent_name)}\s*$", re.MULTILINE)

    mem_match = mem_re.search(text)
    opp_match = opp_re.search(text)

    memory_block = ""
    if mem_match:
        mem_end = opp_match.start() if opp_match else len(text)
        memory_block = text[mem_match.end() : mem_end].strip()

    opponent_block = ""
    if opp_match:
        opponent_block = text[opp_match.end() :].strip()

    # Fallback: if neither header was found, treat the whole reply as memory.
    if not mem_match and not opp_match:
        memory_block = text.strip()

    return memory_block, opponent_block
