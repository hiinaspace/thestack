"""Reusable default-action policies for ScriptedAgent.

A policy is a callable ``(obs, legal_actions) -> action_id`` used by the
agent's fallback path (when no Directive in the script matches). Tests
compose a small policy + a few targeted directives instead of listing one
directive per phase step.
"""

from __future__ import annotations


def first_non_pass(obs: dict, legal_actions: list[dict]) -> int:
    """Default — pick the first action whose description isn't a pass."""
    for a in legal_actions:
        if "pass" not in (a.get("description") or "").lower():
            return int(a["actionId"])
    return int(legal_actions[0]["actionId"])


def aggressive(obs: dict, legal_actions: list[dict]) -> int:
    """Bias toward making combat actually happen.

    Priority order:
      1. Attack with all (so DECLARE_ATTACKERS commits a real attack)
      2. Block as many as possible / "Block X with Y" (real block at DECLARE_BLOCKERS)
      3. Play land / Cast spell
      4. Anything non-pass, non-skip
      5. Pass priority / Skip combat
    """
    priorities = (
        ("Attack with all", lambda d: d.startswith("Attack with all")),
        ("Block as many as possible", lambda d: d.startswith("Block as many as possible")),
        ("Block X with Y", lambda d: d.startswith("Block ") and " with " in d),
        ("Play land", lambda d: d.startswith("Play ")),
        ("Cast spell", lambda d: d.startswith("Cast ")),
        ("Attack with N", lambda d: d.startswith("Attack with ")),
    )
    for _label, predicate in priorities:
        for a in legal_actions:
            if predicate(a.get("description") or ""):
                return int(a["actionId"])
    # Fall through to first non-pass, then pass.
    return first_non_pass(obs, legal_actions)
