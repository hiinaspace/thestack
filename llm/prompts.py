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
    decklist: str = "",
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
    if decklist:
        sections.append(f"## Your full decklist this game\n{decklist}")
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

## Your three voice registers
You speak in three distinct registers. Use them deliberately.

  - **think** (private): your raw strategic reasoning in the thinking block.
    Spectators see it as analysis; it is not in your character voice.
  - **monologue** (audience-facing, in voice): in-character internal voice. The
    spectator transcript shows it; your opponent does NOT read it. Use to set
    up bluffs, react to your opponent's table talk, or build tension before a
    big play.
  - **table_talk** (opponent-facing, in voice): in-character lines spoken AT
    your opponent. Their agent will read these at the start of their next
    decision — this is real dialogue between two players. Use sparingly; one
    pointed line beats a paragraph.

Your submit_action `reasoning` argument is the action declaration spectators
see (e.g. "I burn for five. Lava Axe."). Keep it short and in voice.

Tools (EXACTLY these — do NOT invent other tool names; unknown names fail):
  - take_note(note)        save a cross-turn strategic note (persists all game)
  - recall_strategy()      retrieve every note you've saved so far
  - monologue(text)        speak an internal-voice line (spectators only)
  - table_talk(text)       say a line to your opponent (they will read it)
  - set_turn_plan(intent, action_sequence, notes)
                            commit your plan for THIS turn (call once per turn)
  - update_turn_plan(revised_intent, reason)
                            revise the plan mid-turn after something changed
  - submit_action(action_id, reasoning)
                            REQUIRED: commit to one numbered legal action.
                            `action_id` is the integer from the 'Legal
                            actions' list. Must be called once per decision.
  - submit_decision(response, reasoning)
                            commit a structured DecisionResponse when asked

Turn plan workflow — think upfront, execute terse:
  - On your FIRST decision of a turn, call set_turn_plan with a one-sentence
    intent ("play Mountain, cast Raging Cougar, attack with everything"). The
    plan is shown at the top of every subsequent prompt this turn.
  - On subsequent decisions, EXECUTE the next step of the plan without
    re-deriving it. Keep monologue and reasoning tight — the plan is the
    thinking; the action is the execution.
  - If something material changes (opponent plays a surprise, you draw a key
    card off a tutor, post-combat board shift), call update_turn_plan with
    the new intent + reason, then continue.
  - At turn boundaries the plan clears automatically; commit a fresh one.

