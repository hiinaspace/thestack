"""System prompts and observation formatters for all LLM agents."""

from __future__ import annotations

import re

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
  - submit_decision(response, why)
                            commit a structured DecisionResponse when asked

Workflow each decision:
  1. Read the game state and legal actions.
  2. Optionally call take_note or recall_strategy to think out loud or check
     your earlier plans.
  3. If a numbered legal-action menu is shown, call submit_action exactly once.
     If a structured decision response is requested, call submit_decision exactly
     once with the requested JSON response object.
  4. Include a one-or-two sentence in-character reasoning — that text is shown
     to spectators.

Rules of the game:
  - Every action in the list is legal RIGHT NOW; the engine validates.
  - The action text is precise. If it says "Attack with all (1 creatures)",
    exactly one creature can attack; do not count tapped, sick, or otherwise
    illegal creatures yourself.
  - In combat steps, you cannot go back to main phase to cast creatures or tap
    lands unless those actions are explicitly listed.
  - To do nothing this priority, pick the "Pass priority" action.
  - You cannot see your opponent's hand or library.
  - Same-named cards can be separate copies. If a legal action lists a spell
    you cast earlier, treat it as another available copy unless told otherwise.
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
  - Mulligans: 0-land and 1-land opening hands are usually mulligans; 2-4 lands
    with early plays are usually keeps. When bottoming after a mulligan, keep a
    functional land/spell mix.

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
        f"YOUR MANA POOL NOW: {_fmt_mana_pool(acting.get('manaPool') or {})}",
        f"{opponent_name}'s MANA POOL NOW: {_fmt_mana_pool(opponent.get('manaPool') or {})}",
        "Untapped lands are potential mana sources, not already-floating mana.",
    ]

    # Zones
    zones_by_owner: dict[str, dict[str, list]] = {}
    for z in obs.get("zones", []):
        owner_id = z["ownerId"]
        owner_name = player_ids.get(owner_id, owner_id)
        zones_by_owner.setdefault(owner_name, {})[z["zoneType"]] = z.get("cards", [])

    def fmt_creature(c: dict) -> str:
        name = c.get("name", "?")
        tapped = " [tapped]" if c.get("tapped") else ""
        sick = " (sick)" if c.get("summoningSick") else ""
        p, t = c.get("power"), c.get("toughness")
        kw = ", ".join(c.get("keywords", []))
        kw_str = f" [{kw}]" if kw else ""
        return f"    - {name} {p}/{t}{kw_str}{tapped}{sick}"

    def fmt_noncreature(c: dict) -> str:
        tapped = " [tapped]" if c.get("tapped") else ""
        return f"    - {c.get('name', '?')}{tapped}"

    def add_battlefield(owner_label: str, cards: list[dict]) -> None:
        creatures = [c for c in cards if _has_card_type(c, "CREATURE")]
        lands = [c for c in cards if _has_card_type(c, "LAND")]
        other = [
            c for c in cards if not _has_card_type(c, "CREATURE") and not _has_card_type(c, "LAND")
        ]

        lines.append(f"\n{owner_label} BATTLEFIELD ({len(cards)} permanents):")
        lines.append(f"  Creatures ({len(creatures)}):")
        if creatures:
            lines.extend(fmt_creature(c) for c in creatures)
        else:
            lines.append("    - none (lands and other noncreatures cannot attack or block)")

        lines.append(f"  Lands / mana sources ({len(lands)}; cannot attack or block):")
        if lands:
            lines.extend(fmt_noncreature(c) for c in lands)
        else:
            lines.append("    - none")

        if other:
            lines.append(f"  Other noncreature permanents ({len(other)}; cannot attack or block):")
            lines.extend(fmt_noncreature(c) for c in other)

    # Acting player's battlefield
    my_bf = zones_by_owner.get(acting_player_name, {}).get("Battlefield", [])
    add_battlefield("YOUR", my_bf)

    # Opponent's battlefield
    opp_bf = zones_by_owner.get(opponent_name, {}).get("Battlefield", [])
    add_battlefield(f"{opponent_name}'s", opp_bf)

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


