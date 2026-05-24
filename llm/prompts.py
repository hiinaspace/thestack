"""System prompts and observation formatters for all LLM agents."""

from __future__ import annotations

from llm import oracle

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------


def build_player_system_prompt(
    player_name: str,
    opponent_name: str,
    *,
    identity: str = "",
    strategy: str = "",
    opponent_notes: str = "",
    recent_memory: str = "",
) -> str:
    """Compose the system prompt from persona files + the game-rules preamble.

    Empty strings for identity/strategy/etc. are allowed; the corresponding
    section is simply omitted. The trailing tool/rules preamble is always
    present.
    """
    sections: list[str] = [
        f"You are {player_name}, playing Magic: The Gathering against {opponent_name}."
    ]
    if identity:
        sections.append(f"## Your identity\n{identity}")
    if strategy:
        sections.append(f"## Your strategy for THIS game (written pre-game)\n{strategy}")
    if opponent_notes:
        sections.append(f"## What you remember about {opponent_name}\n{opponent_notes}")
    if recent_memory:
        sections.append(f"## Selected memories from past games\n{recent_memory}")

    sections.append(
        """## How this conversation works
This chat persists for the entire game. Each user turn shows you the current
game state plus a numbered list of legal actions; you commit to one by
calling the submit_action tool.

Tools:
  - take_note(note)        save a strategic note to your scratchpad (persists)
  - recall_strategy()      retrieve every note you've saved so far
  - submit_action(id, why) commit to a legal action and end this decision

Workflow each decision:
  1. Read the game state and legal actions.
  2. Optionally call take_note or recall_strategy to think out loud or check
     your earlier plans.
  3. Call submit_action exactly once with the action id and a one-or-two
     sentence in-character reasoning — that text is shown to spectators.

Rules of the game:
  - Every action in the list is legal RIGHT NOW; the engine validates.
  - To do nothing this priority, pick the "Pass priority" action.
  - You cannot see your opponent's hand or library.
  - X-cost spells: never pick X=0; if an action lists X-options, use the
    biggest X you can afford that still serves the plan.
  - Targeted spells: skip any action tagged [NO VALID TARGETS]. Removal hits
    the opponent's most dangerous creature; pump hits your own attacker.
  - Mana abilities ("{T}: Add {X}") only matter if you immediately spend that
    mana on a spell — mana left in your pool evaporates at end of step. If
    you have no spell to cast, pass priority instead of tapping a land for
    nothing.
  - If an action says "equivalent engine variants collapsed", that is one
    strategic choice with multiple identical engine bindings, not multiple
    spells or extra plays.

Play to win, but you are also a character — your reasoning is the show."""
    )
    return "\n\n".join(sections).strip()


def build_commentator_system_prompt() -> str:
    return """You are a tournament coverage analyst providing commentary for a Magic: The Gathering game.

You see the public game state — both battlefields, graveyards, life totals — but not either player's hand or library.
Each user turn gives you the current public state and the notable public actions
that happened. Produce 1-4 sentences of insightful commentary: use one terse
sentence when little changed, and save longer color for real swings.

You have a persistent memory of prior turns, so build an arc: who's winning,
who's adapting, what was the turning point. Be precise about combat: a block is
not automatically a trade. Only say a creature died, was sacrificed, or traded
if the action log or resulting battlefield/graveyard clearly supports it.
""".strip()


# ---------------------------------------------------------------------------
# Observation formatter
# ---------------------------------------------------------------------------


