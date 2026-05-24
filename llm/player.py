"""Player agent — picks from Argentum's legal-action list via tool calls.

Holds one persistent Ollama conversation across every decision in a game.
The model's scratchpad (take_note) is preserved between decisions; reasoning
is captured both as a free-text REASONING event (final assistant message) and
as the structured `reasoning` argument the model passes to submit_action.
"""

from __future__ import annotations

import json
import re

import ollama

from game.events import ACTION, EventLog
from llm import oracle
from llm.agent import Agent
from llm.persona import Persona
from llm.prompts import (
    build_player_system_prompt,
    format_combat_evaluator,
    format_legal_actions,
    format_mulligan_evaluator,
    format_observation,
    format_recent_public_actions,
    format_structured_decision,
)
from llm.tools import Toolbox


class PlayerAgent:
    def __init__(
        self,
        persona: Persona,
        opponent_name: str,
        model: str,
        client: ollama.Client,
        event_log: EventLog,
        deck: dict[str, int] | None = None,
    ) -> None:
        self.persona = persona
        self.name = persona.name
        self.event_log = event_log
        self.toolbox = Toolbox(name=persona.name)
        self.last_reasoning: str = ""
        self.agent = Agent(
            name=persona.name,
            model=model,
            client=client,
            event_log=event_log,
            system_prompt=build_player_system_prompt(
                persona.name,
                opponent_name,
                identity=persona.identity,
                decklist=oracle.deck_listing(deck or {}),
                strategy=persona.strategy,
                opponent_notes=persona.opponent_entry(opponent_name),
                recent_memory=persona.recent_memory(),
            ),
            toolbox=self.toolbox,
        )

    def choose_action(
        self,
        obs: dict,
        verbose: bool = False,
        recent_public_actions: list[dict] | None = None,
    ) -> int:
        legal_actions = obs.get("legalActions", [])
        if not legal_actions:
            return 0

        valid_ids = {a["actionId"] for a in legal_actions}
        pass_id = _find_pass_id(legal_actions)
        self.toolbox.reset_turn(valid_ids)

        user_msg = (
            f"{format_observation(obs, self.name)}\n\n"
            f"{format_recent_public_actions(recent_public_actions or [])}\n\n"
            f"{format_combat_evaluator(obs, self.name, legal_actions)}\n\n"
            f"{format_mulligan_evaluator(obs, self.name, legal_actions)}\n\n"
            f"{format_legal_actions(legal_actions, obs, self.name)}\n\n"
            "Choose one of the numbered legal actions and call submit_action."
        )

        response = self.agent.run(user_msg, verbose=verbose)
        self.last_reasoning = response.reasoning

        action_id = response.action_id
        if action_id is None:
            action_id = _parse_action_id_fallback(response.content, valid_ids)
        if action_id is None or action_id not in valid_ids:
            if verbose:
                print(f"  [{self.name}] no valid action chosen, defaulting to pass")
            action_id = pass_id

        chosen = next((a for a in legal_actions if a["actionId"] == action_id), None)
        self.event_log.append(
            ACTION,
            {
                "player": self.name,
                "action_id": action_id,
                "description": chosen.get("description") if chosen else None,
                "reasoning": response.reasoning,
            },
        )
        return action_id

    def choose_decision(
        self,
        obs: dict,
        verbose: bool = False,
        recent_public_actions: list[dict] | None = None,
    ) -> dict:
        pending = obs.get("pendingDecision") or {}
        decision_id = pending.get("decisionId")
        self.toolbox.reset_turn(set(), valid_decision_id=decision_id)

        user_msg = (
            f"{format_observation(obs, self.name)}\n\n"
            f"{format_recent_public_actions(recent_public_actions or [])}\n\n"
            f"{format_structured_decision(obs, self.name)}\n\n"
            "Construct the DecisionResponse JSON and call submit_decision."
        )

        response = self.agent.run(user_msg, verbose=verbose)
        self.last_reasoning = response.reasoning

        decision_response = response.decision_response
        if decision_response is None:
            decision_response = _parse_decision_response_fallback(response.content, decision_id)
        if not _valid_decision_response(decision_response, decision_id):
            if verbose:
                print(f"  [{self.name}] no valid structured decision, using default")
            decision_response = _default_decision_response(obs)

        self.event_log.append(
            ACTION,
            {
                "player": self.name,
                "action_id": None,
                "description": f"{pending.get('kind', 'DECISION')}: {pending.get('prompt', '')}",
                "reasoning": response.reasoning,
                "decision_response": decision_response,
            },
        )
        return decision_response