def format_recent_public_actions(actions: list[dict], max_actions: int = 8) -> str:
    """Compact shared history for player prompts.

    This is passive result injection: the next decision prompt gets a concise
    public action/result feed, without spending another model turn on reactions.
    """
    if not actions:
        return "RECENT PUBLIC ACTIONS:\n  (none yet)"

    notable = [a for a in actions if _is_notable_public_action(a)]
    if not notable:
        return "RECENT PUBLIC ACTIONS:\n  (none yet)"

    lines = ["RECENT PUBLIC ACTIONS (oldest to newest):"]
    for action in notable[-max_actions:]:
        result = _format_public_engine_result(
            action.get("engine_events") or [],
            action.get("player_names_by_id") or {},
        )
        is_pass = _is_pass_action(action)
        if is_pass and result:
            lines.append(f"  - Result after priority passed: {result}")
            continue

        who = action.get("player", "?")
        desc = action.get("description") or "?"
        lines.append(f"  - {who}: {desc}")
        reasoning = (action.get("reasoning") or "").strip()
        if reasoning and not reasoning.startswith("[autopass]"):
            lines.append(f"    Stated reason: {_single_line(reasoning, 220)}")
        if result:
            lines.append(f"    Result: {result}")
    return "\n".join(lines)


def format_combat_evaluator(obs: dict, acting_player_name: str, legal_actions: list[dict]) -> str:
    """Small deterministic combat heuristic for the acting player's prompt."""
    phase = str(obs.get("phase", "")).upper()
    step = str(obs.get("step", "")).upper()
    if phase != "COMBAT" or step not in {"DECLARE_ATTACKERS", "DECLARE_BLOCKERS"}:
        return ""

    player_ids = {p["id"]: p["name"] for p in obs.get("players", [])}
    players = {p["name"]: p for p in obs.get("players", [])}
    opponent_name = next((n for n in players if n != acting_player_name), "Opponent")

    zones_by_owner: dict[str, dict[str, list]] = {}
    for z in obs.get("zones", []):
        owner_name = player_ids.get(z["ownerId"], z["ownerId"])
        zones_by_owner.setdefault(owner_name, {})[z["zoneType"]] = z.get("cards", [])

    mine = zones_by_owner.get(acting_player_name, {}).get("Battlefield", [])
    theirs = zones_by_owner.get(opponent_name, {}).get("Battlefield", [])
    my_creatures = [c for c in mine if _has_card_type(c, "CREATURE")]
    opp_blockers = [c for c in theirs if _has_card_type(c, "CREATURE") and not c.get("tapped")]
    action_descs = list(dict.fromkeys(str(a.get("description") or "") for a in legal_actions))

    lines = ["COMBAT EVALUATOR (heuristic; legal action text is authoritative):"]
    if step == "DECLARE_ATTACKERS":
        likely_attackers = [
            c for c in my_creatures if not c.get("tapped") and not c.get("summoningSick")
        ]
        lines.append(
            f"  Your likely available attackers: {_fmt_creature_list(likely_attackers) or 'none'}."
        )
        lines.append(
            f"  {opponent_name}'s untapped potential blockers: "
            f"{_fmt_creature_list(opp_blockers) or 'none'}."
        )
        if not opp_blockers:
            lines.append("  With no blockers, attacking is mostly a damage race decision.")
        else:
            lines.append(
                "  Small attackers may trade or die if blocked; preserve blockers when "
                "your life total is under immediate crack-back pressure."
            )
        for desc in action_descs:
            if not desc.startswith("Attack "):
                continue
            attackers = _attackers_for_action(desc, likely_attackers)
            damage = sum(_as_int(_power_toughness(c)[0]) for c in attackers)
            detail = f"    - {desc}: about {damage} unblocked damage"
            if opp_blockers:
                detail += f"; {len(opp_blockers)} possible blocker(s)"
            lines.append(detail)
        return "\n".join(lines)

    if step == "DECLARE_BLOCKERS":
        attackers = _attacking_creatures_from_actions(action_descs, theirs)
        lines.append(
            f"  Incoming attackers inferred from actions: {_fmt_creature_list(attackers) or 'unknown'}."
        )
        lines.append(
            f"  Your untapped blockers: {_fmt_creature_list([c for c in my_creatures if not c.get('tapped')]) or 'none'}."
        )
        evaluated = 0
        for desc in action_descs:
            m = re.match(r"Block (.+) with (.+)$", desc)
            if not m:
                continue
            attacker = _find_creature_by_name(theirs, m.group(1))
            blocker = _find_creature_by_name(mine, m.group(2))
            if not attacker or not blocker:
                continue
            lines.append(f"    - {desc}: {_block_outcome(attacker, blocker)}")
            evaluated += 1
            if evaluated >= 6:
                break
        if evaluated == 0:
            lines.append("  No profitable block choice is visible in the legal-action menu.")
        return "\n".join(lines)

    return ""