def format_observation(obs: dict, acting_player_name: str) -> str:
    """Format a TrainingObservation dict into a human-readable game state string."""
    players = {p["name"]: p for p in obs.get("players", [])}
    player_ids = {p["id"]: p["name"] for p in obs.get("players", [])}

    acting = players.get(acting_player_name, {})
    opponent_name = next((n for n in players if n != acting_player_name), "Opponent")
    opponent = players.get(opponent_name, {})

    phase = obs.get("phase", "?")
    step = obs.get("step", "?")
    turn = obs.get("turnNumber", "?")
    priority_id = obs.get("priorityPlayerId")
    priority_name = player_ids.get(priority_id, "?") if priority_id else "none"

    lines = [
        f"Turn {turn} | {phase} / {step} | Priority: {priority_name}",
        "",
        f"YOUR LIFE: {acting.get('lifeTotal', '?')} | {opponent_name}'s LIFE: {opponent.get('lifeTotal', '?')}",
    ]

    # Zones
    zones_by_owner: dict[str, dict[str, list]] = {}
    for z in obs.get("zones", []):
        owner_id = z["ownerId"]
        owner_name = player_ids.get(owner_id, owner_id)
        zones_by_owner.setdefault(owner_name, {})[z["zoneType"]] = z.get("cards", [])

    def fmt_permanent(c: dict) -> str:
        name = c.get("name", "?")
        is_creature = _has_card_type(c, "CREATURE")
        tapped = " [tapped]" if c.get("tapped") else ""
        sick = " (sick)" if c.get("summoningSick") else ""
        if is_creature:
            p, t = c.get("power"), c.get("toughness")
            kw = ", ".join(c.get("keywords", []))
            kw_str = f" [{kw}]" if kw else ""
            return f"  {name} {p}/{t}{kw_str}{tapped}{sick}"
        return f"  {name}{tapped}"

    # Acting player's battlefield
    my_bf = zones_by_owner.get(acting_player_name, {}).get("Battlefield", [])
    lines.append(f"\nYOUR BATTLEFIELD ({len(my_bf)}):")
    for c in my_bf:
        lines.append(fmt_permanent(c))
    if not my_bf:
        lines.append("  (empty)")

    # Opponent's battlefield
    opp_bf = zones_by_owner.get(opponent_name, {}).get("Battlefield", [])
    lines.append(f"\n{opponent_name}'s BATTLEFIELD ({len(opp_bf)}):")
    for c in opp_bf:
        lines.append(fmt_permanent(c))
    if not opp_bf:
        lines.append("  (empty)")

    # Acting player's hand (visible because revealAll=true)
    my_hand = zones_by_owner.get(acting_player_name, {}).get("Hand", [])
    lines.append(f"\nYOUR HAND ({len(my_hand)}):")
    for c in my_hand:
        name = c.get("name", "?")
        cost = c.get("manaCost", "")
        oracle = c.get("oracleText", "").replace("\n", "; ")
        types = "/".join(t.capitalize() for t in c.get("types", []))
        p, t = _power_toughness(c)
        pt = f" {p}/{t}" if p is not None and t is not None else ""
        lines.append(f"  {name}{pt} {cost} — {types}{': ' + oracle if oracle else ''}")
    if not my_hand:
        lines.append("  (empty)")

    # Opponent hand size only
    opp_hand_size = opponent.get("handSize", 0)
    lines.append(f"\n{opponent_name}'s HAND: {opp_hand_size} cards (hidden)")

    # Graveyards
    my_gy = zones_by_owner.get(acting_player_name, {}).get("Graveyard", [])
    opp_gy = zones_by_owner.get(opponent_name, {}).get("Graveyard", [])
    lines.append(f"\nYour graveyard: {[c['name'] for c in my_gy] or 'empty'}")
    lines.append(f"{opponent_name}'s graveyard: {[c['name'] for c in opp_gy] or 'empty'}")

    # Library sizes
    my_lib = acting.get("librarySize", 0)
    opp_lib = opponent.get("librarySize", 0)
    lines.append(f"\nLibrary: you {my_lib} | {opponent_name} {opp_lib}")

    # Stack
    stack = obs.get("stack", [])
    if stack:
        lines.append("\nSTACK (top last):")
        for item in stack:
            ctrl = player_ids.get(item.get("controllerId"), "?")
            lines.append(f"  {item['name']} (by {ctrl})")

    # Pending decision
    pd = obs.get("pendingDecision")
    if pd:
        lines.append(f"\nDECISION REQUIRED: {pd.get('prompt', '')} [{pd.get('kind', '')}]")

    return "\n".join(lines)


