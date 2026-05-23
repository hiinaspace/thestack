"""Generic LLM agent loop on the native Ollama SDK.

Holds a persistent message history across turns, drives a tool-call loop, and
logs THINKING / REASONING / TOOL_CALL events to the game's event log. Used by
both PlayerAgent and CommentatorAgent.
"""

from __future__ import annotations

from dataclasses import dataclass

import ollama

from game.events import REASONING, THINKING, TOOL_CALL, EventLog
from llm.tools import Toolbox, serialize_tool_args


@dataclass
class AgentResponse:
    content: str
    thinking: str | None
    # Set if the model invoked submit_action; None if the tool loop ran out or
    # the model never called it.
    action_id: int | None
    # Per-turn natural-language reasoning, captured via submit_action's
    # `reasoning` argument when available; falls back to message content.
    reasoning: str


class Agent:
    """One LLM agent with a persistent conversation and optional tool access."""

    MAX_TOOL_ITERATIONS = 12

    def __init__(
        self,
        *,
        name: str,
        model: str,
        client: ollama.Client,
        event_log: EventLog,
        system_prompt: str,
        toolbox: Toolbox | None = None,
        temperature: float = 0.6,
        think: bool = True,
        log_content_as: str | None = REASONING,
    ) -> None:
        self.name = name
        self.model = model
        self.client = client
        self.event_log = event_log
        self.toolbox = toolbox
        self.temperature = temperature
        self.think = think
        # Event type to log assistant content under (set to None for agents
        # whose content is logged separately, e.g. the commentator).
        self.log_content_as = log_content_as
        self.history: list[dict] = [{"role": "system", "content": system_prompt}]

    # ------------------------------------------------------------------- run

    def run(self, user_message: str, *, verbose: bool = False) -> AgentResponse:
        """Send a user turn; loop through any tool calls; return final answer."""
        self.history.append({"role": "user", "content": user_message})

        last_content = ""
        last_thinking: str | None = None

        for _ in range(self.MAX_TOOL_ITERATIONS):
            try:
                response = self.client.chat(
                    model=self.model,
                    messages=self.history,
                    tools=self._tools_payload(),
                    think=self.think,
                    options={"temperature": self.temperature},
                )
            except Exception as e:
                err = f"[LLM error: {e}]"
                self.event_log.append(REASONING, {"player": self.name, "text": err})
                return AgentResponse(content=err, thinking=None, action_id=None, reasoning=err)

            msg = response.message
            last_content = msg.content or ""
            last_thinking = msg.thinking

            self._record_turn(msg, verbose=verbose)
            self._append_assistant_to_history(msg)

            tool_calls = list(msg.tool_calls or [])
            if not tool_calls:
                break

            for tc in tool_calls:
                self._execute_tool_call(tc, verbose=verbose)

            # If the toolbox recorded an action, we're done.
            if self.toolbox is not None and self.toolbox.chosen_action_id is not None:
                break

        action_id = self.toolbox.chosen_action_id if self.toolbox else None
        reasoning = (
            self.toolbox.chosen_reasoning
            if (self.toolbox and self.toolbox.chosen_reasoning)
            else last_content
        )
        return AgentResponse(
            content=last_content,
            thinking=last_thinking,
            action_id=action_id,
            reasoning=reasoning,
        )

    # --------------------------------------------------------------- helpers

    def _tools_payload(self) -> list[dict] | None:
        if self.toolbox is None:
            return None
        from llm.tools import TOOL_SCHEMAS

        return TOOL_SCHEMAS

    def _record_turn(self, msg, *, verbose: bool) -> None:
        if msg.thinking:
            self.event_log.append(THINKING, {"player": self.name, "text": msg.thinking})
            if verbose:
                excerpt = msg.thinking.replace("\n", " ")[:200]
                ellipsis = "..." if len(msg.thinking) > 200 else ""
                print(f"\n  [{self.name} thinking] {excerpt}{ellipsis}")
        if msg.content and self.log_content_as:
            self.event_log.append(self.log_content_as, {"player": self.name, "text": msg.content})
            if verbose:
                print(f"\n[{self.name}] {msg.content}")

    def _append_assistant_to_history(self, msg) -> None:
        # Reconstruct the message in Ollama's expected dict form so the next
        # request replays it correctly.
        entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.thinking:
            entry["thinking"] = msg.thinking
        if msg.tool_calls:
            entry["tool_calls"] = [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": serialize_tool_args(tc.function.arguments),
                    }
                }
                for tc in msg.tool_calls
            ]
        self.history.append(entry)

    def _execute_tool_call(self, tc, *, verbose: bool) -> None:
        assert self.toolbox is not None
        name = tc.function.name
        args = serialize_tool_args(tc.function.arguments)
        result = self.toolbox.dispatch(name, args)
        self.event_log.append(
            TOOL_CALL,
            {"player": self.name, "tool": name, "args": args, "result": result},
        )
        if verbose:
            arg_preview = ", ".join(f"{k}={v!r}" for k, v in args.items())[:120]
            print(f"  [{self.name} tool] {name}({arg_preview}) -> {result[:80]}")
        self.history.append({"role": "tool", "content": result, "tool_name": name})