def format_mulligan_evaluator(
    obs: dict,
    acting_player_name: str,
    legal_actions: list[dict],
) -> str:
    """Deterministic opening-hand guidance for the mulligan phase."""
    if not any(a.get("kind") in {"KeepHand", "TakeMulligan", "BottomCards"} for a in legal_actions):
        return ""

    hand = _hand_for(obs, acting_player_name)
    lands = [c for c in hand if _has_card_type(c, "LAND")]
    nonlands = [c for c in hand if not _has_card_type(c, "LAND")]
    cheap = [c for c in nonlands if _as_int(c.get("manaValue")) <= 2]
    three_or_less = [c for c in nonlands if _as_int(c.get("manaValue")) <= 3]

    curve: dict[int, list[str]] = {}
    for card in nonlands:
        curve.setdefault(_as_int(card.get("manaValue")), []).append(card.get("name", "?"))

    lines = ["MULLIGAN EVALUATOR (heuristic; legal action text is authoritative):"]
    lines.append(
        "  This is a setup decision; use the Keep/Mulligan/Bottom actions, not the priority line."
    )
    lines.append(
        f"  Hand: {len(hand)} cards, {len(lands)} land(s), "
        f"{len(cheap)} nonland spell(s) costing <=2, {len(three_or_less)} costing <=3."
    )
    if lands:
        lines.append(f"  Lands: {', '.join(c.get('name', '?') for c in lands)}.")
    if curve:
        curve_text = "; ".join(
            f"MV {mv}: {', '.join(names[:4])}" for mv, names in sorted(curve.items())
        )
        lines.append(f"  Curve: {curve_text}.")

    if any(a.get("kind") == "BottomCards" for a in legal_actions):
        lines.append(
            "  Bottoming guidance: preserve 2-4 lands and early plays; bottom excess "
            "expensive spells, redundant lands above four, or narrow late-game cards first."
        )
    elif len(lands) == 0:
        lines.append("  Guidance: 0 lands is almost always a mulligan.")
    elif len(lands) == 1:
        lines.append(
            "  Guidance: 1 land is usually a mulligan unless the hand has several cheap plays "
            "and a realistic second-land plan."
        )
    elif 2 <= len(lands) <= 4:
        lines.append("  Guidance: 2-4 lands with early plays is usually keepable.")
    else:
        lines.append(
            "  Guidance: 5+ lands is flood-prone; keep only if the spells are unusually strong "
            "or the deck needs many lands."
        )
    return "\n".join(lines)