def format_legal_actions(legal_actions: list[dict]) -> str:
    """Format the legalActions list into a numbered menu."""
    if not legal_actions:
        return "No legal actions available."
    groups: dict[tuple, list[dict]] = {}
    ordered_keys: list[tuple] = []
    for action in legal_actions:
        key = _legal_action_key(action)
        if key not in groups:
            groups[key] = []
            ordered_keys.append(key)
        groups[key].append(action)

    lines = ["LEGAL ACTIONS:"]
    for key in ordered_keys:
        grouped = groups[key]
        a = grouped[0]
        action_id = a["actionId"]
        desc = a.get("description", "?")
        cost = a.get("manaCost")
        affordable = a.get("affordable", True)
        suffix = ""
        if cost:
            suffix = f" [cost: {cost}]"
        if not affordable:
            suffix += " [can't afford]"
        if len(grouped) > 1:
            suffix += f" [{len(grouped)} equivalent engine variants collapsed]"
        lines.append(f"  {action_id}: {desc}{suffix}")
    return "\n".join(lines)


def format_public_state_for_commentator(obs: dict) -> str:
    """Compact public state for the commentator (no hidden hand info)."""
    player_ids = {p["id"]: p["name"] for p in obs.get("players", [])}
    players = obs.get("players", [])
    turn = obs.get("turnNumber", "?")
    phase = obs.get("phase", "?")

    lines = [f"Turn {turn} | {phase}"]
    for p in players:
        lines.append(f"  {p['name']}: {p['lifeTotal']} life, {p['handSize']} cards in hand")

    zones_by_owner: dict[str, dict] = {}
    for z in obs.get("zones", []):
        owner_name = player_ids.get(z["ownerId"], z["ownerId"])
        zones_by_owner.setdefault(owner_name, {})[z["zoneType"]] = z

    for pname in player_ids.values():
        bf_zone = zones_by_owner.get(pname, {}).get("Battlefield", {})
        cards = bf_zone.get("cards", [])
        if cards:
            creature_strs = [
                f"{c['name']} {c.get('power')}/{c.get('toughness')}"
                if _has_card_type(c, "CREATURE")
                else c["name"]
                for c in cards
            ]
            lines.append(f"  {pname} battlefield: {', '.join(creature_strs)}")
        gy_zone = zones_by_owner.get(pname, {}).get("Graveyard", {})
        gy_cards = gy_zone.get("cards", [])
        if gy_cards:
            lines.append(f"  {pname} graveyard: {', '.join(c['name'] for c in gy_cards)}")

    return "\n".join(lines)


def _has_card_type(card: dict, card_type: str) -> bool:
    """Argentum serializes card types as uppercase enum names."""
    wanted = card_type.upper()
    return any(str(t).upper() == wanted for t in card.get("types", []))


def _power_toughness(card: dict) -> tuple[object | None, object | None]:
    """Use projected stats when present, printed oracle stats otherwise."""
    power = card.get("power")
    toughness = card.get("toughness")
    if power is not None or toughness is not None:
        return power, toughness
    oracle_card = oracle.card(card.get("name", ""))
    if oracle_card is None:
        return None, None
    return oracle_card.get("power"), oracle_card.get("toughness")


def _legal_action_key(action: dict) -> tuple:
    """Group engine-distinct but strategically identical action variants.

    Argentum may expose one action per identical card copy or mana source. For
    a text-only LLM menu those are the same choice; keeping only the first
    representative avoids implying that multiple copies are being cast.
    Target ids stay in the key so same-named but distinct targets do not merge.
    """
    return (
        action.get("kind"),
        action.get("description"),
        action.get("manaCost"),
        action.get("affordable", True),
        action.get("hasXCost", False),
        action.get("minTargets"),
        action.get("maxTargets"),
        action.get("requiresDamageDistribution", False),
        action.get("isManaAbility", False),
        action.get("isDecisionOption", False),
        tuple(action.get("targetEntityIds") or []),
    )
