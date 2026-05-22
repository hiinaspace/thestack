"""Canonical game state — zones, life totals, turn structure."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class CardDef:
    """Static card definition loaded from starter_kit.json."""

    name: str
    scryfall_id: str
    mana_cost: str
    cmc: float
    type_line: str
    oracle_text: str
    power: str | None
    toughness: str | None
    keywords: list[str]
    colors: list[str]

    def is_land(self) -> bool:
        return "Land" in self.type_line

    def is_creature(self) -> bool:
        return "Creature" in self.type_line

    def is_instant(self) -> bool:
        return "Instant" in self.type_line

    def is_sorcery(self) -> bool:
        return "Sorcery" in self.type_line

    def is_enchantment(self) -> bool:
        return "Enchantment" in self.type_line

    def is_artifact(self) -> bool:
        return "Artifact" in self.type_line

    def color_identity(self) -> set[str]:
        """Extract colors from mana cost string like '{2}{W}{G}'."""
        import re

        symbols = re.findall(r"\{([A-Z])\}", self.mana_cost)
        return {s for s in symbols if s in "WUBRGC"}


@dataclass
class CardInstance:
    """A specific copy of a card on the battlefield or in a zone."""

    instance_id: str
    definition: CardDef
    tapped: bool = False
    entered_this_turn: bool = False  # summoning sickness
    counters: dict[str, int] = field(default_factory=dict)
    # Runtime power/toughness (can be modified by effects)
    power_mod: int = 0
    toughness_mod: int = 0
    damage_marked: int = 0

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def effective_power(self) -> int:
        base = (
            int(self.definition.power)
            if self.definition.power and self.definition.power != "*"
            else 0
        )
        return base + self.power_mod + self.counters.get("+1/+1", 0) - self.counters.get("-1/-1", 0)

    @property
    def effective_toughness(self) -> int:
        base = (
            int(self.definition.toughness)
            if self.definition.toughness and self.definition.toughness != "*"
            else 0
        )
        return (
            base
            + self.toughness_mod
            + self.counters.get("+1/+1", 0)
            - self.counters.get("-1/-1", 0)
        )

    @property
    def is_dead(self) -> bool:
        return self.damage_marked >= self.effective_toughness and self.effective_toughness > 0

    def can_attack(self) -> bool:
        return (
            self.definition.is_creature()
            and not self.tapped
            and not self.entered_this_turn
            and "Defender" not in self.definition.keywords
        )

    def can_block(self) -> bool:
        return self.definition.is_creature() and not self.tapped

    @classmethod
    def from_def(cls, card_def: CardDef) -> CardInstance:
        return cls(instance_id=str(uuid.uuid4())[:8], definition=card_def)


@dataclass
class PlayerState:
    name: str
    library: list[CardInstance]
    hand: list[CardInstance] = field(default_factory=list)
    battlefield: list[CardInstance] = field(default_factory=list)
    graveyard: list[CardInstance] = field(default_factory=list)
    exile: list[CardInstance] = field(default_factory=list)
    life: int = 20
    mana_pool: dict[str, int] = field(default_factory=dict)
    lands_played_this_turn: int = 0

    def available_mana(self) -> int:
        return sum(self.mana_pool.values())

    def untapped_lands(self) -> list[CardInstance]:
        return [c for c in self.battlefield if c.definition.is_land() and not c.tapped]

    def creatures(self) -> list[CardInstance]:
        return [c for c in self.battlefield if c.definition.is_creature()]

    def hand_card_by_name(self, name: str) -> CardInstance | None:
        for c in self.hand:
            if c.name.lower() == name.lower():
                return c
        return None

    def battlefield_card_by_name(self, name: str) -> CardInstance | None:
        for c in self.battlefield:
            if c.name.lower() == name.lower():
                return c
        return None

    def to_public_dict(self) -> dict:
        """Visible to both players."""
        return {
            "name": self.name,
            "life": self.life,
            "hand_size": len(self.hand),
            "library_size": len(self.library),
            "battlefield": [_card_to_dict(c) for c in self.battlefield],
            "graveyard": [c.name for c in self.graveyard],
            "exile": [c.name for c in self.exile],
            "mana_pool": dict(self.mana_pool),
        }

    def to_private_dict(self) -> dict:
        """Own player's view — includes hand."""
        d = self.to_public_dict()
        d["hand"] = [_card_to_dict(c) for c in self.hand]
        return d


def _card_to_dict(c: CardInstance) -> dict:
    d: dict = {
        "id": c.instance_id,
        "name": c.name,
        "type": c.definition.type_line,
        "tapped": c.tapped,
    }
    if c.definition.is_creature():
        d["power"] = c.effective_power
        d["toughness"] = c.effective_toughness
        d["damage"] = c.damage_marked
        d["summoning_sick"] = c.entered_this_turn
    if c.counters:
        d["counters"] = c.counters
    return d


@dataclass
class StackItem:
    card_instance: CardInstance
    controller: str
    targets: list[str]  # card instance_ids or player names


@dataclass
class GameState:
    game_id: str
    players: dict[str, PlayerState]
    player_order: list[str]  # index 0 goes first
    turn: int = 0
    active_player: str = ""
    phase: str = "untap"
    # combat state
    stack: list[StackItem] = field(default_factory=list)
    declared_attackers: list[str] = field(default_factory=list)  # instance_ids
    declared_blockers: dict[str, list[str]] = field(
        default_factory=dict
    )  # attacker_id -> [blocker_ids]
    game_over: bool = False
    winner: str | None = None

    def opponent_of(self, player_name: str) -> str:
        for p in self.player_order:
            if p != player_name:
                return p
        raise ValueError(f"No opponent for {player_name}")

    def active_player_state(self) -> PlayerState:
        return self.players[self.active_player]

    def to_public_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "turn": self.turn,
            "active_player": self.active_player,
            "phase": self.phase,
            "players": {name: p.to_public_dict() for name, p in self.players.items()},
            "stack": [
                {"card": s.card_instance.name, "controller": s.controller, "targets": s.targets}
                for s in self.stack
            ],
            "attackers": self.declared_attackers,
            "blockers": self.declared_blockers,
            "game_over": self.game_over,
            "winner": self.winner,
        }
