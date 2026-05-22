"""Player LLM agent — tool-call loop until pass_priority or concede."""

from __future__ import annotations

import json

import ollama

from game.actions import PLAYER_TOOLS, ActionError, dispatch_tool
from game.events import REASONING, THINKING, EventLog
from game.state import GameState
from llm.prompts import build_player_system_prompt, format_game_state_for_player

MAX_TOOL_ITERATIONS = 20  # guard against infinite loops

# Convert our OpenAI-schema tool defs to the dict format Ollama accepts
# (Ollama's Python SDK accepts the same schema directly)
_OLLAMA_TOOLS = PLAYER_TOOLS


class PlayerAgent:
    def __init__(self, name: str, model: str, client: ollama.Client, event_log: EventLog) -> None:
        self.name = name
        self.model = model
        self.client = client
        self.event_log = event_log
        self._system_prompt: str | None = None

    def _get_system_prompt(self, opponent_name: str) -> str:
        if self._system_prompt is None:
            self._system_prompt = build_player_system_prompt(self.name, opponent_name)
        return self._system_prompt

    def take_action(self, state: GameState, context_message: str, verbose: bool = False) -> None:
        """
        Run the agent's tool-call loop for the current game phase.
        Exits when pass_priority or concede is called, or after MAX_TOOL_ITERATIONS.
        """
        opponent_name = state.opponent_of(self.name)
        system_prompt = self._get_system_prompt(opponent_name)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context_message},
        ]

        for _iteration in range(MAX_TOOL_ITERATIONS):
            if state.game_over:
                break

            try:
                response = self.client.chat(
                    model=self.model,
                    messages=messages,
                    tools=_OLLAMA_TOOLS,
                    think=True,
                    options={"temperature": 0.8},
                )
            except Exception as e:
                self.event_log.append(REASONING, {"player": self.name, "text": f"[LLM error: {e}]"})
                break

            msg = response.message

            # Log the internal thinking trace if present
            if msg.thinking:
                self.event_log.append(THINKING, {"player": self.name, "text": msg.thinking})
                if verbose:
                    # Show a brief excerpt so the terminal isn't flooded
                    excerpt = msg.thinking[:200].replace("\n", " ")
                    print(
                        f"\n  [{self.name} thinking] {excerpt}{'...' if len(msg.thinking) > 200 else ''}"
                    )

            # Log natural language narration
            if msg.content:
                if verbose:
                    print(f"\n[{self.name}] {msg.content}")
                self.event_log.append(REASONING, {"player": self.name, "text": msg.content})

            if not msg.tool_calls:
                # No tool call — try fallback JSON parse, otherwise treat as pass_priority
                tool_call = _parse_fallback_json(msg.content or "")
                if tool_call:
                    tool_name, args = tool_call
                    result = self._run_tool(tool_name, args, state, verbose)
                    # Append assistant turn + tool result as plain user message
                    messages.append({"role": "assistant", "content": msg.content})
                    messages.append({"role": "user", "content": f"Tool result: {result}"})
                    if tool_name in ("pass_priority", "concede"):
                        break
                else:
                    if verbose:
                        print(f"[{self.name}] No tool call, treating as pass_priority")
                    dispatch_tool("pass_priority", {}, state, self.name, self.event_log)
                    break
                continue

            # Append assistant message with tool calls to history
            # Ollama Message objects can be passed directly back into messages
            messages.append(msg)

            done = False
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                # Ollama SDK gives arguments as a dict already (not a JSON string)
                args = tc.function.arguments if isinstance(tc.function.arguments, dict) else {}

                result = self._run_tool(tool_name, args, state, verbose)

                # Ollama tool result format: role=tool, content=result string
                messages.append({"role": "tool", "content": result, "name": tool_name})

                if tool_name in ("pass_priority", "concede"):
                    done = True
                    break

            if done or state.game_over:
                break

        else:
            # Hit iteration limit
            if verbose:
                print(f"[{self.name}] Hit iteration limit, forcing pass_priority")
            dispatch_tool("pass_priority", {}, state, self.name, self.event_log)

    def _run_tool(self, tool_name: str, args: dict, state: GameState, verbose: bool) -> str:
        if verbose:
            arg_str = json.dumps(args) if args else ""
            print(f"  [{self.name}] -> {tool_name}({arg_str})")

        if tool_name == "appeal":
            from llm.judge import JudgeAgent

            situation = args.get("situation", "")
            judge = JudgeAgent(self.client, self.event_log)
            ruling = judge.rule(situation, state)
            if verbose:
                print(f"  [JUDGE] {ruling}")
            return f"Judge ruling: {ruling}"

        try:
            result = dispatch_tool(tool_name, args, state, self.name, self.event_log)
        except ActionError as e:
            result = f"Action error: {e}"

        if verbose and tool_name not in ("get_game_state", "get_hand"):
            print(f"  [{self.name}] <- {result}")
        return result

    def ask_for_blockers(self, state: GameState, verbose: bool = False) -> None:
        """Prompt the defending player to declare blockers."""
        attacker_player = state.active_player
        game_state_str = format_game_state_for_player(state, self.name)

        attacker_creatures = [
            c.name
            for c in state.players[attacker_player].battlefield
            if c.instance_id in state.declared_attackers
        ]

        context = (
            f"BLOCKING PHASE\n\n"
            f"{game_state_str}\n\n"
            f"{attacker_player} is attacking with: {', '.join(attacker_creatures) or 'nothing'}.\n"
            f"Declare your blockers using declare_blockers, or pass_priority to take no blocks."
        )
        self.take_action(state, context, verbose=verbose)


def _parse_fallback_json(text: str) -> tuple[str, dict] | None:
    """Try to extract {\"tool\": ..., \"args\": ...} from text response."""
    import re

    match = re.search(r'\{[^{}]*"tool"\s*:\s*"([^"]+)"[^{}]*\}', text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj.get("tool"), obj.get("args", {})
    except json.JSONDecodeError:
        return None
