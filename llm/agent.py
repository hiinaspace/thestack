"""Generic LLM agent loop on the native Ollama SDK.

Holds a persistent message history across turns, drives a tool-call loop, and
logs THINKING / REASONING / TOOL_CALL events to the game's event log. Used by
both PlayerAgent and CommentatorAgent.

Per-call Ollama metrics (prompt_eval_count, eval_count, *_duration) are
streamed to ``<game_dir>/dev.jsonl`` via ``llm.dev_telemetry`` — diagnostic
data per [[feedback-dev-vs-spectator]], never to game.jsonl or the viewer.
"""

from __future__ import annotations

from dataclasses import dataclass

import ollama

from game.events import MONOLOGUE, REASONING, TABLE_TALK, THINKING, TOOL_CALL, EventLog
from llm import dev_telemetry
from llm.client import DEFAULT_NUM_CTX, chat_options
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


# Rough char->token ratio for a no-tokeniser pre-flight. Gemma's tokenizer
# averages ~3.5 chars/token on prose+JSON mixes; we use 3.0 to stay safe.
_CHARS_PER_TOKEN_EST = 3.0

# Keep at least this many messages of recent history untouched when
# compacting — the system prompt and the live decision context.
_KEEP_HEAD = 1  # system prompt
_KEEP_TAIL = 6  # last few exchanges (user + assistant + tool replies)


class Agent:
    """One LLM agent with a persistent conversation and optional tool access."""

    MAX_TOOL_ITERATIONS = 6

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
        num_ctx: int | None = None,
        num_predict: int | None = None,
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
        self.num_ctx = num_ctx if num_ctx is not None else DEFAULT_NUM_CTX
        self.num_predict = num_predict
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

        committed = False
        for iteration in range(limit):
            # Compact before each chat so we never send a request the
            # configured num_ctx can't hold. This is what keeps long games
            # from grinding to a halt under truncation/re-eval churn.
            self._compact_history_if_needed()

            options = chat_options(
                temperature=self.temperature,
                num_ctx=self.num_ctx,
                num_predict=self.num_predict,
            )

            with dev_telemetry.call_span(
                "ollama_chat",
                agent=self.name,
                model=self.model,
                iteration=iteration,
                history_len=len(self.history),
                history_chars=sum(len(_msg_text(m)) for m in self.history),
                num_ctx=options["num_ctx"],
                num_predict=options["num_predict"],
            ) as span:
                try:
                    response = self.client.chat(
                        model=self.model,
                        messages=self.history,
                        tools=self._tools_payload(),
                        think=self.think,
                        options=options,
                    )
                except Exception as e:
                    span["error"] = str(e)
                    err = f"[LLM error: {e}]"
                    self.event_log.append(REASONING, {"player": self.name, "text": err})
                    return AgentResponse(
                        content=err,
                        thinking=None,
                        action_id=None,
                        decision_response=None,
                        reasoning=err,
                    )
                dev_telemetry.record_ollama_response(span, response)

            msg = response.message
            last_content = msg.content or ""
            last_thinking = msg.thinking

            self._record_turn(msg, verbose=verbose)
            self._append_assistant_to_history(msg)

            tool_calls = list(msg.tool_calls or [])
            if not tool_calls:
                committed = True
                break

            for tc in tool_calls:
                self._execute_tool_call(tc, verbose=verbose)

            # Wait_for_commit: the loop also breaks early once the toolbox
            # records a submit_action / submit_decision commit. React hooks
            # pass wait_for_commit=False so narration-only passes exit cleanly
            # as soon as the model stops calling tools.
            if (
                wait_for_commit
                and self.toolbox is not None
                and (
                    self.toolbox.chosen_action_id is not None
                    or self.toolbox.chosen_decision_response is not None
                )
            ):
                committed = True
                break

        if not committed:
            # Loop ran to the iteration cap without breaking — record it so
            # we can see in dev.jsonl which decisions exhausted the budget.
            dev_telemetry.write_event(
                "tool_loop_exhausted",
                agent=self.name,
                iterations=limit,
                has_action=(self.toolbox is not None and self.toolbox.chosen_action_id is not None),
            )

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

    # ------------------------------------------------------- compaction -----

    def _compact_history_if_needed(self) -> None:
        """Drop / summarize old turns when the estimated prompt size approaches num_ctx.

        Strategy is intentionally simple: keep the system prompt and the
        most recent _KEEP_TAIL messages verbatim; collapse anything older
        into a single synthetic system note that retains scratchpad-worthy
        signal (count of dropped messages + the last few action results).

        This isn't a true summarizer — it's a guardrail. The persona's
        own scratchpad (take_note) is where durable cross-turn signal
        lives; this layer just keeps the prompt from blowing through the
        window mid-game and forcing the model to re-evaluate every token.
        """
        if len(self.history) <= _KEEP_HEAD + _KEEP_TAIL + 1:
            return

        # Reserve ~25% headroom for the model's reply.
        budget_tokens = int(self.num_ctx * 0.75)
        budget_chars = budget_tokens * _CHARS_PER_TOKEN_EST
        total_chars = sum(len(_msg_text(m)) for m in self.history)
        if total_chars <= budget_chars:
            return

        head = self.history[:_KEEP_HEAD]
        tail = self.history[-_KEEP_TAIL:]
        middle = self.history[_KEEP_HEAD:-_KEEP_TAIL]
        if not middle:
            return

        dropped = len(middle)
        # Retain a thin trail of the most recent committed actions / tool
        # results from the dropped span so the model has continuity without
        # the verbose narration that was bloating the window.
        trail_lines: list[str] = []
        for m in middle[-12:]:
            role = m.get("role", "?")
            text = _msg_text(m)
            if not text:
                continue
            preview = text.replace("\n", " ").strip()[:140]
            trail_lines.append(f"  - [{role}] {preview}")

        summary = {
            "role": "system",
            "content": (
                f"[harness compaction: dropped {dropped} older message(s) to "
                f"stay under num_ctx={self.num_ctx}. Recent trace below; the "
                f"player's own scratchpad (recall_strategy) holds durable "
                f"strategic notes.]\n" + "\n".join(trail_lines)
            ),
        }
        self.history = head + [summary] + tail
        dev_telemetry.write_event(
            "history_compacted",
            agent=self.name,
            dropped=dropped,
            new_len=len(self.history),
            new_chars=sum(len(_msg_text(m)) for m in self.history),
            budget_chars=int(budget_chars),
        )


def _msg_text(msg: dict) -> str:
    """Char count for a history message — used by the compaction heuristic."""
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    thinking = msg.get("thinking")
    if isinstance(thinking, str):
        parts.append(thinking)
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        parts.append(str(fn.get("name", "")))
        args = fn.get("arguments")
        if isinstance(args, dict):
            for v in args.values():
                parts.append(str(v))
    return " ".join(parts)
