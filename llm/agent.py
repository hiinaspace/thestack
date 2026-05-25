"""Generic LLM agent loop on the native Ollama SDK.

Holds a persistent message history across turns, drives a tool-call loop, and
logs THINKING / REASONING / TOOL_CALL events to the game's event log. Used by
both PlayerAgent and CommentatorAgent.
"""

from __future__ import annotations

from dataclasses import dataclass

import ollama

from game.events import MONOLOGUE, REASONING, TABLE_TALK, THINKING, TOOL_CALL, EventLog
from llm.tools import VOICE_TOOLS, Toolbox, serialize_tool_args


@dataclass
class AgentResponse:
    content: str
    thinking: str | None
    # Set if the model invoked submit_action; None if the tool loop ran out or
    # the model never called it.
    action_id: int | None
    # Set if the model invoked submit_decision for a structured gym decision.
    decision_response: dict | None
    # Per-turn natural-language reasoning, captured via submit_action's
    # or submit_decision's `reasoning` argument when available; falls back to
    # message content.
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

    def run(
        self,
        user_message: str,
        *,
        verbose: bool = False,
        max_iterations: int | None = None,
        wait_for_commit: bool = True,
    ) -> AgentResponse:
        """Send a user turn; loop through any tool calls; return final answer.

        With defaults, the loop also breaks early once the toolbox records a
        submit_action / submit_decision commit. Pass ``wait_for_commit=False``
        for narration-only callers (react hooks) that don't expect a commit;
        the loop then exits cleanly as soon as the model stops calling tools.
        ``max_iterations`` overrides the class default for tight react budgets.
        """
        self.history.append({"role": "user", "content": user_message})

        last_content = ""
        last_thinking: str | None = None
        limit = max_iterations if max_iterations is not None else self.MAX_TOOL_ITERATIONS

        for _ in range(limit):
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
                return AgentResponse(
                    content=err,
                    thinking=None,
                    action_id=None,
                    decision_response=None,
                    reasoning=err,
                )

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
            if (
                wait_for_commit
                and self.toolbox is not None
                and (
                    self.toolbox.chosen_action_id is not None
                    or self.toolbox.chosen_decision_response is not None
                )
            ):
                break

        action_id = self.toolbox.chosen_action_id if self.toolbox else None
        decision_response = self.toolbox.chosen_decision_response if self.toolbox else None
        reasoning = (
            self.toolbox.chosen_reasoning
            if (self.toolbox and self.toolbox.chosen_reasoning)
            else last_content
        )
        return AgentResponse(
            content=last_content,
            thinking=last_thinking,
            action_id=action_id,
            decision_response=decision_response,
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
        voice_event = VOICE_TOOLS.get(name)
        if voice_event:
            text = str(args.get("text", "")).strip()
            event_type = MONOLOGUE if voice_event == "monologue" else TABLE_TALK
            self.event_log.append(event_type, {"player": self.name, "text": text})
            if verbose and text:
                label = "monologue" if voice_event == "monologue" else "table talk"
                print(f"  [{self.name} {label}] {text[:160]}")
        else:
            self.event_log.append(
                TOOL_CALL,
                {"player": self.name, "tool": name, "args": args, "result": result},
            )
            if verbose:
                arg_preview = ", ".join(f"{k}={v!r}" for k, v in args.items())[:120]
                print(f"  [{self.name} tool] {name}({arg_preview}) -> {result[:80]}")
        self.history.append({"role": "tool", "content": result, "tool_name": name})
