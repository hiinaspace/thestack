"""Live-harness tests for Phase 2 combat-decision refactor.

After the engine refactor:
- `DeclareAttackers` and `DeclareBlockers` shell actions surface as a
  single bare "Declare attackers" / "Declare blockers" entry in the legal
  actions list (no per-attacker / per-blocker fan-out from the gym
  expander, which is gone — see ``LegalActionExpander.kt``).
- Submitting one of those shells with an empty bindings map pauses the
  engine on a ``ChooseAttackersDecision`` / ``ChooseBlockersDecision``;
  the harness replies via ``/decision/advance`` with the structured
  response.

The tests below drive a real game and assert on the resulting engine
events + harness call records, validating both the new decision flow
and the gym observation shape that the harness depends on.

CLI:
    uv run python -m tests.scripted.test_combat_decisions
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from llm import argentum

from .policies import aggressive
from .runner import run_scripted_game
from .scripted_agent import DecisionScript


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def test_combat_step_emits_only_shell_action() -> bool:
    """The kludge is gone: combat-declaration steps emit a single bare
    shell action ("Declare attackers" / "Declare blockers") rather than
    one entry per attacker / per (attacker, blocker) pair.

    We assert by scanning every action recorded during the run — there
    should be no "Attack with X", "Block X with Y", "Skip combat", or
    "No blocks" descriptions. The engine routes those choices through
    CHOOSE_ATTACKERS / CHOOSE_BLOCKERS decisions instead.
    """
    print("\ntest_combat_step_emits_only_shell_action:")
    with tempfile.TemporaryDirectory() as td:
        result = run_scripted_game(
            persona_a="aria",
            persona_b="bryn",
            deck_a="red_rush",
            deck_b="green_might",
            library_seed=42,
            script_a=[],
            script_b=[],
            policy_a=aggressive,
            policy_b=aggressive,
            tmp_root=Path(td),
            max_steps=200,
            verbose=False,
            game_id="combat-shell",
        )

    legacy_prefixes = (
        "Attack with",
        "Block ",
        "Skip combat",
        "Skip blocks",
        "No blocks",
    )
    legacy_actions = [
        a
        for a in result.actions()
        if any((a.get("description") or "").startswith(p) for p in legacy_prefixes)
    ]
    return _check(
        "no legacy 'Attack with X' / 'Block X with Y' / 'Skip combat' actions submitted "
        "(gym kludge fully retired)",
        not legacy_actions,
        f"{len(legacy_actions)} legacy actions seen: "
        f"{[a.get('description') for a in legacy_actions[:3]]}",
    )


def test_choose_attackers_and_choose_blockers_decisions_fire() -> bool:
    """Both new structured-decision kinds should surface during a real
    game with both players ever swinging or blocking. We check via the
    ScriptedAgent's recorded decision-call kinds rather than digging
    through engine_events, since the agent records exactly the obs +
    decision_kind it was asked to respond to.
    """
    print("\ntest_choose_attackers_and_choose_blockers_decisions_fire:")
    with tempfile.TemporaryDirectory() as td:
        result = run_scripted_game(
            persona_a="aria",
            persona_b="bryn",
            deck_a="red_rush",
            deck_b="green_might",
            library_seed=42,
            script_a=[],
            script_b=[],
            policy_a=aggressive,
            policy_b=aggressive,
            tmp_root=Path(td),
            max_steps=200,
            verbose=False,
            game_id="combat-decisions",
        )

    attacker_calls = [
        c
        for c in result.agents["aria"].calls + result.agents["bryn"].calls
        if c.call_type == "decision" and (c.extra or {}).get("kind") == "CHOOSE_ATTACKERS"
    ]
    blocker_calls = [
        c
        for c in result.agents["aria"].calls + result.agents["bryn"].calls
        if c.call_type == "decision" and (c.extra or {}).get("kind") == "CHOOSE_BLOCKERS"
    ]
    ok = True
    ok &= _check(
        "at least one CHOOSE_ATTACKERS decision call surfaced to the harness",
        len(attacker_calls) >= 1,
        f"{len(attacker_calls)} calls",
    )
    ok &= _check(
        "at least one CHOOSE_BLOCKERS decision call surfaced to the harness",
        len(blocker_calls) >= 1,
        f"{len(blocker_calls)} calls",
    )
    return ok


def test_combat_decisions_commit_through_to_damage() -> bool:
    """End-to-end: the structured-decision path produces real engine
    events (AttackersDeclared, BlockersDeclared, DamageDealt) — i.e. the
    pause/resume roundtrip actually advances the state into combat
    damage."""
    print("\ntest_combat_decisions_commit_through_to_damage:")
    with tempfile.TemporaryDirectory() as td:
        result = run_scripted_game(
            persona_a="aria",
            persona_b="bryn",
            deck_a="red_rush",
            deck_b="green_might",
            library_seed=42,
            script_a=[],
            script_b=[],
            policy_a=aggressive,
            policy_b=aggressive,
            tmp_root=Path(td),
            max_steps=200,
            verbose=False,
            game_id="combat-resolve",
        )

    by_type: dict[str, int] = {}
    for ev in result.engine_events():
        t = ev.get("type")
        if t:
            by_type[t] = by_type.get(t, 0) + 1

    ok = True
    ok &= _check(
        "AttackersDeclared engine events fired",
        by_type.get("AttackersDeclared", 0) >= 1,
        f"{by_type.get('AttackersDeclared', 0)} events",
    )
    ok &= _check(
        "BlockersDeclared engine events fired",
        by_type.get("BlockersDeclared", 0) >= 1,
        f"{by_type.get('BlockersDeclared', 0)} events",
    )
    ok &= _check(
        "DamageDealt engine events fired (combat resolved)",
        by_type.get("DamageDealt", 0) >= 1,
        f"{by_type.get('DamageDealt', 0)} events",
    )
    return ok


def test_blockers_response_with_omitted_map_is_accepted() -> bool:
    """Regression for the 400 a live 26b run hit at step 189: the model
    expressed "no blocks" by emitting ``{type, decisionId}`` with the
    ``blockers`` field omitted entirely. kotlinx-serialization used to
    reject the missing required field, 400-ing the whole game. The field
    now defaults to empty, so an omitted-map response must be accepted and
    the game must continue past the block step.

    We script the defending player's CHOOSE_BLOCKERS response to a bare
    dict with no ``blockers`` key (the scripted agent backfills decisionId,
    reproducing the exact shape that failed) and assert the run reaches a
    natural stop without an argentum_error.
    """
    print("\ntest_blockers_response_with_omitted_map_is_accepted:")
    with tempfile.TemporaryDirectory() as td:
        result = run_scripted_game(
            persona_a="aria",
            persona_b="bryn",
            deck_a="red_rush",
            deck_b="green_might",
            library_seed=42,
            script_a=[],
            # Every CHOOSE_BLOCKERS for bryn → bare response, no blockers key.
            script_b=[
                DecisionScript(
                    kind="CHOOSE_BLOCKERS",
                    response={"type": "BlockersChosenResponse"},
                    reasoning="[bare no-blocks]",
                )
                for _ in range(20)
            ],
            policy_a=aggressive,
            policy_b=aggressive,
            tmp_root=Path(td),
            max_steps=120,
            verbose=False,
            game_id="combat-bare-blockers",
        )

    ok = True
    ok &= _check(
        "run did not abort with an argentum_error (omitted blockers map accepted)",
        not (result.stop_reason or "").startswith("argentum_error"),
        f"stop_reason={result.stop_reason}",
    )
    # And the bare response actually drove block steps — at least one
    # BlockersDeclared fired despite the omitted map.
    by_type = sum(1 for ev in result.engine_events() if ev.get("type") == "BlockersDeclared")
    ok &= _check(
        "BlockersDeclared still fired with the bare response shape",
        by_type >= 1,
        f"{by_type} BlockersDeclared events",
    )
    return ok


def main() -> int:
    if not argentum.health():
        print(
            f"Argentum gym-server not reachable at {argentum.ARGENTUM_HOST}; "
            "start it with `just gym-server` from the engine repo."
        )
        return 2
    tests = [
        test_combat_step_emits_only_shell_action,
        test_choose_attackers_and_choose_blockers_decisions_fire,
        test_combat_decisions_commit_through_to_damage,
        test_blockers_response_with_omitted_map_is_accepted,
    ]
    all_ok = True
    for t in tests:
        all_ok &= t()
    print()
    print("=" * 60)
    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
