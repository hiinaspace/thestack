"""Phase B spike — Claude Agent SDK adapter for The Stack.

Subscription-billed (Pro/Max) Claude path. The SDK is fundamentally async and
agentic: you open a ClaudeSDKClient, define tools as @tool-decorated async
functions, send a user message via ``client.query``, and stream messages
back via ``client.receive_response``.

This module exposes ``ClaudeDecisionSession``, a *narrow* adapter focused on
the structured-decision integration tests:

- one session per (game, persona)
- one ``run_user_turn`` per decision
- tools mirror llm.tools.Toolbox semantics but execute as async MCP tools

It is NOT a drop-in replacement for ``llm.agent.Agent`` yet. The full
PydanticAI-fronted refactor described in plan track B1 stays deferred; this
file is the spike that confirms the SDK call shape works for our
one-user-turn-per-decision loop. Once we trust it, the next step is folding
the Ollama and SDK paths behind a single backend Protocol.

Authentication: the SDK reuses ``~/.claude/`` credentials populated by
``claude /login``. No API key required for subscription users.

Prompt caching: the SDK auto-caches the system prompt, the registered tool
definitions, and accumulated conversation history while a ClaudeSDKClient is
held open. Two implications:

- Keep ONE ClaudeDecisionSession open for the whole game (per persona); a
  per-decision session loses every prior decision's cache benefit.
- Do not mutate ``system_prompt`` mid-game. ``build_player_system_prompt``
  bakes in persona identity + decklist + opponent notes + recent_memory at
  game start; treat that snapshot as immutable for the session's lifetime.

Known cache micro-leak (acceptable for now): each per-decision user message
re-renders ``format_recent_public_actions`` with the last N opponent actions,
which substantially overlaps the previous decision's user message. The cache
still hits on everything *before* the new user message, but the redundant
recent-actions block is paid for on every turn. Trimming this to "just the
latest action" once cache savings dominate is a worthwhile follow-up.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

try:  # optional dep — installed via `uv sync --group frontier`
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
        create_sdk_mcp_server,
        tool,
    )

    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover — graceful degradation if dep missing
    SDK_AVAILABLE = False


HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-7"

# Map short aliases the CLI / runner accepts to canonical SDK model strings.
# The SDK itself also accepts "haiku" / "sonnet" / "opus" aliases but those
# bind late (whatever the harness considers current); pin to dated IDs by
# default so tests are reproducible.
MODEL_ALIASES: dict[str, str] = {
    "haiku": HAIKU_MODEL,
    "sonnet": SONNET_MODEL,
    "opus": OPUS_MODEL,
}


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


@dataclass
class ClaudeAgentTurnResult:
    """Mirror of llm.agent.AgentResponse for the Claude SDK path."""

    content: str
    thinking: str | None
    action_id: int | None
    decision_response: dict | None
    reasoning: str
    raw_tool_calls: list[dict] = field(default_factory=list)
    # Raw fields from the SDK ResultMessage; useful for diagnosing cache hit
    # rates and per-turn cost. None when the SDK didn't emit a ResultMessage
    # (shouldn't normally happen, but we don't want to crash if it does).
    usage: dict | None = None
    model_usage: dict | None = None
    total_cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    stop_reason: str | None = None


class ClaudeDecisionSession:
    """One Claude SDK session for one persona for one game.

    Designed for the structured-decision integration tests. Keep one session
    open across all decisions in a game so the conversation accumulates
    (matching how Ollama PlayerAgent works).
    """

    def __init__(
        self,
        *,
        system_prompt: str,
        toolbox: Any,  # llm.tools.Toolbox — typed as Any to keep this module import-light
        model: str = HAIKU_MODEL,
        max_turns: int = 12,
        verbose: bool = False,
        event_log: Any = None,  # game.events.EventLog — optional; when set, voice + tool calls land in it
        persona_name: str = "",
    ) -> None:
        if not SDK_AVAILABLE:
            raise RuntimeError("claude-agent-sdk is not installed. Run `uv sync --group frontier`.")
        self.system_prompt = system_prompt
        self.toolbox = toolbox
        self.model = resolve_model(model)
        self.max_turns = max_turns
        self.verbose = verbose
        self.event_log = event_log
        self.persona_name = persona_name or getattr(toolbox, "name", "")
        self._mcp_server = _build_mcp_server(
            toolbox, event_log=event_log, persona_name=self.persona_name
        )
        self._allowed_tools = [f"mcp__thestack__{name}" for name in _TOOL_NAMES]
        self._client: ClaudeSDKClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Open the underlying ClaudeSDKClient. Idempotent; safe to re-call."""
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._client = self._loop.run_until_complete(self._open_client())

    def close(self) -> None:
        """Close the SDK client + asyncio loop. Idempotent."""
        if self._loop is None:
            return
        if self._client is not None:
            self._loop.run_until_complete(self._close_client())
        self._loop.close()
        self._loop = None
        self._client = None

    def __enter__(self) -> ClaudeDecisionSession:
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    async def _open_client(self) -> ClaudeSDKClient:
        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=self.system_prompt,
            # Empty list disables all built-in Claude Code tools (Bash/Read/Edit/...).
            # We only want our MCP tools; otherwise the built-ins bloat the cached
            # tool-definitions prefix on every request.
            tools=[],
            mcp_servers={"thestack": self._mcp_server},
            allowed_tools=self._allowed_tools,
            permission_mode="bypassPermissions",
            max_turns=self.max_turns,
        )
        client = ClaudeSDKClient(options=options)
        await client.__aenter__()
        return client

    async def _close_client(self) -> None:
        assert self._client is not None
        await self._client.__aexit__(None, None, None)

    # --------------------------------------------------------------- per-turn

    def run_user_turn(self, prompt: str) -> ClaudeAgentTurnResult:
        """Send a user message; let Claude work; return the captured action."""
        assert self._loop is not None and self._client is not None
        return self._loop.run_until_complete(self._run_user_turn_async(prompt))

    async def _run_user_turn_async(self, prompt: str) -> ClaudeAgentTurnResult:
        assert self._client is not None
        last_text = ""
        last_thinking: str | None = None
        raw_tool_calls: list[dict] = []
        result_usage: dict | None = None
        result_model_usage: dict | None = None
        result_cost: float | None = None
        result_duration_ms: int | None = None
        result_num_turns: int | None = None
        result_stop_reason: str | None = None

        await self._client.query(prompt)
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        last_text = block.text or last_text
                        if self.verbose and block.text:
                            print(f"[claude] {block.text[:200]}")
                    elif isinstance(block, ThinkingBlock):
                        last_thinking = block.thinking or last_thinking
                        if self.event_log is not None and block.thinking:
                            self.event_log.append(
                                "thinking",
                                {"player": self.persona_name, "text": block.thinking},
                            )
                        if self.verbose and block.thinking:
                            print(f"[claude thinking] {block.thinking[:200]}")
                    elif isinstance(block, ToolUseBlock):
                        raw_tool_calls.append({"name": block.name, "input": block.input})
                        if self.verbose:
                            preview = json.dumps(block.input)[:160]
                            print(f"[claude tool] {block.name} {preview}")
            elif isinstance(message, ResultMessage):
                result_usage = message.usage
                result_model_usage = message.model_usage
                result_cost = message.total_cost_usd
                result_duration_ms = message.duration_ms
                result_num_turns = message.num_turns
                result_stop_reason = message.stop_reason
                # Final wrap message from the SDK — the loop terminates next.
                break

        reasoning = self.toolbox.chosen_reasoning or last_text
        return ClaudeAgentTurnResult(
            content=last_text,
            thinking=last_thinking,
            action_id=self.toolbox.chosen_action_id,
            decision_response=self.toolbox.chosen_decision_response,
            reasoning=reasoning,
            raw_tool_calls=raw_tool_calls,
            usage=result_usage,
            model_usage=result_model_usage,
            total_cost_usd=result_cost,
            duration_ms=result_duration_ms,
            num_turns=result_num_turns,
            stop_reason=result_stop_reason,
        )


