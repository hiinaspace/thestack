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
    ) -> None:
        if not SDK_AVAILABLE:
            raise RuntimeError("claude-agent-sdk is not installed. Run `uv sync --group frontier`.")
        self.system_prompt = system_prompt
        self.toolbox = toolbox
        self.model = resolve_model(model)
        self.max_turns = max_turns
        self.verbose = verbose
        self._mcp_server = _build_mcp_server(toolbox)
        self._allowed_tools = [f"mcp__thestack__{name}" for name in _TOOL_NAMES]
        self._client: ClaudeSDKClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -------------------------------------------------------------- lifecycle

    def __enter__(self) -> ClaudeDecisionSession:
        self._loop = asyncio.new_event_loop()
        self._client = self._loop.run_until_complete(self._open_client())
        return self

    def __exit__(self, *_exc: Any) -> None:
        assert self._loop is not None
        if self._client is not None:
            self._loop.run_until_complete(self._close_client())
        self._loop.close()
        self._loop = None
        self._client = None

    async def _open_client(self) -> ClaudeSDKClient:
        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=self.system_prompt,
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
                        if self.verbose and block.thinking:
                            print(f"[claude thinking] {block.thinking[:200]}")
                    elif isinstance(block, ToolUseBlock):
                        raw_tool_calls.append({"name": block.name, "input": block.input})
                        if self.verbose:
                            preview = json.dumps(block.input)[:160]
                            print(f"[claude tool] {block.name} {preview}")
            elif isinstance(message, ResultMessage):
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
        )


# -------------------------------------------------------------------- tools


_TOOL_NAMES = (
    "take_note",
    "recall_strategy",
    "monologue",
    "table_talk",
    "submit_action",
    "submit_decision",
)


def _build_mcp_server(toolbox: Any) -> Any:
    """Build an in-process SDK MCP server whose tools delegate to the Toolbox.

    The Ollama path uses sync OpenAI-shaped tool schemas in llm.tools; here we
    wrap the same Toolbox methods as @tool-decorated async functions. State
    lives in the shared Toolbox instance, so submit_action / submit_decision
    record their choice into the same field the Ollama path reads.
    """

    async def _wrap(fn: Callable[..., str]) -> dict:
        try:
            text = fn()
        except Exception as e:
            text = f"ERROR: {e}"
        return {"content": [{"type": "text", "text": text}]}

    @tool("take_note", "Save a short strategic note to your scratchpad.", {"note": str})
    async def take_note(args: dict[str, Any]) -> dict:
        return await _wrap(lambda: toolbox.take_note(str(args.get("note", ""))))

    @tool("recall_strategy", "Return all strategy notes saved so far this game.", {})
    async def recall_strategy(_args: dict[str, Any]) -> dict:
        return await _wrap(toolbox.recall_strategy)

    @tool(
        "monologue",
        "Speak an in-character internal-monologue line; spectators see it, opponent does not.",
        {"text": str},
    )
    async def monologue(args: dict[str, Any]) -> dict:
        return await _wrap(lambda: toolbox.monologue(str(args.get("text", ""))))

    @tool(
        "table_talk",
        "Speak an in-character line at your opponent; they read it at the start of their next turn.",
        {"text": str},
    )
    async def table_talk(args: dict[str, Any]) -> dict:
        return await _wrap(lambda: toolbox.table_talk(str(args.get("text", ""))))

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
            submit_action,
            submit_decision,
        ],
    )


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
