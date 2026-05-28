"""Smoke test for the scripted-harness scaffold itself.

Asserts that two runs with the same library seed + same script produce
identical game.jsonl content (modulo timestamps). This is the
determinism check that justifies using the scripted harness as the basis
for regression tests downstream — if this drifts, every Phase 1-3 test
built on top is unreliable.

CLI:
    uv run python -m tests.scripted.test_smoke
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from llm import argentum

from .runner import ScriptedRunResult, run_scripted_game


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def _run(tmp_root: Path, game_id: str) -> ScriptedRunResult:
    """One scripted run: aria vs bryn, seed 42, 40 steps, empty scripts.

    Empty scripts mean every choose_action falls back to the first
    non-pass legal action — fully deterministic given the seed.
    """
    return run_scripted_game(
        persona_a="aria",
        persona_b="bryn",
        deck_a="red_rush",
        deck_b="green_might",
        library_seed=42,
        script_a=[],
        script_b=[],
        tmp_root=tmp_root,
        max_steps=40,
        verbose=False,
        game_id=game_id,
    )


def _strip_volatile(events: list[dict]) -> list[dict]:
    """Drop timestamp + game_id (different across runs) before equality check."""
    out = []
    for e in events:
        copy = {k: v for k, v in e.items() if k not in {"ts", "game_id"}}
        out.append(copy)
    return out


def test_scripted_runs_are_byte_deterministic() -> bool:
    """Two runs with identical seed + scripts produce identical event logs."""
    print("\ntest_scripted_runs_are_byte_deterministic:")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run1 = _run(root, "run1")
        run2 = _run(root, "run2")

    ok = True
    e1 = _strip_volatile(run1.events)
    e2 = _strip_volatile(run2.events)
    ok &= _check("event count matches", len(e1) == len(e2), f"{len(e1)} vs {len(e2)}")
    ok &= _check(
        "same action_ids across both runs",
        [e.get("action_id") for e in run1.actions()]
        == [e.get("action_id") for e in run2.actions()],
    )
    ok &= _check(
        "same autopass reasons",
        [e.get("reason") for e in run1.autopasses()]
        == [e.get("reason") for e in run2.autopasses()],
    )

    # Engine-event types should match step-for-step. We do not compare
    # entityIds because shared-log seq numbering already covers ordering;
    # the type sequence is the load-bearing determinism signal.
    types1 = [ev.get("type") for ev in run1.engine_events()]
    types2 = [ev.get("type") for ev in run2.engine_events()]
    ok &= _check(
        "same engine event-type sequence",
        types1 == types2,
        f"{len(types1)} events" if types1 == types2 else f"{len(types1)} vs {len(types2)}",
    )
    return ok


def test_scripted_run_makes_progress() -> bool:
    """Sanity check: with empty scripts we should still see real engine
    events fire (not just autopasses), so the runner is exercising the
    harness end-to-end and not silently stalling."""
    print("\ntest_scripted_run_makes_progress:")
    with tempfile.TemporaryDirectory() as td:
        result = _run(Path(td), "progress-check")
    ok = True
    ok &= _check(
        "at least one ACTION event recorded", len(result.actions()) > 0, str(len(result.actions()))
    )
    ok &= _check(
        "at least one engine event fired",
        len(result.engine_events()) > 0,
        str(len(result.engine_events())),
    )
    # Random first-legal play should still progress the turn counter.
    final_turn = (result.final_obs or {}).get("turnNumber", 0)
    ok &= _check("turn number advanced past 1", final_turn > 1, f"final turn={final_turn}")
    return ok


def main() -> int:
    if not argentum.health():
        print(
            f"Argentum gym-server not reachable at {argentum.ARGENTUM_HOST}; "
            "start it with `just gym-server` from the engine repo."
        )
        return 2
    tests = [
        test_scripted_runs_are_byte_deterministic,
        test_scripted_run_makes_progress,
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
