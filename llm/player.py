"""Player LLM agent — tool-call loop until pass_priority or concede."""

from __future__ import annotations

import json

from openai import OpenAI

from game.actions import PLAYER_TOOLS, ActionError, dispatch_tool
from game.events import REASONING, EventLog
from game.state import GameState
from llm.prompts import build_player_system_prompt, format_game_state_for_player

MAX_TOOL_ITERATIONS = 20  # guard against infinite loops


class PlayerAgent:
    def __init__(self, name: str, model: str, client: OpenAI, event_log: EventLog) -> None:
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
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=PLAYER_TOOLS,
                    tool_choice="auto",
                    temperature=0.8,
                )
            except Exception as e:
                self.event_log.append(REASONING, {"player": self.name, "text": f"[LLM error: {e}]"})
                break

            msg = response.choices[0].message

            # Log reasoning/natural language output
            if msg.content:
                if verbose:
                    print(f"\n[{self.name}] {msg.content}")
                self.event_log.append(REASONING, {"player": self.name, "text": msg.content})

            if not msg.tool_calls:
                # No tool call — model may have finished or given a text response
                # Try to parse fallback JSON
                tool_call = _parse_fallback_json(msg.content or "")
                if tool_call:
                    tool_name, args = tool_call
                    result = self._run_tool(tool_name, args, state, verbose)
                    messages.append({"role": "assistant", "content": msg.content})
                    messages.append({"role": "user", "content": f"Tool result: {result}"})
                    if tool_name in ("pass_priority", "concede"):
                        break
                else:
                    # Model gave a plain text response — treat as pass_priority
                    if verbose:
                        print(f"[{self.name}] No tool call, treating as pass_priority")
                    dispatch_tool("pass_priority", {}, state, self.name, self.event_log)
                    break
                continue

            # Process tool calls
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            done = False
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                result = self._run_tool(tool_name, args, state, verbose)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

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
            # Import here to avoid circular
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
            f"{state.players[attacker_player].battlefield[i].name}"
            for i in range(len(state.players[attacker_player].battlefield))
            if state.players[attacker_player].battlefield[i].instance_id in state.declared_attackers
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
