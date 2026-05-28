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
    format_react_prompt,
    format_recent_public_actions,
    format_structured_decision,
    format_turn_plan,
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
            f"{format_turn_plan(self.toolbox.turn_plan)}\n\n"
            f"{format_observation(obs, self.name)}\n\n"
            f"{format_recent_public_actions(recent_public_actions or [], obs=obs)}\n\n"
            f"{format_combat_evaluator(obs, self.name, legal_actions)}\n\n"
            f"{format_mulligan_evaluator(obs, self.name, legal_actions)}\n\n"
            f"{format_legal_actions(legal_actions, obs, self.name)}\n\n"
            "If no turn plan is committed yet, set one before submit_action. "
            "If one is committed, execute the next step — keep voice tight "
            "unless something material changed (call update_turn_plan if it "
            "did). Chain tool calls in this single response."
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
                "monologues": list(self.toolbox.turn_monologues),
                "table_talk": list(self.toolbox.turn_table_talk),
            },
        )
        return action_id

    def react(
        self,
        obs: dict,
        trigger: str,
        engine_events: list[dict] | None,
        verbose: bool = False,
    ) -> None:
        """Run a narration-only LLM pass for draw / post-combat hooks.

        Emits MONOLOGUE / TABLE_TALK events through the standard tool
        dispatcher (per [[feedback-no-new-event-types]]) and a thin ACTION
        record tagged as a reaction so the public_action_history surfaces it
        to the opponent's next decision. No submit_action expected.
        """
        user_msg = format_react_prompt(
            obs, self.name, trigger, engine_events, self.toolbox.turn_plan
        )
        self.agent.run(user_msg, verbose=verbose, max_iterations=2, wait_for_commit=False)
        monologues = list(self.toolbox.turn_monologues)
        table_talk = list(self.toolbox.turn_table_talk)
        # Drain so the next real action's record doesn't double-attach them.
        self.toolbox.turn_monologues = []
        self.toolbox.turn_table_talk = []
        if monologues or table_talk:
            self.event_log.append(
                ACTION,
                {
                    "player": self.name,
                    "action_id": None,
                    "description": f"(reaction: {trigger})",
                    "reasoning": "",
                    "monologues": monologues,
                    "table_talk": table_talk,
                    "reaction_trigger": trigger,
                },
            )

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
            f"{format_recent_public_actions(recent_public_actions or [], obs=obs)}\n\n"
            f"{format_structured_decision(obs, self.name)}\n\n"
            "Stay in voice. A quick monologue() line is welcome if this "
            "decision matters; otherwise just construct the DecisionResponse "
            "JSON and call submit_decision."
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

    if kind == "CHOOSE_ATTACKERS":
        # Default policy: attack with all valid attackers into the first valid
        # defender. Mirrors the legacy "Attack with all" gym-kludge fallback so
        # tests/random-agent self-play still produce forward combat progress.
        # Trainers that want to skip combat can scripted-override this.
        legal_targets = pd.get("legalTargets") or {}
        defenders = legal_targets.get("0") or legal_targets.get(0) or []
        defender_id = defenders[0].get("entityId") if defenders else None
        attackers = {}
        if defender_id is not None:
            for opt in pd.get("options") or []:
                aid = opt.get("entityId")
                if aid is not None:
                    attackers[aid] = defender_id
        return {
            "type": "AttackersChosenResponse",
            "decisionId": decision_id,
            "attackers": attackers,
        }

    if kind == "CHOOSE_BLOCKERS":
        # Default policy: greedy 1:1 — pair each blocker with its first legal
        # attacker, one each. Mirrors the legacy "Block as many as possible"
        # entry the gym kludge surfaced, so default-policy games still exchange
        # blocker↔attacker damage instead of silently skipping all blocks.
        # Scripted overrides can supply a different blocker map.
        legal_targets = pd.get("legalTargets") or {}
        options = pd.get("options") or []
        blockers: dict[str, list[str]] = {}
        used_attackers: set[str] = set()
        for idx, opt in enumerate(options):
            blocker_id = opt.get("entityId")
            if blocker_id is None:
                continue
            attackers_for = legal_targets.get(str(idx)) or legal_targets.get(idx) or []
            for atk in attackers_for:
                atk_id = atk.get("entityId")
                if atk_id is None or atk_id in used_attackers:
                    continue
                blockers[blocker_id] = [atk_id]
                used_attackers.add(atk_id)
                break
        return {
            "type": "BlockersChosenResponse",
            "decisionId": decision_id,
            "blockers": blockers,
        }

    return {"type": "CancelDecisionResponse", "decisionId": decision_id}
