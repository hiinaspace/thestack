"""ScriptedAgent — duck-types PlayerAgent / RandomAgent without an LLM.

Drives ``run_game.run_game()`` with pre-scripted action / decision / react
responses keyed by ``(turn, phase_step, decision_kind, react_trigger)``.
Records the obs + the prompt that WOULD have gone to the LLM + the scripted
response on every call, so the same scenarios can be reused as a DSPy
optimization corpus later.

Surfaces unmatched calls loudly: falls back to first non-pass legal action
and appends an entry to ``self.gaps`` so the test can ``assert not gaps``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from game.events import ACTION, EventLog
from llm.player import _default_decision_response
from llm.prompts import (
    format_legal_actions,
    format_observation,
    format_react_prompt,
    format_recent_public_actions,
    format_structured_decision,
    format_turn_plan,
)
from llm.tools import Toolbox

# ---- Directive shapes -------------------------------------------------------


@dataclass
class ActionScript:
    """Match an upcoming choose_action call; pick one legal action.

    Match keys (None = wildcard):
        turn: int — exact turn number
        phase_step: str — uppercase step ("PRECOMBAT_MAIN", "DECLARE_BLOCKERS"...).
            Matched as substring against ``f"{phase}/{step}"`` upper-cased so either
            "PRECOMBAT_MAIN" or "BEGINNING/UNTAP" works.
        when: callable(obs) -> bool — extra predicate.

    Selector (exactly one of):
        action_id: int — literal action id.
        description_contains: str — substring (case-insensitive) match on the
            action's description.
        pick: callable(obs, legal_actions) -> int — full custom selector.

    Voice:
        monologue, table_talk: optional one-liners pushed into the toolbox so
            they end up on the action record like a real PlayerAgent.
        reasoning: short label for the action record (mirrors PlayerAgent's
            ``last_reasoning``).
    """

    turn: int | None = None
    phase_step: str | None = None
    when: Callable[[dict], bool] | None = None
    action_id: int | None = None
    description_contains: str | None = None
    pick: Callable[[dict, list[dict]], int] | None = None
    monologue: str | None = None
    table_talk: str | None = None
    reasoning: str = "[scripted]"


@dataclass
class DecisionScript:
    """Match an upcoming choose_decision call; return a structured response."""

    turn: int | None = None
    phase_step: str | None = None
    kind: str | None = None  # e.g. "CHOOSE_TARGETS", "SEARCH_LIBRARY"
    source_contains: str | None = None  # substring against pending_decision.sourceName
    when: Callable[[dict], bool] | None = None
    response: dict | Callable[[dict], dict] | None = None  # None = use default
    monologue: str | None = None
    table_talk: str | None = None
    reasoning: str = "[scripted decision]"


@dataclass
class ReactScript:
    """Match an upcoming react call (draw / combat_resolution); emit voice."""

    turn: int | None = None
    trigger: str | None = None  # "draw" or "combat_resolution"
    when: Callable[[dict], bool] | None = None
    monologues: list[str] = field(default_factory=list)
    table_talk: list[str] = field(default_factory=list)


Directive = ActionScript | DecisionScript | ReactScript


# ---- Call records (DSPy-ready) ---------------------------------------------


@dataclass
class CallRecord:
    """One LLM-call moment that the ScriptedAgent intercepted.

    The (obs, prompt, response) triple is shaped to be reusable as a DSPy
    labeled example: ``response`` is the gold output for the given prompt.
    """

    call_type: str  # "action" | "decision" | "react"
    turn: int
    phase_step: str
    obs: dict
    prompt: str  # what a real LLM would have seen for this call
    response: int | dict | None  # action_id / DecisionResponse / None for react
    matched_directive_index: int | None  # None = fell back to default
    reasoning: str
    extra: dict = field(default_factory=dict)  # decision_kind, trigger, etc.


# ---- Agent ------------------------------------------------------------------


class ScriptedAgent:
    """Pre-scripted, no-LLM agent. Mirrors PlayerAgent's surface.

    Construct with a list of ``Directive`` (Action / Decision / React).
    Calls match in order; first matching directive is consumed. If a call
    has no match, falls back to a deterministic default (first non-pass
    legal action / engine-default decision response) AND records the gap
    in ``self.gaps`` so the test can fail loudly on coverage holes.
    """

    def __init__(
        self,
        name: str,
        event_log: EventLog | None = None,
        script: list[Directive] | None = None,
        *,
        opponent_name: str | None = None,
        default_action_pick: Callable[[dict, list[dict]], int] | None = None,
    ) -> None:
        self.name = name
        self.opponent_name = opponent_name or "opponent"
        # Late-bindable: tests/scripted/runner.py constructs the agent BEFORE
        # run_game opens its EventLog, then run_game calls bind_event_log on
        # any agents_override that exposes the method.
        self.event_log: EventLog | None = event_log
        self.toolbox = Toolbox(name=name)
        self.last_reasoning: str = "[scripted]"
        self._remaining: list[tuple[int, Directive]] = list(enumerate(script or []))
        self.calls: list[CallRecord] = []
        self.gaps: list[CallRecord] = []  # subset of self.calls where no directive matched
        # Policy used when no directive matches an action call. Default:
        # first non-pass legal action. Tests can supply a different policy
        # (e.g. ``policies.aggressive`` to make blocking/attacking happen
        # without listing one ActionScript per combat step).
        self._default_pick = default_action_pick or _first_non_pass_or_pass

    def bind_event_log(self, event_log: EventLog) -> None:
        """Called by run_game after it constructs the EventLog so the agent
        appends ACTION rows to the same file run_game writes its
        OBSERVATION/ENGINE_EVENT/AUTOPASS rows into."""
        self.event_log = event_log

    # -- public surface mirroring PlayerAgent --------------------------------

    def choose_action(self, obs: dict, verbose: bool = False) -> int:
        legal_actions = obs.get("legalActions", [])
        if not legal_actions:
            return 0
        valid_ids = {a["actionId"] for a in legal_actions}
        self.toolbox.reset_turn(valid_ids)

        prompt = _render_action_prompt(obs, self.name)
        directive_idx, directive = self._match("action", obs)

        action_id: int | None = None
        reasoning = "[scripted/fallback]"
        if isinstance(directive, ActionScript):
            action_id = _pick_action_from_directive(directive, obs, legal_actions)
            reasoning = directive.reasoning
            if directive.monologue:
                self.toolbox.monologue(directive.monologue)
            if directive.table_talk:
                self.toolbox.table_talk(directive.table_talk)

        record = CallRecord(
            call_type="action",
            turn=int(obs.get("turnNumber", 0)),
            phase_step=_phase_step(obs),
            obs=obs,
            prompt=prompt,
            response=action_id,
            matched_directive_index=directive_idx,
            reasoning=reasoning,
        )
        if action_id is None or action_id not in valid_ids:
            action_id = self._default_pick(obs, legal_actions)
            record.response = action_id
            record.reasoning = "[scripted/fallback]"
            self.gaps.append(record)

        self.calls.append(record)
        self.last_reasoning = reasoning
        chosen = next((a for a in legal_actions if a["actionId"] == action_id), None)
        if self.event_log is not None:
            self.event_log.append(
                ACTION,
                {
                    "player": self.name,
                    "action_id": action_id,
                    "description": chosen.get("description") if chosen else None,
                    "reasoning": reasoning,
                    "monologues": list(self.toolbox.turn_monologues),
                    "table_talk": list(self.toolbox.turn_table_talk),
                },
            )
        return action_id

    def choose_decision(self, obs: dict, verbose: bool = False) -> dict:
        pending = obs.get("pendingDecision") or {}
        decision_id = pending.get("decisionId")
        self.toolbox.reset_turn(set(), valid_decision_id=decision_id)

        prompt = _render_decision_prompt(obs, self.name)
        directive_idx, directive = self._match("decision", obs)

        reasoning = "[scripted/default]"
        response: dict | None = None
        if isinstance(directive, DecisionScript):
            response = _resolve_decision_response(directive, obs)
            reasoning = directive.reasoning
            if directive.monologue:
                self.toolbox.monologue(directive.monologue)
            if directive.table_talk:
                self.toolbox.table_talk(directive.table_talk)

        record = CallRecord(
            call_type="decision",
            turn=int(obs.get("turnNumber", 0)),
            phase_step=_phase_step(obs),
            obs=obs,
            prompt=prompt,
            response=response,
            matched_directive_index=directive_idx,
            reasoning=reasoning,
            extra={"kind": pending.get("kind")},
        )

        if response is None or not isinstance(response, dict):
            response = _default_decision_response(obs)
            record.response = response
            record.reasoning = "[scripted/default]"
            self.gaps.append(record)

        # Ensure decisionId is correct even when the script supplied a partial dict.
        if "decisionId" not in response and decision_id is not None:
            response = {**response, "decisionId": decision_id}
        self.calls.append(record)
        self.last_reasoning = reasoning
        return response

    def react(
        self,
        obs: dict,
        trigger: str,
        engine_events: list[dict] | None,
        verbose: bool = False,
    ) -> None:
        prompt = format_react_prompt(obs, self.name, trigger, engine_events, self.toolbox.turn_plan)
        directive_idx, directive = self._match("react", obs, trigger=trigger)

        monologues: list[str] = []
        table_talk: list[str] = []
        if isinstance(directive, ReactScript):
            monologues = list(directive.monologues)
            table_talk = list(directive.table_talk)
        # No fallback voice for react — silence is fine.

        for m in monologues:
            self.toolbox.monologue(m)
        for t in table_talk:
            self.toolbox.table_talk(t)
        recorded_monos = list(self.toolbox.turn_monologues)
        recorded_tt = list(self.toolbox.turn_table_talk)
        # Drain so the next real action's record doesn't double-attach.
        self.toolbox.turn_monologues = []
        self.toolbox.turn_table_talk = []
        if (recorded_monos or recorded_tt) and self.event_log is not None:
            self.event_log.append(
                ACTION,
                {
                    "player": self.name,
                    "action_id": None,
                    "description": f"(reaction: {trigger})",
                    "reasoning": "",
                    "monologues": recorded_monos,
                    "table_talk": recorded_tt,
                    "reaction_trigger": trigger,
                },
            )

        self.calls.append(
            CallRecord(
                call_type="react",
                turn=int(obs.get("turnNumber", 0)),
                phase_step=_phase_step(obs),
                obs=obs,
                prompt=prompt,
                response=None,
                matched_directive_index=directive_idx,
                reasoning="[scripted react]",
                extra={"trigger": trigger},
            )
        )

    def close(self) -> None:
        pass

    # -- internals -----------------------------------------------------------

    def _match(
        self,
        call_type: str,
        obs: dict,
        *,
        trigger: str | None = None,
    ) -> tuple[int | None, Directive | None]:
        """Find + consume the first matching directive of the requested kind."""
        for i, (orig_idx, d) in enumerate(self._remaining):
            if not _kind_matches(d, call_type):
                continue
            if not _keys_match(d, obs, trigger=trigger):
                continue
            self._remaining.pop(i)
            return orig_idx, d
        return None, None


# ---- Matching helpers -------------------------------------------------------


def _kind_matches(d: Directive, call_type: str) -> bool:
    if call_type == "action":
        return isinstance(d, ActionScript)
    if call_type == "decision":
        return isinstance(d, DecisionScript)
    if call_type == "react":
        return isinstance(d, ReactScript)
    return False


def _keys_match(d: Directive, obs: dict, *, trigger: str | None = None) -> bool:
    turn = obs.get("turnNumber")
    phase_step = _phase_step(obs)
    if d.turn is not None and d.turn != turn:
        return False
    if getattr(d, "phase_step", None) and d.phase_step.upper() not in phase_step.upper():
        return False
    if isinstance(d, DecisionScript):
        pending = obs.get("pendingDecision") or {}
        if d.kind and (pending.get("kind") or "").upper() != d.kind.upper():
            return False
        if d.source_contains:
            src = (pending.get("sourceName") or "").lower()
            if d.source_contains.lower() not in src:
                return False
    if isinstance(d, ReactScript) and d.trigger and d.trigger != trigger:
        return False
    return not (d.when is not None and not d.when(obs))


def _pick_action_from_directive(
    d: ActionScript,
    obs: dict,
    legal_actions: list[dict],
) -> int | None:
    if d.pick is not None:
        try:
            return int(d.pick(obs, legal_actions))
        except (TypeError, ValueError):
            return None
    if d.action_id is not None:
        return int(d.action_id) if d.action_id in {a["actionId"] for a in legal_actions} else None
    if d.description_contains:
        needle = d.description_contains.lower()
        for a in legal_actions:
            if needle in (a.get("description") or "").lower():
                return int(a["actionId"])
        return None
    return None


def _resolve_decision_response(d: DecisionScript, obs: dict) -> dict | None:
    if d.response is None:
        return None
    if callable(d.response):
        result = d.response(obs)
        return result if isinstance(result, dict) else None
    return d.response


def _first_non_pass_or_pass(_obs: dict, legal_actions: list[dict]) -> int:
    for a in legal_actions:
        if "pass" not in (a.get("description") or "").lower():
            return int(a["actionId"])
    return int(legal_actions[0]["actionId"])


def _phase_step(obs: dict) -> str:
    phase = obs.get("phase", "?")
    step = obs.get("step", "?")
    return f"{phase}/{step}"


def _render_action_prompt(obs: dict, name: str) -> str:
    """Format the prompt a PlayerAgent WOULD have sent — recorded for DSPy reuse."""
    legal_actions = obs.get("legalActions", [])
    return (
        f"{format_turn_plan(None)}\n\n"
        f"{format_observation(obs, name)}\n\n"
        f"{format_recent_public_actions([], obs=obs)}\n\n"
        f"{format_legal_actions(legal_actions, obs, name)}"
    )


def _render_decision_prompt(obs: dict, name: str) -> str:
    return (
        f"{format_observation(obs, name)}\n\n"
        f"{format_recent_public_actions([], obs=obs)}\n\n"
        f"{format_structured_decision(obs, name)}"
    )
