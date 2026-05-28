"""Prompt snapshot tests for the Phase 3 presentation overhaul.

Renders the player-facing prompts (``format_observation``,
``format_recent_public_actions``, ``format_legal_actions``,
``format_structured_decision``) against a frozen deterministic game and
diffs against checked-in ``.snapshot.txt`` files. Catches formatting
regressions — reordering fields, dropping a section, broken truncation —
without paying for an LLM.

Entity IDs are nondeterministic UUIDs (the gym mints fresh ones per env),
so the snapshot pipeline normalizes them to ``<id-N>`` placeholders
keyed by first-appearance order before comparison. The base seed
(``library_seed=42`` + ``skip_mulligans=True``) gives the same game tree
across reruns of the same engine build; player IDs and card entityIds
shift between runs but the normalization keeps the snapshot stable.

Re-recording snapshots: rerun with ``UPDATE_SNAPSHOTS=1`` set.

CLI:
    uv run python -m tests.scripted.test_prompt_snapshots
    UPDATE_SNAPSHOTS=1 uv run python -m tests.scripted.test_prompt_snapshots
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

from llm import argentum
from llm.prompts import (
    format_legal_actions,
    format_observation,
    format_recent_public_actions,
    format_structured_decision,
)

from .policies import aggressive
from .runner import ScriptedRunResult, run_scripted_game

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
UPDATE = bool(os.environ.get("UPDATE_SNAPSHOTS"))


# ---- Snapshot normalization ------------------------------------------------


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _normalize_ids(text: str) -> str:
    """Replace each unique UUID with ``<id-N>`` keyed by appearance order.

    Without this, every snapshot run would differ on every entityId. Player
    IDs and card entityIds are minted by the gym; they're nondeterministic
    even with ``librarySeed`` pinned because the seed only controls the
    library shuffle, not Java's UUID PRNG.
    """
    mapping: dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        s = match.group(0).lower()
        if s not in mapping:
            mapping[s] = f"<id-{len(mapping)}>"
        return mapping[s]

    return _UUID_RE.sub(repl, text)


def _strip_trailing_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()) + ("\n" if text else "")


def _compare_or_write(name: str, rendered: str) -> bool:
    """Compare ``rendered`` against the checked-in snapshot, or write it
    when ``UPDATE_SNAPSHOTS=1`` is set."""
    path = SNAPSHOT_DIR / f"{name}.snapshot.txt"
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    normalized = _strip_trailing_ws(_normalize_ids(rendered))
    if UPDATE or not path.exists():
        path.write_text(normalized)
        print(f"  [WROTE] {path.name} ({len(normalized)} bytes)")
        return True
    expected = path.read_text()
    if expected == normalized:
        print(f"  [PASS] snapshot {path.name} matches ({len(normalized)} bytes)")
        return True
    print(f"  [FAIL] snapshot {path.name} differs. Run with UPDATE_SNAPSHOTS=1 to re-record.")
    _print_diff_preview(expected, normalized)
    return False


def _print_diff_preview(expected: str, actual: str, context: int = 4) -> None:
    e_lines = expected.splitlines()
    a_lines = actual.splitlines()
    # Find first divergence
    n = min(len(e_lines), len(a_lines))
    idx = next((i for i in range(n) if e_lines[i] != a_lines[i]), n)
    start = max(0, idx - context)
    end = min(max(len(e_lines), len(a_lines)), idx + context + 1)
    print("    --- expected ---")
    for i in range(start, min(end, len(e_lines))):
        print(f"    {i:4d} | {e_lines[i]}")
    print("    --- actual ---")
    for i in range(start, min(end, len(a_lines))):
        print(f"    {i:4d} | {a_lines[i]}")


# ---- Game fixture ----------------------------------------------------------


def _run_fixture_game(tmp_root: Path) -> ScriptedRunResult:
    """Same shape as the smoke/block tests: aria vs bryn, seed 42, both
    aggressive. Runs long enough to traverse several main phases + combat,
    so we can snapshot prompts at each interesting beat."""
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
        max_steps=80,
        verbose=False,
        game_id="snapshot",
    )


def _find_obs_at(result: ScriptedRunResult, *, turn: int, phase: str, step: str) -> dict | None:
    """Pull the most-recent OBSERVATION row matching (turn, phase, step)."""
    for ev in reversed(result.events):
        if ev.get("event") != "observation":
            continue
        obs = ev.get("obs") or {}
        if (
            obs.get("turnNumber") == turn
            and (obs.get("phase") or "").upper() == phase.upper()
            and (obs.get("step") or "").upper() == step.upper()
        ):
            return obs
    return None


def _find_first_obs_with_pending_kind(result: ScriptedRunResult, kind: str) -> dict | None:
    for ev in result.events:
        if ev.get("event") != "observation":
            continue
        obs = ev.get("obs") or {}
        pd = obs.get("pendingDecision") or {}
        if pd.get("kind") == kind:
            return obs
    return None


# ---- Tests -----------------------------------------------------------------


def test_observation_snapshot_turn3_precombat() -> bool:
    """The game state at the start of turn 3 precombat: both players have
    lands + one creature, deterministic under seed=42. Exercises
    battlefield + hand + graveyard rendering."""
    print("\ntest_observation_snapshot_turn3_precombat:")
    with tempfile.TemporaryDirectory() as td:
        result = _run_fixture_game(Path(td))
    obs = _find_obs_at(result, turn=3, phase="PRECOMBAT_MAIN", step="PRECOMBAT_MAIN")
    if obs is None:
        return _check("turn-3 precombat obs found", False, "no matching obs")
    rendered = format_observation(obs, "aria")
    return _compare_or_write("observation_t3_precombat", rendered)


def test_legal_actions_snapshot_turn3_precombat() -> bool:
    print("\ntest_legal_actions_snapshot_turn3_precombat:")
    with tempfile.TemporaryDirectory() as td:
        result = _run_fixture_game(Path(td))
    obs = _find_obs_at(result, turn=3, phase="PRECOMBAT_MAIN", step="PRECOMBAT_MAIN")
    if obs is None:
        return _check("turn-3 precombat obs found", False, "no matching obs")
    rendered = format_legal_actions(obs.get("legalActions") or [], obs, "aria")
    return _compare_or_write("legal_actions_t3_precombat", rendered)


def test_choose_attackers_snapshot() -> bool:
    """Captures the structured-decision prompt at a CHOOSE_ATTACKERS pause
    — proves the entity-anchor block + options list render correctly."""
    print("\ntest_choose_attackers_snapshot:")
    with tempfile.TemporaryDirectory() as td:
        result = _run_fixture_game(Path(td))
    obs = _find_first_obs_with_pending_kind(result, "CHOOSE_ATTACKERS")
    if obs is None:
        return _check("CHOOSE_ATTACKERS obs found", False, "no matching obs")
    name = next(
        (p["name"] for p in obs["players"] if p["id"] == obs.get("priorityPlayerId")), "aria"
    )
    rendered = format_structured_decision(obs, name)
    return _compare_or_write("structured_decision_choose_attackers", rendered)


def test_recent_public_actions_snapshot() -> bool:
    """Uses a synthetic 'last action put a spell on the stack' action
    record + an obs whose stack is non-empty, to lock the
    stack-aware ``STILL ON THE STACK`` prelude.

    Synthetic because seed=42 / red_rush vs green_might rarely leaves
    spells on the stack at the moment the harness observes — vanilla
    creatures resolve too fast. The format function doesn't know or care
    that the action came from a real game, so the rendering is identical.
    """
    print("\ntest_recent_public_actions_snapshot:")
    obs = {
        "perspectivePlayerId": "p-aria",
        "turnNumber": 5,
        "phase": "PRECOMBAT_MAIN",
        "step": "PRECOMBAT_MAIN",
        "players": [
            {"id": "p-aria", "name": "aria"},
            {"id": "p-bryn", "name": "bryn"},
        ],
        "zones": [
            {
                "ownerId": "p-aria",
                "zoneType": "Battlefield",
                "cards": [
                    {
                        "entityId": "ent-hammer-victim",
                        "name": "Volcanic Hammer",
                        "types": ["INSTANT"],
                    },
                ],
            },
        ],
        "stack": [
            {
                "entityId": "ent-stack-counterspell",
                "controllerId": "p-bryn",
                "name": "Counterspell",
                "kind": "SPELL",
                "manaCost": "{U}{U}",
                "oracleText": "Counter target spell.",
                "targets": ["ent-hammer-victim"],
            }
        ],
    }
    action_record = {
        "player": "bryn",
        "description": "Cast Counterspell",
        "reasoning": "Don't let that bolt my Gorilla.",
        "table_talk": ["Not so fast."],
        "engine_events": [
            {"type": "SpellCast", "cardNames": ["Counterspell"]},
        ],
        "player_names_by_id": {"p-aria": "aria", "p-bryn": "bryn"},
    }
    rendered = format_recent_public_actions([action_record], obs=obs)
    return _compare_or_write("recent_public_actions_stack_pending", rendered)


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def main() -> int:
    if not argentum.health():
        print(
            f"Argentum gym-server not reachable at {argentum.ARGENTUM_HOST}; "
            "start it with `just gym-server` from the engine repo."
        )
        return 2
    tests = [
        test_observation_snapshot_turn3_precombat,
        test_legal_actions_snapshot_turn3_precombat,
        test_choose_attackers_snapshot,
        test_recent_public_actions_snapshot,
    ]
    all_ok = True
    for t in tests:
        all_ok &= t()
    print()
    print("=" * 60)
    if UPDATE:
        print("SNAPSHOTS RECORDED (re-run without UPDATE_SNAPSHOTS=1 to verify)")
    else:
        print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