def format_structured_decision(obs: dict, acting_player_name: str) -> str:
    """Explain a pending structured DecisionResponse in JSON terms."""
    pd = obs.get("pendingDecision") or {}
    if not pd:
        return "No pending structured decision."

    kind = pd.get("kind", "?")
    decision_id = pd.get("decisionId", "")
    lines = [
        "STRUCTURED DECISION REQUIRED:",
        f"  Kind: {kind}",
        f"  Decision ID: {decision_id}",
        f"  Prompt: {pd.get('prompt', '')}",
    ]
    if pd.get("sourceName"):
        lines.append(f"  Source: {pd.get('sourceName')}")
    if pd.get("effectHint"):
        lines.append(f"  Effect hint: {pd.get('effectHint')}")

    shape = pd.get("shape") or {}
    if shape:
        lines.append(f"  Shape: {_compact_shape(shape)}")

    option_texts = pd.get("optionTexts") or []
    if option_texts:
        lines.append("  Text options:")
        for idx, text in enumerate(option_texts[:40]):
            lines.append(f"    - {idx}: {text}")

    modes = pd.get("modes") or []
    if modes:
        lines.append("  Modes:")
        for mode in modes:
            available = "" if mode.get("available", True) else " [unavailable]"
            lines.append(f"    - {mode.get('index')}: {mode.get('text')}{available}")

    target_requirements = pd.get("targetRequirements") or []
    legal_targets = pd.get("legalTargets") or {}
    if target_requirements:
        lines.append("  Target requirements:")
        for req in target_requirements:
            idx = req.get("index")
            lines.append(
                f"    - {idx}: {req.get('description')} "
                f"(choose {req.get('minTargets')}-{req.get('maxTargets')})"
            )
            for ref in legal_targets.get(str(idx), legal_targets.get(idx, []))[:30]:
                lines.append(f"      * {_fmt_decision_ref(ref)}")

    options = pd.get("options") or []
    if options:
        label = "Options"
        if pd.get("ordered"):
            label = "Orderable options (return in chosen order)"
        lines.append(f"  {label}:")
        for ref in options[:60]:
            lines.append(f"    - {_fmt_decision_ref(ref)}")

    distribution_targets = pd.get("distributionTargets") or []
    if distribution_targets:
        lines.append("  Distribution targets:")
        for item in distribution_targets:
            target = item.get("target") or {}
            max_part = f", max {item.get('max')}" if item.get("max") is not None else ""
            lines.append(
                f"    - {_fmt_decision_ref(target)} (min positive {item.get('min', 0)}{max_part})"
            )

    lines.append("  Response shape:")
    lines.append(f"    {_decision_response_example(kind, decision_id)}")
    lines.append(
        "Call submit_decision(response, reasoning). Do not call submit_action for this prompt."
    )
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
            creatures = [
                f"{c['name']} {c.get('power')}/{c.get('toughness')}"
                for c in cards
                if _has_card_type(c, "CREATURE")
            ]
            lands = [c["name"] for c in cards if _has_card_type(c, "LAND")]
            other = [
                c["name"]
                for c in cards
                if not _has_card_type(c, "CREATURE") and not _has_card_type(c, "LAND")
            ]
            parts = [
                f"creatures: {', '.join(creatures) if creatures else 'none'}",
                f"lands: {', '.join(lands) if lands else 'none'}",
            ]
            if other:
                parts.append(f"other: {', '.join(other)}")
            lines.append(f"  {pname} battlefield: {' | '.join(parts)}")
        gy_zone = zones_by_owner.get(pname, {}).get("Graveyard", {})
        gy_cards = gy_zone.get("cards", [])
        if gy_cards:
            lines.append(f"  {pname} graveyard: {', '.join(c['name'] for c in gy_cards)}")

    return "\n".join(lines)


def _hand_for(obs: dict, player_name: str) -> list[dict]:
    player_ids = {p["id"]: p["name"] for p in obs.get("players", [])}
    for zone in obs.get("zones", []):
        owner_name = player_ids.get(zone.get("ownerId"), zone.get("ownerId"))
        if owner_name == player_name and str(zone.get("zoneType", "")).upper() == "HAND":
            return zone.get("cards", [])
    return []


