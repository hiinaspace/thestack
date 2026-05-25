"""Regression test for the no-progress fallback overwriting valid blocker actions.

Bug observed in games/haiku-vs-e4b-002 turn 12 (seq 2116-2125): aria submitted
action 5 "Block as many as possible (2 pairs)" during DECLARE_BLOCKERS facing
two attackers; the engine's stateDigest was invariant across the step, so
run_game.py's no-progress detection flagged the position; on the next
iteration no_progress_fallback_action_id forced "No blocks", wiping aria's
blockers and killing her.

Fix: gate the no-progress *detection* (run_game.py:357-363) with
``llm.autopass.should_track_no_progress(obs)``, which returns False during
DECLARE_BLOCKERS / DECLARE_ATTACKERS / COMBAT_DAMAGE — steps where the
digest is known to be unreliable.

CLI:
    uv run python -m tests.test_no_progress_fallback
"""

from __future__ import annotations

import sys

from llm.autopass import should_track_no_progress
from run_game import no_progress_fallback_action_id
from tests.scenario_runner import SCENARIO_ROOT, load_scenario

BLOCK_BUG_FIXTURE = SCENARIO_ROOT / "block_bug_t12.json"


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def test_block_bug_fixture_reproduces_digest_invariance() -> bool:
    """The captured digests confirm the bug shape: three identical digests
    across a real block submission and a No-blocks fallback. Without the fix,
    digest equality would (and historically did) trip the no-progress flag."""
    scenario = load_scenario(BLOCK_BUG_FIXTURE)
    obs_pre = scenario["obs_pre_action"]
    obs_post_block = scenario["obs_post_block_action"]
    obs_post_noblock = scenario["obs_post_noblock_fallback"]

    print("\ntest_block_bug_fixture_reproduces_digest_invariance:")
    ok = True
    ok &= _check(
        "pre/post-block digests identical (bug precondition)",
        obs_pre["stateDigest"] == obs_post_block["stateDigest"],
        f"pre={obs_pre['stateDigest'][:16]}  post={obs_post_block['stateDigest'][:16]}",
    )
    ok &= _check(
        "post-block / post-noblock digests identical (fallback also no-op)",
        obs_post_block["stateDigest"] == obs_post_noblock["stateDigest"],
    )
    ok &= _check(
        "step is DECLARE_BLOCKERS throughout",
        obs_pre.get("step") == obs_post_block.get("step") == "DECLARE_BLOCKERS",
    )
    return ok


def test_should_track_no_progress_skips_combat_declaration_steps() -> bool:
    """The helper returns False during the unreliable-digest steps so the
    flag never gets set in the first place."""
    print("\ntest_should_track_no_progress_skips_combat_declaration_steps:")
    ok = True
    for step in ("DECLARE_BLOCKERS", "DECLARE_ATTACKERS", "COMBAT_DAMAGE"):
        ok &= _check(
            f"returns False for step={step}",
            should_track_no_progress({"step": step}) is False,
        )
    return ok


def test_should_track_no_progress_default_true_for_other_steps() -> bool:
    """Default behavior preserved for everything else."""
    print("\ntest_should_track_no_progress_default_true_for_other_steps:")
    ok = True
    for step in ("MAIN_PRECOMBAT", "MAIN_POSTCOMBAT", "UPKEEP", "DRAW", "END_OF_TURN", "CLEANUP"):
        ok &= _check(
            f"returns True for step={step}",
            should_track_no_progress({"step": step}) is True,
        )
    ok &= _check(
        "returns True for missing step",
        should_track_no_progress({}) is True,
    )
    return ok


def test_block_bug_fix_prevents_fallback_firing() -> bool:
    """Integration: with the fix in place, the no-progress flag is never
    added for the captured DECLARE_BLOCKERS sequence, so the fallback
    function returns None even when asked about the same (digest, name)."""
    print("\ntest_block_bug_fix_prevents_fallback_firing:")
    scenario = load_scenario(BLOCK_BUG_FIXTURE)
    obs_pre = scenario["obs_pre_action"]
    obs_post_block = scenario["obs_post_block_action"]
    acting_name = scenario["_meta"]["perspective_player_name"]

    no_progress_positions: set[tuple[str, str]] = set()
    previous_digest = obs_pre.get("stateDigest")
    if (
        previous_digest
        and obs_post_block.get("stateDigest") == previous_digest
        and not obs_post_block.get("terminated")
        and should_track_no_progress(obs_post_block)
    ):
        no_progress_positions.add((str(previous_digest), acting_name))

    ok = True
    ok &= _check(
        "no_progress_positions stays empty under the fix",
        len(no_progress_positions) == 0,
        f"contents: {no_progress_positions!r}",
    )
    ok &= _check(
        "no_progress_fallback_action_id returns None on the post-block obs",
        no_progress_fallback_action_id(obs_post_block, acting_name, no_progress_positions) is None,
    )
    return ok


def test_fallback_still_fires_for_genuine_repeats_outside_combat() -> bool:
    """Sanity: the fallback's original purpose — defending against actually
    stuck positions during non-combat steps — still works."""
    print("\ntest_fallback_still_fires_for_genuine_repeats_outside_combat:")
    fake_obs = {
        "step": "MAIN_PRECOMBAT",
        "stateDigest": "deadbeef",
        "legalActions": [
            {"actionId": 0, "description": "Pass priority"},
            {"actionId": 1, "description": "Cast Goblin Bully"},
        ],
    }
    no_progress_positions = {("deadbeef", "aria")}
    ok = True
    result = no_progress_fallback_action_id(fake_obs, "aria", no_progress_positions)
    ok &= _check(
        "fallback returns Pass priority when state matches a flagged position",
        result is not None and result[0] == 0,
        f"got {result!r}",
    )
    return ok


def main() -> int:
    tests = [
        test_block_bug_fixture_reproduces_digest_invariance,
        test_should_track_no_progress_skips_combat_declaration_steps,
        test_should_track_no_progress_default_true_for_other_steps,
        test_block_bug_fix_prevents_fallback_firing,
        test_fallback_still_fires_for_genuine_repeats_outside_combat,
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
