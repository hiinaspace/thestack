"""Lightweight legality checks for the curated Starter Kit card pool."""

from __future__ import annotations

import re

from game.state import CardDef, CardInstance, GameState, PlayerState


def parse_mana_cost(cost_str: str) -> dict[str, int]:
    """
    Parse '{2}{W}{G}' -> {'generic': 2, 'W': 1, 'G': 1}.
    Returns empty dict for lands or free spells.
    """
    if not cost_str:
        return {}
    result: dict[str, int] = {}
    for token in re.findall(r"\{([^}]+)\}", cost_str):
        if token.isdigit():
            result["generic"] = result.get("generic", 0) + int(token)
        elif token in "WUBRGC":
            result[token] = result.get(token, 0) + 1
        elif token == "X":
            result["X"] = result.get("X", 0) + 1
        # Ignore hybrid mana etc for MVP — treat as generic
    return result


def mana_available(player: PlayerState) -> dict[str, int]:
    """
    Sum up mana from untapped lands (doesn't tap them — just checks totals).
    Also includes any mana already in the pool.
    """
    totals: dict[str, int] = dict(player.mana_pool)
    for card in player.battlefield:
        if card.definition.is_land() and not card.tapped:
            color = _land_color(card.definition)
            if color:
                totals[color] = totals.get(color, 0) + 1
    return totals


def _land_color(defn: CardDef) -> str | None:
    text = defn.oracle_text
    for symbol, color in [
        ("{W}", "W"),
        ("{U}", "U"),
        ("{B}", "B"),
        ("{R}", "R"),
        ("{G}", "G"),
        ("{C}", "C"),
    ]:
        if symbol in text:
            return color
    # Fall back to type line
    type_lower = defn.type_line.lower()
    if "forest" in type_lower:
        return "G"
    if "plains" in type_lower:
        return "W"
    if "island" in type_lower:
        return "U"
    if "swamp" in type_lower:
        return "B"
    if "mountain" in type_lower:
        return "R"
    return None


def can_pay_cost(cost: dict[str, int], available: dict[str, int]) -> bool:
    """
    Check if the available mana can pay the cost.
    Generic mana can be paid by any color.
    """
    remaining_available = dict(available)

    # Pay specific colors first
    for color in "WUBRGC":
        needed = cost.get(color, 0)
        have = remaining_available.get(color, 0)
        if have < needed:
            return False
        remaining_available[color] = have - needed

    # Pay generic with whatever's left
    generic_needed = cost.get("generic", 0)
    total_left = sum(remaining_available.values())
    return total_left >= generic_needed


def can_play_land(state: GameState, player_name: str) -> tuple[bool, str]:
    p = state.players[player_name]
    if p.lands_played_this_turn > 0:
        return False, "already played a land this turn"
    if state.active_player != player_name:
        return False, "not your turn"
    if state.phase not in ("main1", "main2"):
        return False, f"can't play lands during {state.phase}"
    return True, "ok"


def can_cast_spell(state: GameState, player_name: str, card: CardInstance) -> tuple[bool, str]:
    p = state.players[player_name]
    defn = card.definition

    # Timing: sorceries only on your turn during main phase with empty stack
    if defn.is_sorcery() or defn.is_creature() or defn.is_enchantment() or defn.is_artifact():
        if state.active_player != player_name:
            return False, "sorcery-speed cards can only be played on your turn"
        if state.phase not in ("main1", "main2"):
            return False, f"sorcery-speed cards can't be cast during {state.phase}"
        if state.stack:
            return False, "can't cast sorcery-speed spells with items on the stack"

    # Mana check
    cost = parse_mana_cost(defn.mana_cost)
    available = mana_available(p)
    if not can_pay_cost(cost, available):
        colors_needed = {k: v for k, v in cost.items() if v > 0}
        return False, f"not enough mana (need {colors_needed}, have {available})"

    return True, "ok"


def can_attack(card: CardInstance, state: GameState, player_name: str) -> tuple[bool, str]:
    if state.active_player != player_name:
        return False, "not your turn"
    if state.phase != "combat":
        return False, "not in combat phase"
    if not card.definition.is_creature():
        return False, "not a creature"
    if card.tapped:
        return False, "creature is tapped"
    if card.entered_this_turn and "Haste" not in card.definition.keywords:
        return False, "creature has summoning sickness"
    return True, "ok"


def can_block(blocker: CardInstance, attacker: CardInstance) -> tuple[bool, str]:
    if not blocker.definition.is_creature():
        return False, "not a creature"
    if blocker.tapped:
        return False, "blocker is tapped"
    if (
        "Flying" in attacker.definition.keywords
        and "Flying" not in blocker.definition.keywords
        and "Reach" not in blocker.definition.keywords
    ):
        return False, "can't block flying creature without flying or reach"
    return True, "ok"


def has_instant_options(player: PlayerState, state: GameState, player_name: str) -> bool:
    """Return True if the player has any castable instants in hand."""
    for card in player.hand:
        if card.definition.is_instant():
            ok, _ = can_cast_spell(state, player_name, card)
            if ok:
                return True
    return False


def tap_lands_for_mana(
    player: PlayerState, amount_needed: int, preferred_colors: list[str] | None = None
) -> dict[str, int]:
    """
    Greedily tap untapped lands to produce mana.
    Returns the mana produced and modifies player.battlefield in place (tapping lands).
    Used by the harness when the LLM doesn't specify which lands to tap.
    """
    tapped_mana: dict[str, int] = {}
    total_tapped = 0

    # Tap lands of preferred colors first
    order = (preferred_colors or []) + [c for c in "WUBRGC" if c not in (preferred_colors or [])]

    lands_by_color: dict[str, list[CardInstance]] = {}
    for card in player.battlefield:
        if card.definition.is_land() and not card.tapped:
            color = _land_color(card.definition)
            if color:
                lands_by_color.setdefault(color, []).append(card)

    for color in order:
        if total_tapped >= amount_needed:
            break
        for land in lands_by_color.get(color, []):
            if total_tapped >= amount_needed:
                break
            land.tapped = True
            tapped_mana[color] = tapped_mana.get(color, 0) + 1
            total_tapped += 1

    player.mana_pool.update(tapped_mana)
    return tapped_mana


def spend_mana_for_cost(player: PlayerState, cost: dict[str, int]) -> bool:
    """
    Tap enough lands and deduct from mana pool to pay cost.
    Modifies player state in place.
    Returns False if unable to pay.
    """
    available = mana_available(player)
    if not can_pay_cost(cost, available):
        return False

    # First tap lands as needed
    colors_needed: list[str] = []
    for color in "WUBRGC":
        for _ in range(cost.get(color, 0)):
            colors_needed.append(color)

    total_needed = sum(cost.values())
    tap_lands_for_mana(player, total_needed, colors_needed)

    # Deduct from pool
    pool = player.mana_pool
    for color in "WUBRGC":
        needed = cost.get(color, 0)
        if needed > 0:
            pool[color] = pool.get(color, 0) - needed

    # Deduct generic from whatever's left
    generic = cost.get("generic", 0)
    for color in "WUBRGCX":
        if generic <= 0:
            break
        have = pool.get(color, 0)
        if have > 0:
            deduct = min(have, generic)
            pool[color] -= deduct
            generic -= deduct

    # Clean up zeros
    player.mana_pool = {k: v for k, v in pool.items() if v > 0}
    return True