def _find_pass_id(legal_actions: list[dict]) -> int:
    return next(
        (a["actionId"] for a in legal_actions if "pass" in a.get("description", "").lower()),
        legal_actions[0]["actionId"],
    )


def _parse_action_id_fallback(text: str, valid_ids: set[int]) -> int | None:
    """If the model wrote a number instead of calling submit_action, salvage it."""
    for m in re.finditer(r"\b(\d+)\b", text or ""):
        candidate = int(m.group(1))
        if candidate in valid_ids:
            return candidate
    return None


def _parse_decision_response_fallback(text: str, decision_id: str | None) -> dict | None:
    """If the model printed JSON instead of calling submit_decision, salvage it."""
    if not text:
        return None
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if _valid_decision_response(candidate, decision_id):
            return candidate
    return None


def _valid_decision_response(response: object, decision_id: str | None) -> bool:
    if not isinstance(response, dict):
        return False
    if not response.get("type") or not response.get("decisionId"):
        return False
    return decision_id is None or response.get("decisionId") == decision_id


def _default_decision_response(obs: dict) -> dict:
    """Last-ditch legal-ish response for structured decisions."""
    pd = obs.get("pendingDecision") or {}
    decision_id = pd.get("decisionId", "")
    kind = pd.get("kind")
    shape = pd.get("shape") or {}

    if kind == "CHOOSE_TARGETS":
        selected = {}
        legal_targets = pd.get("legalTargets") or {}
        for req in pd.get("targetRequirements") or []:
            idx = str(req.get("index"))
            targets = legal_targets.get(idx, legal_targets.get(req.get("index"), []))
            count = int(req.get("minTargets") or 0)
            selected[idx] = [t.get("entityId") for t in targets[:count]]
        return {"type": "TargetsResponse", "decisionId": decision_id, "selectedTargets": selected}

    if kind in {"SELECT_CARDS", "SEARCH_LIBRARY"}:
        count = int(shape.get("minSelections") or 0)
        cards = [o.get("entityId") for o in (pd.get("options") or [])[:count]]
        return {"type": "CardsSelectedResponse", "decisionId": decision_id, "selectedCards": cards}

    if kind == "DISTRIBUTE":
        total = int(shape.get("totalToDistribute") or 0)
        distribution = {}
        remaining = total
        for item in pd.get("distributionTargets") or []:
            if remaining <= 0:
                break
            target = (item.get("target") or {}).get("entityId")
            if not target:
                continue
            min_amount = int(item.get("min") or 0)
            max_amount = item.get("max")
            amount = remaining
            if max_amount is not None:
                amount = min(amount, int(max_amount))
            if 0 < amount < min_amount:
                amount = min_amount if min_amount <= remaining else 0
            if amount > 0:
                distribution[target] = amount
                remaining -= amount
        return {
            "type": "DistributionResponse",
            "decisionId": decision_id,
            "distribution": distribution,
        }

    if kind in {"ORDER_OBJECTS", "REORDER_LIBRARY"}:
        ordered = [o.get("entityId") for o in pd.get("options") or []]
        return {"type": "OrderedResponse", "decisionId": decision_id, "orderedObjects": ordered}

    if kind == "CHOOSE_MODE":
        count = int(shape.get("minSelections") or 1)
        modes = [m.get("index") for m in pd.get("modes") or [] if m.get("available", True)]
        return {
            "type": "ModesChosenResponse",
            "decisionId": decision_id,
            "selectedModes": modes[:count],
        }

    if kind == "SELECT_MANA_SOURCES":
        return {"type": "ManaSourcesSelectedResponse", "decisionId": decision_id, "autoPay": True}

    if kind == "BUDGET_MODAL":
        return {"type": "BudgetModalResponse", "decisionId": decision_id, "selectedModeIndices": []}

    return {"type": "CancelDecisionResponse", "decisionId": decision_id}
