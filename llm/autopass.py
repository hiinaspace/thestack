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

# Steps where the engine's stateDigest is known to be invariant across
# legitimate player commitments — declaring blockers/attackers stages the
# action without changing the digest, and combat damage resolves between
# observations. Using digest equality to infer "no progress" inside these
# steps produces a false positive that then bait the no_progress fallback
# into overwriting the player's choice with "No blocks" / "Skip combat".
# Observed in games/haiku-vs-e4b-002 T12, seq 2116-2125: three identical
# digests across two distinct submissions.
_DIGEST_UNRELIABLE_STEPS = frozenset(
    {
        "DECLARE_BLOCKERS",
        "DECLARE_ATTACKERS",
        "COMBAT_DAMAGE",
    }
)


def should_track_no_progress(obs: dict) -> bool:
    """True iff stateDigest equality is a reliable no-progress signal here.

    Returns False during combat-declaration steps where the digest does not
    incorporate the staged blocker/attacker assignment, so an unchanged
    digest after a real submission would be a false positive.
    """
    step = (obs.get("step") or "").upper()
    return step not in _DIGEST_UNRELIABLE_STEPS


def autopass_action_id(obs: dict) -> tuple[int, str] | None:
    """If this observation is structurally a Pass, return (action_id, reason).

    Returns None when the LLM should actually decide.
    """
    legal = obs.get("legalActions", [])
    if not legal:
        return None  # decision pending, different code path

    if len(legal) == 1:
        only = legal[0]
        desc = only.get("description", "").lower()
        if (
            desc.startswith("skip combat")
            or desc.startswith("skip blocks")
            or desc == "no blocks"
            or desc.startswith("no blocks ")
        ):
            return only["actionId"], "only non-choice combat action is legal"

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