def _compact_shape(shape: dict) -> str:
    parts = []
    for key in (
        "minSelections",
        "maxSelections",
        "numericMin",
        "numericMax",
        "totalToDistribute",
        "budget",
    ):
        value = shape.get(key)
        if value not in (None, 0, [], {}):
            parts.append(f"{key}={value}")
    colors = shape.get("availableColors") or []
    if colors:
        parts.append(f"availableColors={list(colors)}")
    return ", ".join(parts) if parts else "no extra constraints"


def _fmt_decision_ref(ref: dict) -> str:
    entity_id = ref.get("entityId", "?")
    label = ref.get("label") or entity_id
    card = ref.get("card") or {}
    card_info = ref.get("cardInfo") or {}
    source = card or card_info
    details = []
    if source.get("manaCost"):
        details.append(source["manaCost"])
    types = source.get("types") or source.get("typeLine")
    if types:
        if isinstance(types, list):
            details.append("/".join(str(t).title() for t in types))
        else:
            details.append(str(types))
    power, toughness = source.get("power"), source.get("toughness")
    if power is not None and toughness is not None:
        details.append(f"{power}/{toughness}")
    tapped = " tapped" if source.get("tapped") else ""
    detail_text = f" ({', '.join(details)})" if details else ""
    return f"{label}{detail_text}{tapped} [{entity_id}]"


def _decision_response_example(kind: str, decision_id: str) -> str:
    examples = {
        "CHOOSE_TARGETS": (
            '{"type":"TargetsResponse","decisionId":"%s",'
            '"selectedTargets":{"0":["target-entity-id"]}}'
        ),
        "DISTRIBUTE": (
            '{"type":"DistributionResponse","decisionId":"%s",'
            '"distribution":{"target-entity-id":1}}'
        ),
        "SELECT_CARDS": (
            '{"type":"CardsSelectedResponse","decisionId":"%s","selectedCards":["card-entity-id"]}'
        ),
        "SEARCH_LIBRARY": (
            '{"type":"CardsSelectedResponse","decisionId":"%s","selectedCards":["card-entity-id"]}'
        ),
        "ORDER_OBJECTS": (
            '{"type":"OrderedResponse","decisionId":"%s",'
            '"orderedObjects":["first-entity-id","second-entity-id"]}'
        ),
        "REORDER_LIBRARY": (
            '{"type":"OrderedResponse","decisionId":"%s",'
            '"orderedObjects":["top-card-id","next-card-id"]}'
        ),
        "CHOOSE_MODE": ('{"type":"ModesChosenResponse","decisionId":"%s","selectedModes":[0]}'),
        "BUDGET_MODAL": (
            '{"type":"BudgetModalResponse","decisionId":"%s","selectedModeIndices":[0]}'
        ),
        "ASSIGN_DAMAGE": (
            '{"type":"DamageAssignmentResponse","decisionId":"%s",'
            '"assignments":{"target-entity-id":1}}'
        ),
        "SELECT_MANA_SOURCES": (
            '{"type":"ManaSourcesSelectedResponse","decisionId":"%s","autoPay":true}'
        ),
        "SPLIT_PILES": (
            '{"type":"PilesSplitResponse","decisionId":"%s","piles":[["card-a"],["card-b"]]}'
        ),
    }
    return (
        examples.get(
            kind,
            '{"type":"<ResponseType>","decisionId":"%s"}',
        )
        % decision_id
    )


def _is_notable_public_action(action: dict) -> bool:
    if not _is_pass_action(action):
        return True
    return bool(
        _format_public_engine_result(
            action.get("engine_events") or [],
            action.get("player_names_by_id") or {},
        )
    )


def _is_pass_action(action: dict) -> bool:
    desc = action.get("description") or ""
    reasoning = action.get("reasoning") or ""
    return desc == "Pass priority" or reasoning.startswith("[autopass]")


def _fmt_creature_list(cards: list[dict]) -> str:
    return ", ".join(_fmt_creature_short(c) for c in cards)