# -------------------------------------------------------------------- tools


_TOOL_NAMES = (
    "take_note",
    "recall_strategy",
    "monologue",
    "table_talk",
    "set_turn_plan",
    "update_turn_plan",
    "submit_action",
    "submit_decision",
)


def _build_mcp_server(toolbox: Any, *, event_log: Any = None, persona_name: str = "") -> Any:
    """Build an in-process SDK MCP server whose tools delegate to the Toolbox.

    The Ollama path uses sync OpenAI-shaped tool schemas in llm.tools; here we
    wrap the same Toolbox methods as @tool-decorated async functions. State
    lives in the shared Toolbox instance, so submit_action / submit_decision
    record their choice into the same field the Ollama path reads.

    When ``event_log`` is provided, the wrappers also emit the same
    MONOLOGUE / TABLE_TALK / TOOL_CALL events that the Ollama Agent loop
    emits, so the viewer + spectator transcript look identical regardless
    of which backend drove the turn.
    """

    async def _wrap(fn: Callable[..., str]) -> dict:
        try:
            text = fn()
        except Exception as e:
            text = f"ERROR: {e}"
        return {"content": [{"type": "text", "text": text}]}

    def _log_voice(kind: str, text: str) -> None:
        if event_log is None or not text:
            return
        event_log.append(kind, {"player": persona_name, "text": text})

    def _log_tool(name: str, args: dict[str, Any], result: str) -> None:
        if event_log is None:
            return
        event_log.append(
            "tool_call",
            {"player": persona_name, "tool": name, "args": args, "result": result},
        )

    @tool("take_note", "Save a short strategic note to your scratchpad.", {"note": str})
    async def take_note(args: dict[str, Any]) -> dict:
        result = await _wrap(lambda: toolbox.take_note(str(args.get("note", ""))))
        _log_tool("take_note", args, result["content"][0]["text"])
        return result

    @tool("recall_strategy", "Return all strategy notes saved so far this game.", {})
    async def recall_strategy(_args: dict[str, Any]) -> dict:
        result = await _wrap(toolbox.recall_strategy)
        _log_tool("recall_strategy", _args, result["content"][0]["text"])
        return result

    @tool(
        "monologue",
        "Speak an in-character internal-monologue line; spectators see it, opponent does not.",
        {"text": str},
    )
    async def monologue(args: dict[str, Any]) -> dict:
        text = str(args.get("text", "")).strip()
        result = await _wrap(lambda: toolbox.monologue(text))
        _log_voice("monologue", text)
        return result

    @tool(
        "table_talk",
        "Speak an in-character line at your opponent; they read it at the start of their next turn.",
        {"text": str},
    )
    async def table_talk(args: dict[str, Any]) -> dict:
        text = str(args.get("text", "")).strip()
        result = await _wrap(lambda: toolbox.table_talk(text))
        _log_voice("table_talk", text)
        return result

    @tool(
        "set_turn_plan",
        "Commit a plan for THIS MTG turn (intent, action sequence, notes). "
        "Call once on your first decision of the turn; the plan surfaces at "
        "the top of every subsequent prompt this turn so you execute against "
        "it rather than re-deriving.",
        {"intent": str, "action_sequence": list, "notes": str},
    )
    async def set_turn_plan(args: dict[str, Any]) -> dict:
        intent = str(args.get("intent", ""))
        action_sequence = args.get("action_sequence") or []
        if isinstance(action_sequence, str):
            action_sequence = [action_sequence]
        notes = str(args.get("notes", ""))
        result = await _wrap(lambda: toolbox.set_turn_plan(intent, list(action_sequence), notes))
        _log_tool("set_turn_plan", args, result["content"][0]["text"])
        return result

    @tool(
        "update_turn_plan",
        "Revise the turn plan when something material happens mid-turn. "
        "Records revision history for the spectator transcript.",
        {"revised_intent": str, "reason": str},
    )
    async def update_turn_plan(args: dict[str, Any]) -> dict:
        result = await _wrap(
            lambda: toolbox.update_turn_plan(
                str(args.get("revised_intent", "")), str(args.get("reason", ""))
            )
        )
        _log_tool("update_turn_plan", args, result["content"][0]["text"])
        return result

    @tool(
        "submit_action",
        "Commit to one numbered legal action; ends your decision.",
        {"action_id": int, "reasoning": str},
    )
    async def submit_action(args: dict[str, Any]) -> dict:
        return await _wrap(
            lambda: toolbox.submit_action(int(args["action_id"]), str(args.get("reasoning", "")))
        )

    @tool(
        "submit_decision",
        "Commit a structured Argentum DecisionResponse for a pending decision.",
        {"response": dict, "reasoning": str},
    )
    async def submit_decision(args: dict[str, Any]) -> dict:
        response = args.get("response")
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "ERROR: response must be a JSON object, got unparseable string.",
                        }
                    ]
                }
        return await _wrap(
            lambda: toolbox.submit_decision(
                response if isinstance(response, dict) else {},
                str(args.get("reasoning", "")),
            )
        )

    return create_sdk_mcp_server(
        name="thestack",
        version="0.1.0",
        tools=[
            take_note,
            recall_strategy,
            monologue,
            table_talk,
            set_turn_plan,
            update_turn_plan,
            submit_action,
            submit_decision,
        ],
    )