Per-decision workflow (do all of this in ONE assistant response — chain the
tool calls together, do not split across multiple turns):
  1. Read the game state and legal actions. If your opponent just spoke,
     decide whether to respond with table_talk.
  2. If no turn plan is set, this is your first decision of the turn — call
     set_turn_plan after a brief monologue setting the scene.
  3. If a plan IS set, execute the next step. Call monologue only if the
     moment warrants flavor (a draw paid off, opponent blocked surprisingly,
     a finisher lands). Routine plays (drop a land, pass priority on opp's
     upkeep) need no voice.
  4. Call submit_action(action_id=<int from the list>, reasoning="<one line>")
     exactly once to commit — this MUST happen; the harness will not move on
     without it. The reasoning is your spoken action declaration ("I burn
     for five. Lava Axe."). Keep it short.

A first decision of a turn (chained in one response):
    monologue("Mountain, Cougar, swing. They die.") →
    set_turn_plan("ramp + attack", ["play Mountain", "cast Raging Cougar",
        "attack with all"], "Bryn at 5, lethal in two turns.") →
    submit_action(action_id=3, reasoning="Mountain down.")
A follow-up decision the same turn:
    submit_action(action_id=7, reasoning="Cougar.")
A dull turn (passing in upkeep, opponent's main phase): submit_action only.

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
  - Beneficial targeted spells (for example "gets +N/+N", gains flying, or
    returns a creature card from a graveyard to hand) are usually for your own
    cards. Destructive or damage spells are usually for opposing cards.
  - Read mass-effect text literally: "each creature" does not hit players
    unless the text also names players.
  - Mana abilities ("{T}: Add {X}") only matter if you immediately spend that
    mana on a spell — mana left in your pool evaporates at end of step. If
    you have no spell to cast, pass priority instead of tapping a land for
    nothing.
  - A `(×N copies)` or `(×N untapped)` suffix on an action means N
    interchangeable engine instances collapsed into one menu entry — pick
    the listed ID and the engine binds to one of them; it is NOT a chance
    to play multiple at once.
  - Mulligans: 0-land and 1-land opening hands are usually mulligans; 2-4 lands
    with early plays are usually keeps. When bottoming after a mulligan, keep a
    functional land/spell mix.

Play to win, but you are also a character — your reasoning, monologue, and
table talk are the show. Stay in voice."""
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
        _format_mana_position(obs, acting_player_name),
        f"{opponent_name}'s mana pool: {_fmt_mana_pool(opponent.get('manaPool') or {})}",
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


def format_recent_public_actions(
    actions: list[dict], max_actions: int = 2, max_full_actions: int = 1
) -> str:
    """Compact dialogue-style history for player prompts.

    Renders the most recent action with full cartoon framing (table talk,
    play, declaration, engine result) and a small number of slightly older
    actions as compact one-liners. Older history beyond ``max_actions``
    intentionally drops out: the persistent per-game LLM conversation
    (Ollama agent.history / ClaudeSDKClient session) already carries every
    earlier turn as a prior user/assistant exchange, so re-rendering them in
    every new user message bloats the message and (for Claude) costs cache
    misses on the redundant prefix.

    ``max_full_actions`` controls how many of the most recent actions get
    the full multi-line framing. The rest are rendered as one-line digests.
    """
    if not actions:
        return "--- WHAT JUST HAPPENED ---\n  (the game is just beginning)"

    notable = [a for a in actions if _is_notable_public_action(a)]
    if not notable:
        return "--- WHAT JUST HAPPENED ---\n  (no notable actions yet)"

    recent = notable[-max_actions:]
    full_cutoff = max(0, len(recent) - max_full_actions)

    lines = ["--- WHAT JUST HAPPENED (oldest to newest) ---"]
    for idx, action in enumerate(recent):
        result = _format_public_engine_result(
            action.get("engine_events") or [],
            action.get("player_names_by_id") or {},
        )
        is_pass = _is_pass_action(action)
        who = action.get("player", "?")
        desc = action.get("description") or "?"

        if idx < full_cutoff:
            # Compact one-liner for older context.
            if is_pass and result:
                lines.append(f"  (earlier) Result after priority passed: {result}")
                continue
            digest = f"  (earlier) {who}: {desc}"
            if result:
                digest += f" | Result: {result}"
            lines.append(digest)
            continue

        # Full cartoon framing for the most recent action(s).
        if is_pass and result:
            lines.append(f"  Result after priority passed: {result}")
            continue

        for line in action.get("table_talk") or []:
            text = _single_line(str(line), 240)
            if text:
                lines.append(f'  {who} (table talk): "{text}"')

        lines.append(f"  {who} played: {desc}")

        reasoning = (action.get("reasoning") or "").strip()
        if reasoning and not reasoning.startswith("[autopass]"):
            lines.append(f'  {who} declared: "{_single_line(reasoning, 220)}"')

        if result:
            lines.append(f"  Engine result: {result}")
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
    constraint_line = _decision_constraint_sentence(pd)
    lines = [
        "STRUCTURED DECISION REQUIRED:",
        f"  Kind: {kind}",
        f"  Decision ID: {decision_id}",
        f"  Prompt: {pd.get('prompt', '')}",
        f"  REQUIRED: {constraint_line}",
    ]
    if pd.get("sourceName"):
        lines.append(f"  Source: {pd.get('sourceName')}")
    if pd.get("effectHint"):
        lines.append(f"  Effect hint: {pd.get('effectHint')}")

    shape = pd.get("shape") or {}
    if shape:
        lines.append(f"  Shape (machine-readable): {_compact_shape(shape)}")

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

    lines.append(
        "  Response shape (illustrative — picks the first legal option; "
        "substitute the entityIds YOU actually choose based on the board):"
    )
    lines.append(f"    {_decision_response_example(pd)}")
    lines.append(
        "Call submit_decision(response, reasoning). Do not call submit_action for this prompt. "
        "Copy entityIds verbatim from the lists above; do not invent or modify them."
    )
    return "\n".join(lines)


def format_legal_actions(
    legal_actions: list[dict],
    obs: dict | None = None,
    acting_player_name: str | None = None,
) -> str:
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
        suffix = ""
        if cost:
            suffix = f" [cost: {cost}]"
        # Affordability is intentionally NOT surfaced as a separate label —
        # the engine's `affordable` field reflects the mana pool *right now*
        # and ignores untapped lands, which misled the LLM into thinking
        # castable spells were impossible. The YOUR MANA block in
        # format_observation gives the LLM the synthesized total spendable;
        # let it compare against the cost suffix above.
        if len(grouped) > 1:
            if a.get("isManaAbility"):
                suffix += f" (×{len(grouped)} untapped)"
            else:
                suffix += f" (×{len(grouped)} copies)"
        target_note = _targeting_note(a, obs, acting_player_name)
        if target_note:
            suffix += f" [{target_note}]"
        lines.append(f"  {action_id}: {desc}{suffix}")
    return "\n".join(lines)


def _summarize_react_events(engine_events: list[dict] | None) -> str:
    """Compact one-line-each summary of engine events for a react prompt."""
    if not engine_events:
        return "(no engine events to summarize)"
    keep = {
        "CardsDrawn",
        "DamageDealt",
        "LifeChanged",
        "CreatureDestroyed",
        "PlayerLost",
        "GameEnded",
    }
    lines = []
    for ev in engine_events:
        t = ev.get("type")
        if t in keep:
            text = ev.get("text", "")
            lines.append(f"  - {t}: {text}" if text else f"  - {t}")
    return "\n".join(lines) if lines else "(no narratively-significant events)"


def format_react_prompt(
    obs: dict,
    player_name: str,
    trigger: str,
    engine_events: list[dict] | None,
    turn_plan: dict | None,
) -> str:
    """Build the user message for a react() hook (draw or post-combat).

    React prompts are narration-first: the LLM is told there is NO priority
    decision to commit, only a beat to deliver. Available tools are
    monologue, table_talk, take_note, set_turn_plan, update_turn_plan. The
    SDK / Ollama loops are expected to be called with ``wait_for_commit=False``
    so they exit cleanly when the model stops calling tools.
    """
    if trigger == "draw":
        framing = (
            "DRAW REACTION — you just drew this turn's card(s). This is both "
            "a narrative beat AND your tactical pivot: the new information "
            "shapes the turn. Deliver one in-character monologue() line "
            "reacting to what you drew (cartoon-energy: 'finally, the card I "
            "needed!' or 'great, a Mountain, again'), then call set_turn_plan "
            "with your committed intent for the turn. table_talk is optional "
            "and rare here. NO submit_action — there is no priority decision "
            "right now."
        )
    elif trigger == "combat_resolution":
        framing = (
            "POST-COMBAT REACTION — combat just resolved. This is the "
            "climax beat of the turn even when nothing died: a duelist saves "
            "the smirk or the 'argh' for the moment of impact. Deliver one "
            "monologue() line reacting in voice (you may know what was "
            "coming, but you've been HOLDING the reaction for this moment), "
            "and one table_talk() line at your opponent if the moment "
            "warrants a quip. If the board materially shifted, call "
            "update_turn_plan with the new intent. NO submit_action — there "
            "is no priority decision right now."
        )
    else:
        framing = f"REACTION — trigger={trigger}. Deliver one beat in voice."

    parts = [
        framing,
        "",
        "What just happened in the engine:",
        _summarize_react_events(engine_events),
        "",
        format_turn_plan(turn_plan),
    ]
    return "\n".join(parts)


def format_turn_plan(plan: dict | None) -> str:
    """Render the turn plan for inclusion at the top of a choose_action prompt.

    Returns a no-plan-yet directive when ``plan`` is None — that branch is
    deliberately short, since it appears on the player's first decision of
    every turn. Returns a structured block (intent + sequence + notes +
    revisions) once the plan is set, so subsequent decisions can execute
    without re-deriving.
    """
    if not plan:
        return (
            "TURN PLAN: not committed yet. Set one with set_turn_plan(intent, "
            "action_sequence, notes) before you submit your first action this "
            "turn — it will surface here on every subsequent decision."
        )
    lines = ["TURN PLAN (committed earlier this turn — execute against it):"]
    lines.append(f"  Intent: {plan.get('intent', '')}")
    seq = plan.get("action_sequence") or []
    if seq:
        joined = " → ".join(seq)
        lines.append(f"  Sequence: {joined}")
    notes = plan.get("notes")
    if notes:
        lines.append(f"  Notes: {notes}")
    revisions = plan.get("revisions") or []
    if revisions:
        lines.append("  Revisions:")
        for r in revisions:
            lines.append(f"    - {r.get('reason', '')} → {r.get('to', '')}")
    lines.append(
        "Execute the next step. Skip monologue on routine plays; revise with "
        "update_turn_plan only if something material changed."
    )
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


def _decision_constraint_sentence(pd: dict) -> str:
    """Plain-English restatement of the constraints in pd.shape + pd.*.

    The structured prompt previously surfaced cardinality only through the
    ``Shape:`` line (e.g. ``minSelections=1, maxSelections=1``). Reflections
    from both gemma4:e4b and claude-haiku-4-5 specifically flagged that
    burying the count there made it easy for the model to over- or
    under-select. The English-sentence restatement is intentionally
    redundant with the Shape line; the Shape line stays for machine-readable
    parsing, this line stays for the LLM's natural-language reasoning.
    """
    kind = pd.get("kind", "")
    shape = pd.get("shape") or {}
    min_sel = int(shape.get("minSelections") or 0)
    max_sel = int(shape.get("maxSelections") or 0)

    def _count_phrase(min_n: int, max_n: int, noun: str) -> str:
        if min_n == max_n:
            return f"exactly {min_n} {noun}{'s' if min_n != 1 else ''}"
        if min_n == 0 and max_n > 0:
            return f"0 to {max_n} {noun}s (zero is allowed)"
        if min_n > 0 and max_n > 0:
            return f"between {min_n} and {max_n} {noun}s"
        if min_n > 0 and max_n == 0:
            return f"at least {min_n} {noun}{'s' if min_n != 1 else ''}"
        return f"some {noun}s"

    if kind == "CHOOSE_TARGETS":
        reqs = pd.get("targetRequirements") or []
        if not reqs:
            return "choose targets per the prompt"
        parts = []
        for req in reqs:
            min_t = int(req.get("minTargets") or 0)
            max_t = int(req.get("maxTargets") or 0)
            parts.append(
                f"slot {req.get('index', '?')}: "
                f"{_count_phrase(min_t, max_t, 'target')} from the legal targets list"
            )
        return "; ".join(parts) + "."

    if kind == "DISTRIBUTE":
        total = shape.get("totalToDistribute")
        min_per = int(pd.get("minPerTarget") or 0)
        target_count = len(pd.get("distributionTargets") or [])
        partial_phrase = (
            " (allowPartial is true; the total may be less if you choose)"
            if pd.get("allowPartial")
            else ""
        )
        # Effective max chosen targets is min(maxSelections, distributionTargets count).
        max_chosen = max_sel or target_count
        chosen_phrase = _count_phrase(max(1, min_sel), max_chosen, "target")
        per_phrase = (
            f" with at least {min_per} per chosen target"
            if min_per > 0
            else " (per-target minimum: 0; any nonzero is fine)"
        )
        if total is not None:
            return (
                f"distribute exactly {total} total across {chosen_phrase}{per_phrase}"
                f"{partial_phrase}."
            )
        return f"distribute across {chosen_phrase}{per_phrase}{partial_phrase}."

    if kind in {"SELECT_CARDS", "SEARCH_LIBRARY"}:
        option_count = len(pd.get("options") or [])
        max_n = max_sel or option_count
        cancel_note = (
            " (canCancel is true; if no card fits, return an empty selection)"
            if pd.get("canCancel") and min_sel == 0
            else ""
        )
        return f"select {_count_phrase(min_sel, max_n, 'card')} from the options.{cancel_note}"

    if kind in {"ORDER_OBJECTS", "REORDER_LIBRARY"}:
        n = len(pd.get("options") or [])
        return f"return all {n} options in your chosen order (every option exactly once)."

    if kind == "CHOOSE_MODE":
        available = sum(1 for m in (pd.get("modes") or []) if m.get("available", True))
        n_required = max(1, min_sel)
        max_n = max_sel or available
        return (
            f"choose {_count_phrase(n_required, max_n, 'mode')} from the "
            f"{available} available mode(s)."
        )

    if kind == "BUDGET_MODAL":
        budget = shape.get("budget")
        return (
            f"choose any subset of modes whose total cost is within budget={budget}."
            if budget is not None
            else "choose any subset of modes that fits the budget shown."
        )

    if kind == "ASSIGN_DAMAGE":
        total = shape.get("totalToDistribute") or shape.get("budget")
        return (
            f"assign exactly {total} total damage across the targets listed."
            if total is not None
            else "assign damage across the targets listed; totals from the prompt."
        )

    if kind == "SELECT_MANA_SOURCES":
        return "confirm autoPay=true unless you have a specific mana-source plan."

    if kind == "SPLIT_PILES":
        return "split the options into the listed piles; each option goes in exactly one pile."

    if kind in {"YES_NO", "CHOOSE_OPTION", "CHOOSE_NUMBER", "CHOOSE_COLOR"}:
        # These fold into legalActions; structured response is uncommon.
        return f"answer the {kind} prompt above."

    return "satisfy the decision constraints described above."


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


def _decision_response_example(pd: dict) -> str:
    """Build an example DecisionResponse, substituting REAL entityIds when present.

    Earlier versions of this function emitted literal placeholder tokens like
    ``"card-entity-id"`` in the example JSON. Smaller models (observed with
    gemma4:e4b) copy that placeholder verbatim and concatenate it with a real
    id — emitting e.g. ``"card-entity-id-L-time-ebb"`` — which fails
    validation. Pulling a real entityId out of the pending decision keeps the
    example concretely correct, so even a model that "just copies the shape"
    produces something the engine will accept.
    """
    import json as _json

    kind = pd.get("kind", "")
    decision_id = pd.get("decisionId", "")

    def _first_option_id() -> str:
        for opt in pd.get("options") or []:
            entity_id = opt.get("entityId")
            if entity_id:
                return str(entity_id)
        return "<entityId-from-options-above>"

    def _first_n_option_ids(n: int) -> list[str]:
        ids = [str(o["entityId"]) for o in (pd.get("options") or []) if o.get("entityId")]
        if len(ids) >= n:
            return ids[:n]
        # Pad with obvious placeholders so the shape is still readable.
        ids += [f"<entityId-{i + 1}-from-options-above>" for i in range(n - len(ids))]
        return ids

    def _first_distribution_id() -> str:
        for dt in pd.get("distributionTargets") or []:
            entity_id = (dt.get("target") or {}).get("entityId")
            if entity_id:
                return str(entity_id)
        return "<entityId-from-distributionTargets-above>"

    def _first_target_id_for_slot(slot: int) -> str:
        legal = pd.get("legalTargets") or {}
        candidates = legal.get(str(slot)) or legal.get(slot) or []
        for ref in candidates:
            entity_id = ref.get("entityId")
            if entity_id:
                return str(entity_id)
        return "<entityId-from-legalTargets-above>"

    if kind == "CHOOSE_TARGETS":
        selected: dict[str, list[str]] = {}
        for req in pd.get("targetRequirements") or [{"index": 0, "minTargets": 1}]:
            idx = req.get("index", 0)
            min_t = max(1, int(req.get("minTargets") or 1))
            selected[str(idx)] = [_first_target_id_for_slot(idx)] * min_t
        return _json.dumps(
            {"type": "TargetsResponse", "decisionId": decision_id, "selectedTargets": selected}
        )

    if kind == "DISTRIBUTE":
        total = int((pd.get("shape") or {}).get("totalToDistribute") or 1)
        return _json.dumps(
            {
                "type": "DistributionResponse",
                "decisionId": decision_id,
                "distribution": {_first_distribution_id(): total},
            }
        )

    if kind in {"SELECT_CARDS", "SEARCH_LIBRARY"}:
        shape = pd.get("shape") or {}
        n = max(1, int(shape.get("minSelections") or 1))
        return _json.dumps(
            {
                "type": "CardsSelectedResponse",
                "decisionId": decision_id,
                "selectedCards": _first_n_option_ids(n),
            }
        )

    if kind in {"ORDER_OBJECTS", "REORDER_LIBRARY"}:
        n = max(2, len(pd.get("options") or []))
        return _json.dumps(
            {
                "type": "OrderedResponse",
                "decisionId": decision_id,
                "orderedObjects": _first_n_option_ids(n),
            }
        )

    if kind == "CHOOSE_MODE":
        modes = [m.get("index", 0) for m in (pd.get("modes") or []) if m.get("available", True)]
        return _json.dumps(
            {
                "type": "ModesChosenResponse",
                "decisionId": decision_id,
                "selectedModes": modes[:1] or [0],
            }
        )

    if kind == "BUDGET_MODAL":
        return _json.dumps(
            {"type": "BudgetModalResponse", "decisionId": decision_id, "selectedModeIndices": [0]}
        )

    if kind == "ASSIGN_DAMAGE":
        return _json.dumps(
            {
                "type": "DamageAssignmentResponse",
                "decisionId": decision_id,
                "assignments": {_first_distribution_id(): 1},
            }
        )

    if kind == "SELECT_MANA_SOURCES":
        return _json.dumps(
            {"type": "ManaSourcesSelectedResponse", "decisionId": decision_id, "autoPay": True}
        )

    if kind == "SPLIT_PILES":
        ids = _first_n_option_ids(2)
        return _json.dumps(
            {
                "type": "PilesSplitResponse",
                "decisionId": decision_id,
                "piles": [[ids[0]], [ids[1]]],
            }
        )

    return _json.dumps({"type": "<ResponseType>", "decisionId": decision_id})


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
    if event_type == "BecomesTarget":
        return _format_becomes_target(event)
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


def _format_becomes_target(event: dict) -> str:
    text = event.get("text") or ""
    target = _raw_event_field(text, "targetName")
    source = _raw_event_field(text, "sourceName")
    if target and source:
        return f"{target} became target of {source}"
    if target:
        return f"{target} became a target"
    return ""


def _raw_event_field(text: str, field: str) -> str:
    m = re.search(rf"{re.escape(field)}=([^,)]+)", text)
    return m.group(1).strip() if m else ""


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


_COLOR_ABBREV = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
    "colorless": "C",
}
_LAND_TO_COLOR = {"PLAINS": "W", "ISLAND": "U", "SWAMP": "B", "MOUNTAIN": "R", "FOREST": "G"}


def _fmt_mana_pool(pool: dict) -> str:
    parts = []
    for color in ("white", "blue", "black", "red", "green", "colorless"):
        amount = pool.get(color, 0)
        if amount:
            parts.append("{" + _COLOR_ABBREV[color] + "}" * amount)
    return "".join(parts) if parts else "(empty)"


def _untapped_land_colors(obs: dict, player_id: str) -> dict[str, int]:
    """Return {color_abbrev: count} of the player's untapped lands by produced color.

    Best-effort: prefers the land's subtype (Plains/Island/Swamp/Mountain/Forest)
    over the oracle text. Non-basic lands without a recognized subtype fall back
    to scanning the oracle text for `{X}` tokens. Lands with no recognizable
    color contribution are surfaced under "?".
    """
    counts: dict[str, int] = {}
    for zone in obs.get("zones", []):
        if zone.get("ownerId") != player_id or zone.get("zoneType") != "Battlefield":
            continue
        for card in zone.get("cards", []):
            if "LAND" not in (t.upper() for t in card.get("types", [])):
                continue
            if card.get("tapped"):
                continue
            color = None
            for subtype in card.get("subtypes", []):
                color = _LAND_TO_COLOR.get(str(subtype).upper())
                if color:
                    break
            if color is None:
                otext = card.get("oracleText", "")
                for abbr in ("W", "U", "B", "R", "G", "C"):
                    if f"{{{abbr}}}" in otext:
                        color = abbr
                        break
            counts[color or "?"] = counts.get(color or "?", 0) + 1
    return counts


def _format_mana_position(obs: dict, acting_player_name: str) -> str:
    """Synthesize a 'mana available this priority' block.

    Combines the player's mana pool (already floating) with their untapped
    lands (potential, requires tapping) into one explicit summary so the
    LLM can compare costs directly against what they can actually spend.
    """
    me = next((p for p in obs.get("players", []) if p.get("name") == acting_player_name), None)
    if me is None:
        return "YOUR MANA: (unknown player)"
    pool = me.get("manaPool") or {}
    pool_str = _fmt_mana_pool(pool)
    untapped = _untapped_land_colors(obs, me["id"])
    if not untapped:
        return f"YOUR MANA: pool={pool_str}; no untapped lands"

    # Synthesize total spendable (pool + every untapped land tapped for one mana).
    total: dict[str, int] = {}
    for color in ("white", "blue", "black", "red", "green", "colorless"):
        n = pool.get(color, 0)
        if n:
            total[_COLOR_ABBREV[color]] = total.get(_COLOR_ABBREV[color], 0) + n
    for color, n in untapped.items():
        total[color] = total.get(color, 0) + n

    total_str = "".join(("{" + c + "}") * total[c] for c in sorted(total)) if total else "(none)"
    untapped_str = ", ".join(f"{n}×{{{c}}}" for c, n in sorted(untapped.items()))
    return (
        f"YOUR MANA: pool={pool_str}; untapped lands={untapped_str}; "
        f"max spendable this priority if you tap all={total_str}"
    )


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


def _targeting_note(
    action: dict,
    obs: dict | None,
    acting_player_name: str | None,
) -> str:
    if not obs or not acting_player_name:
        return ""
    target_ids = action.get("targetEntityIds") or []
    if not target_ids:
        return ""

    source_name = _source_card_from_action(action.get("description") or "")
    if not source_name:
        return ""
    source_card = oracle.card(source_name)
    if not source_card:
        return ""

    target_owners = _target_owners_by_entity(obs)
    own_targets = [
        entity_id for entity_id in target_ids if target_owners.get(entity_id) == acting_player_name
    ]
    opposing_targets = [
        entity_id
        for entity_id in target_ids
        if target_owners.get(entity_id) and target_owners.get(entity_id) != acting_player_name
    ]

    oracle_text = str(source_card.get("oracle_text") or "").lower()
    beneficial = _looks_beneficial_target_spell(oracle_text)
    harmful = _looks_harmful_target_spell(oracle_text)
    if beneficial and opposing_targets:
        return "WARNING: beneficial effect targets opponent-controlled object"
    if harmful and own_targets:
        return "WARNING: harmful effect targets your own object"
    return ""


def _source_card_from_action(description: str) -> str:
    m = re.match(r"Cast (.+?)(?: targeting\b| for\b|$)", description)
    return m.group(1).strip() if m else ""


def _target_owners_by_entity(obs: dict) -> dict[str, str]:
    player_names = {p.get("id"): p.get("name") for p in obs.get("players", [])}
    owners: dict[str, str] = {
        player_id: name for player_id, name in player_names.items() if player_id and name
    }
    for zone in obs.get("zones", []):
        zone_owner = player_names.get(zone.get("ownerId"), zone.get("ownerId"))
        for card in zone.get("cards") or []:
            entity_id = card.get("entityId")
            controller_id = card.get("controllerId") or card.get("ownerId") or zone.get("ownerId")
            controller = player_names.get(controller_id, zone_owner)
            if entity_id and controller:
                owners[entity_id] = controller
    for card in obs.get("stack") or []:
        entity_id = card.get("entityId")
        controller = player_names.get(card.get("controllerId"), card.get("controllerId"))
        if entity_id and controller:
            owners[entity_id] = controller
    return owners


def _looks_beneficial_target_spell(oracle_text: str) -> bool:
    if "target opponent" in oracle_text or "target player discards" in oracle_text:
        return False
    return bool(
        re.search(r"gets \+[0-9x]+/\+[0-9x]+", oracle_text)
        or "gains flying" in oracle_text
        or "return target creature card from your graveyard to your hand" in oracle_text
    )


def _looks_harmful_target_spell(oracle_text: str) -> bool:
    return any(
        phrase in oracle_text
        for phrase in (
            "destroy target",
            "deals damage to target",
            "put target creature on top",
            "return target creature to its owner's hand",
            "target player discards",
        )
    )