def _fmt_creature_short(card: dict) -> str:
    p, t = _power_toughness(card)
    tapped = " tapped" if card.get("tapped") else ""
    sick = " sick" if card.get("summoningSick") else ""
    return f"{card.get('name', '?')} {p}/{t}{tapped}{sick}"


def _attackers_for_action(desc: str, likely_attackers: list[dict]) -> list[dict]:
    if desc.startswith("Attack with all"):
        return likely_attackers
    m = re.match(r"Attack with (.+?)(?: \(|$)", desc)
    if not m:
        return []
    card = _find_creature_by_name(likely_attackers, m.group(1))
    return [card] if card else []


def _attacking_creatures_from_actions(
    action_descs: list[str], battlefield: list[dict]
) -> list[dict]:
    attackers: list[dict] = []
    for desc in action_descs:
        m = re.match(r"Block (.+) with .+$", desc)
        if not m:
            continue
        card = _find_creature_by_name(battlefield, m.group(1))
        if card and card not in attackers:
            attackers.append(card)
    return attackers


def _find_creature_by_name(cards: list[dict], name: str) -> dict | None:
    wanted = name.strip().lower()
    return next(
        (
            c
            for c in cards
            if _has_card_type(c, "CREATURE") and str(c.get("name", "")).lower() == wanted
        ),
        None,
    )


def _block_outcome(attacker: dict, blocker: dict) -> str:
    attacker_power, attacker_toughness = (_as_int(v) for v in _power_toughness(attacker))
    blocker_power, blocker_toughness = (_as_int(v) for v in _power_toughness(blocker))
    attacker_dies = blocker_power >= attacker_toughness
    blocker_dies = attacker_power >= blocker_toughness
    if attacker_dies and not blocker_dies:
        return "favorable block, kills attacker and blocker survives"
    if attacker_dies and blocker_dies:
        return "trade, both creatures likely die"
    if not attacker_dies and blocker_dies:
        return "chump block, blocker likely dies without killing attacker"
    return "stall block, neither creature likely dies"


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _format_public_engine_result(
    events: list[dict],
    player_names_by_id: dict[str, str] | None = None,
) -> str:
    """Summarize public rules-engine events without leaking hidden draw names."""
    player_names_by_id = player_names_by_id or {}
    buckets: dict[str, list[str]] = {
        "deaths": [],
        "life": [],
        "combat": [],
        "board": [],
        "game": [],
    }
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
    for event in events:
        event_type = event.get("type")
        if event_type in noise:
            continue
        text = _public_event_text(event, player_names_by_id)
        if not text:
            continue
        if event_type == "CreatureDestroyed":
            _append_unique(buckets["deaths"], text)
        elif event_type == "LifeChanged":
            _append_unique(buckets["life"], text)
        elif event_type in {"AttackersDeclared", "BlockersDeclared", "DamageDealt"}:
            _append_unique(buckets["combat"], text)
        elif event_type in {"PlayerLost", "GameEnded"}:
            _append_unique(buckets["game"], text)
        elif event_type in {"SpellCast", "ZoneChange", "Tapped", "CardsDrawn"}:
            _append_unique(buckets["board"], text)
        else:
            _append_unique(buckets["board"], text)

    parts = []
    for label, texts in buckets.items():
        if texts:
            parts.append(f"{label}: {'; '.join(texts[:4])}")
    return " | ".join(parts)


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


def _append_unique(items: list[str], text: str) -> None:
    if text not in items:
        items.append(text)


def _single_line(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _has_card_type(card: dict, card_type: str) -> bool:
    """Argentum serializes card types as uppercase enum names."""
    wanted = card_type.upper()
    return any(str(t).upper() == wanted for t in card.get("types", []))


def _fmt_mana_pool(pool: dict) -> str:
    parts = []
    for color in ("white", "blue", "black", "red", "green", "colorless"):
        amount = pool.get(color, 0)
        if amount:
            parts.append(f"{amount} {color}")
    return ", ".join(parts) if parts else "empty"


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