# --------------------------------------------------------------------- player


class ClaudePlayerAgent:
    """PlayerAgent surface backed by ClaudeDecisionSession instead of Ollama.

    Mirrors llm.player.PlayerAgent: same constructor signature, same public
    methods (``choose_action``, ``choose_decision``), same ``toolbox`` and
    ``last_reasoning`` attributes. Holds ONE ClaudeDecisionSession open for
    the entire game so prompt caching is effective across all decisions.
    Caller must invoke ``close()`` when the game ends to release the loop +
    SDK client.
    """

    def __init__(
        self,
        persona: Any,
        opponent_name: str,
        model: str,
        event_log: Any,
        deck: dict[str, int] | None = None,
        *,
        verbose: bool = False,
        max_turns: int = 12,
    ) -> None:
        # Lazy imports to keep this module importable without the full harness
        # context (e.g. when only the integration tests use ClaudeDecisionSession).
        from llm import oracle
        from llm.prompts import build_player_system_prompt
        from llm.tools import Toolbox

        self.persona = persona
        self.name = persona.name
        self.event_log = event_log
        self.toolbox = Toolbox(name=persona.name)
        self.last_reasoning: str = ""
        # Dev-only usage telemetry lives next to game.jsonl, not in it —
        # spectators should never see cache hit rates or dollar amounts.
        self._usage_path = (
            event_log.path.parent / "sdk_usage.jsonl"
            if event_log is not None and getattr(event_log, "path", None) is not None
            else None
        )
        system_prompt = build_player_system_prompt(
            persona.name,
            opponent_name,
            identity=persona.identity,
            decklist=oracle.deck_listing(deck or {}),
            strategy=persona.strategy,
            opponent_notes=persona.opponent_entry(opponent_name),
            recent_memory=persona.recent_memory(),
        )
        self.session = ClaudeDecisionSession(
            system_prompt=system_prompt,
            toolbox=self.toolbox,
            model=model,
            max_turns=max_turns,
            verbose=verbose,
            event_log=event_log,
            persona_name=persona.name,
        )
        self.session.start()

    # -------------------------------------------------------------- telemetry

    def _record_usage(self, kind: str, result: ClaudeAgentTurnResult) -> None:
        """Append one decision's SDK usage stats to the side file."""
        if self._usage_path is None:
            return
        # event_log carries the current turn/phase context the harness set
        # before calling choose_action; use it directly so the side file lines
        # up with game.jsonl on turn boundaries.
        turn = getattr(self.event_log, "_turn", None)
        phase = getattr(self.event_log, "_phase", None)
        entry = {
            "ts": time.time(),
            "player": self.name,
            "kind": kind,
            "turn": turn,
            "phase": phase,
            "model": self.session.model,
            "duration_ms": result.duration_ms,
            "num_turns": result.num_turns,
            "stop_reason": result.stop_reason,
            "total_cost_usd": result.total_cost_usd,
            "usage": result.usage,
            "model_usage": result.model_usage,
        }
        with self._usage_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ---------------------------------------------------------------- actions

    def choose_action(
        self,
        obs: dict,
        verbose: bool = False,
        recent_public_actions: list[dict] | None = None,
    ) -> int:
        from llm.player import _find_pass_id, _parse_action_id_fallback
        from llm.prompts import (
            format_combat_evaluator,
            format_legal_actions,
            format_mulligan_evaluator,
            format_observation,
            format_recent_public_actions,
            format_turn_plan,
        )

        legal_actions = obs.get("legalActions", [])
        if not legal_actions:
            return 0

        valid_ids = {a["actionId"] for a in legal_actions}
        pass_id = _find_pass_id(legal_actions)
        self.toolbox.reset_turn(valid_ids)

        user_msg = (
            f"{format_turn_plan(self.toolbox.turn_plan)}\n\n"
            f"{format_observation(obs, self.name)}\n\n"
            f"{format_recent_public_actions(recent_public_actions or [])}\n\n"
            f"{format_combat_evaluator(obs, self.name, legal_actions)}\n\n"
            f"{format_mulligan_evaluator(obs, self.name, legal_actions)}\n\n"
            f"{format_legal_actions(legal_actions, obs, self.name)}\n\n"
            "If no turn plan is committed yet, set one before submit_action. "
            "If one is committed, execute the next step — keep voice tight "
            "unless something material changed (call update_turn_plan if it "
            "did). Chain tool calls in this single response."
        )

        result = self.session.run_user_turn(user_msg)
        self.last_reasoning = result.reasoning
        self._record_usage("action", result)

        action_id = result.action_id
        if action_id is None:
            action_id = _parse_action_id_fallback(result.content, valid_ids)
        if action_id is None or action_id not in valid_ids:
            action_id = pass_id

        chosen = next((a for a in legal_actions if a["actionId"] == action_id), None)
        self.event_log.append(
            "action",
            {
                "player": self.name,
                "action_id": action_id,
                "description": chosen.get("description") if chosen else None,
                "reasoning": result.reasoning,
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

        Mirrors PlayerAgent.react: monologue / table_talk / take_note /
        set_turn_plan / update_turn_plan only — no commit. Emits MONOLOGUE /
        TABLE_TALK via the MCP-tool dispatcher and a thin ACTION record so
        the public_action_history surfaces the beat for the opponent.
        """
        from llm.prompts import format_react_prompt

        # Snapshot pre-react decision-state so reset_turn isn't needed; just
        # clear anything that may have been left around from the last action.
        self.toolbox.turn_monologues = []
        self.toolbox.turn_table_talk = []
        self.toolbox.chosen_action_id = None
        self.toolbox.chosen_decision_response = None
        self.toolbox.chosen_reasoning = ""

        user_msg = format_react_prompt(
            obs, self.name, trigger, engine_events, self.toolbox.turn_plan
        )
        result = self.session.run_user_turn(user_msg)
        self._record_usage(f"react_{trigger}", result)

        monologues = list(self.toolbox.turn_monologues)
        table_talk = list(self.toolbox.turn_table_talk)
        # Drain so the next real action's record doesn't double-attach them.
        self.toolbox.turn_monologues = []
        self.toolbox.turn_table_talk = []
        if monologues or table_talk:
            self.event_log.append(
                "action",
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
        from llm.player import (
            _default_decision_response,
            _parse_decision_response_fallback,
            _valid_decision_response,
        )
        from llm.prompts import (
            format_observation,
            format_recent_public_actions,
            format_structured_decision,
        )

        pending = obs.get("pendingDecision") or {}
        decision_id = pending.get("decisionId")
        self.toolbox.reset_turn(set(), valid_decision_id=decision_id)

        user_msg = (
            f"{format_observation(obs, self.name)}\n\n"
            f"{format_recent_public_actions(recent_public_actions or [])}\n\n"
            f"{format_structured_decision(obs, self.name)}\n\n"
            "Stay in voice. A quick monologue() line is welcome if this "
            "decision matters; otherwise just construct the DecisionResponse "
            "JSON and call submit_decision."
        )

        result = self.session.run_user_turn(user_msg)
        self.last_reasoning = result.reasoning
        self._record_usage("decision", result)

        decision_response = result.decision_response
        if decision_response is None:
            decision_response = _parse_decision_response_fallback(result.content, decision_id)
        if not _valid_decision_response(decision_response, decision_id):
            decision_response = _default_decision_response(obs)

        self.event_log.append(
            "action",
            {
                "player": self.name,
                "action_id": None,
                "description": f"{pending.get('kind', 'DECISION')}: {pending.get('prompt', '')}",
                "reasoning": result.reasoning,
                "decision_response": decision_response,
            },
        )
        return decision_response

    def close(self) -> None:
        """Release the underlying SDK session. Safe to call multiple times."""
        self.session.close()


# Optional: small smoke-runnable to confirm auth + tool wiring with no game state.
def _self_test() -> None:  # pragma: no cover — manual smoke
    from llm.tools import Toolbox

    tb = Toolbox(name="probe")
    tb.reset_turn({1, 2, 3})
    with ClaudeDecisionSession(
        system_prompt=(
            "You are a test agent. When asked, pick action 2 and call submit_action "
            "with action_id=2 and a one-sentence reasoning."
        ),
        toolbox=tb,
        model="haiku",
        verbose=True,
    ) as session:
        result = session.run_user_turn(
            "LEGAL ACTIONS: 1 pass, 2 attack, 3 cast. Please choose action 2."
        )
    print("action_id =", result.action_id)
    print("reasoning =", result.reasoning)


if __name__ == "__main__":  # pragma: no cover
    _self_test()
