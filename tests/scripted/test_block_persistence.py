"""Live-harness block-persistence test.

Migration of ``tests/test_no_progress_fallback.py`` to the scripted-agent
pattern ([[feedback-live-harness-tests]]). The original test pinned a
captured T12 obs and exercised the no-progress *gate* logic in isolation
— useful, but brittle when the engine's decision flow changes (Phase 2 of
``deep-honking-gadget`` rewrites combat declaration entirely).

This version drives the real ``run_game.run_game()`` loop with
aggressive ScriptedAgents that actually attack and block, then asserts on
engine events that real blocker assignments commit and no no-progress
autopass fires on a block step. Aligns with [[plan-deep-honking-gadget]]
Phase 0.3 and survives the Phase 2 combat-decision refactor that retires
the gate entirely.

CLI:
    uv run python -m tests.scripted.test_block_persistence
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from llm import argentum

from .policies import aggressive
from .runner import ScriptedRunResult, run_scripted_game


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def _run_aggressive_game(tmp_root: Path, game_id: str = "block-persistence") -> ScriptedRunResult:
    """Aria (red rush) vs Bryn (green might), seed 42, both aggressive.

    Combat reliably fires around T4-T5 under this seed/decks/policy; the
    first DamageDealt event is at engine seq ~371 with one block
    actually happening (Gorilla Warrior blocks Hulking Goblin).
    """
    return run_scripted_game(
        persona_a="aria",
        persona_b="bryn",
        deck_a="red_rush",
        deck_b="green_might",
        library_seed=42,
        script_a=[],
        script_b=[],
        policy_a=aggressive,
        policy_b=aggressive,
        tmp_root=tmp_root,
        max_steps=200,
        verbose=False,
        game_id=game_id,
    )


def test_real_block_commits_through_harness() -> bool:
    """Run a real game where blockers happen; assert the engine actually
    records combat damage including blocker↔attacker exchanges (not just
    unblocked face-damage)."""
    print("\ntest_real_block_commits_through_harness:")
    with tempfile.TemporaryDirectory() as td:
        result = _run_aggressive_game(Path(td))

    damage_events = [e for e in result.engine_events() if e.get("type") == "DamageDealt"]
    creature_to_creature = [e for e in damage_events if "Player" not in (e.get("text") or "")]
    return _check(
        "at least one creature-to-creature damage event (i.e. a real block exchanged damage)",
        len(creature_to_creature) > 0,
        f"{len(creature_to_creature)} creature↔creature damage events; "
        f"{len(damage_events)} total damage events",
    )


def test_no_no_progress_autopass_on_block_step() -> bool:
    """If the no-progress fallback ever fired with reason mentioning
    'no progress' on a block submission, it'd show up as an autopass
    event. The current gate (``should_track_no_progress``) prevents this
    by returning False at DECLARE_BLOCKERS. After Phase 2 retires the
    gate, the StateDigest fix should keep this property — same test,
    different mechanism.
    """
    print("\ntest_no_no_progress_autopass_on_block_step:")
    with tempfile.TemporaryDirectory() as td:
        result = _run_aggressive_game(Path(td))

    offenders = [e for e in result.autopasses() if "no progress" in (e.get("reason") or "").lower()]
    return _check(
        "no autopass fired with reason 'no progress after prior action'",
        len(offenders) == 0,
        f"offenders: {offenders[:3]}" if offenders else "",
    )


def test_aggressive_policy_actually_attacks_and_blocks() -> bool:
    """Sanity: the aggressive policy is doing what we think it is, so
    the assertions above are exercising a real combat code path and not
    quietly testing 'nothing happens'.

    Post-Phase-2 the harness no longer emits per-attacker / per-blocker
    flat actions; combat goes through ``CHOOSE_ATTACKERS`` /
    ``CHOOSE_BLOCKERS`` structured decisions. The signals here read the
    engine event stream and the structured-response side files.
    """
    print("\ntest_aggressive_policy_actually_attacks_and_blocks:")
    with tempfile.TemporaryDirectory() as td:
        result = _run_aggressive_game(Path(td))

    events = result.engine_events()
    attackers_declared = [e for e in events if e.get("type") == "AttackersDeclared"]
    blockers_declared = [e for e in events if e.get("type") == "BlockersDeclared"]
    ok = True
    ok &= _check(
        "at least one AttackersDeclared engine event fired",
        len(attackers_declared) > 0,
        str(len(attackers_declared)),
    )
    ok &= _check(
        "at least one BlockersDeclared engine event fired",
        len(blockers_declared) > 0,
        str(len(blockers_declared)),
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
        test_aggressive_policy_actually_attacks_and_blocks,
        test_real_block_commits_through_harness,
        test_no_no_progress_autopass_on_block_step,
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
