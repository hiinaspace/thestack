"""Deterministic autopass heuristics.

Before paying for an LLM call, check whether the observation has any
*meaningful* choice in it. If the only thing a thoughtful player could do
is Pass priority, do that for them.

Returning a Pass here also removes the most boring deliberation from the
spectator transcript: Aria thinking out loud about whether to pass priority
on Bryn's upkeep is anti-content. Real reasoning shows up only at real
decisions.
"""

from __future__ import annotations


def autopass_action_id(obs: dict) -> tuple[int, str] | None:
    """If this observation is structurally a Pass, return (action_id, reason).

    Returns None when the LLM should actually decide.
    """
    legal = obs.get("legalActions", [])
    if not legal:
        return None  # decision pending, different code path

    pass_action = next(
        (a for a in legal if "pass" in a.get("description", "").lower()),
        None,
    )
    if pass_action is None:
        return None  # no pass option, must engage

    non_pass = [a for a in legal if a is not pass_action]

    # Only option is Pass.
    if not non_pass:
        return pass_action["actionId"], "only Pass is legal"

    # Everything else is just tapping mana — the only outcome of any non-pass
    # action is floating mana with nowhere to spend it. Same as Pass.
    if all(a.get("isManaAbility", False) for a in non_pass):
        return pass_action["actionId"], "only mana abilities besides Pass"

    return None
