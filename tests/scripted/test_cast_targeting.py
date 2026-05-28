"""Live-harness test for Phase 1 cast-targeting refactor.

After the engine refactor:
- A ``CastSpell`` action with required targets but ``targets=[]`` no longer
  fails with "No valid targets available"; the engine pauses for a
  ``ChooseTargetsDecision`` via ``CastTargetingContinuation``.
- The gym ``LegalActionExpander`` no longer pre-binds single-target casts;
  the player sees one bare "Cast X" entry and binds targets through the
  structured-decision flow (``/decision/advance``) like every other
  structured decision.

The test drives a real harness game with two ScriptedAgents and asserts
on the resulting game.jsonl events. We deliberately don't pin on a
specific spell name: under any seed where SOMETHING targeted goes on the
stack we want to see the new path used. The assertions instead pair each
cast-action against the engine-event window around it and check that the
new flow (CHOOSE_TARGETS decision + SpellCast-with-target) was used.

CLI:
    uv run python -m tests.scripted.test_cast_targeting
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from llm import argentum

from .policies import aggressive
from .runner import run_scripted_game


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


# Targeted spells in the bundled portal decks. If a cast for one of these
# shows up in the game, we expect to see CHOOSE_TARGETS surface before it.
# Lands, vanilla creatures, and non-targeted sorceries are filtered out.
_TARGETED_SPELL_NAMES = {
    "Volcanic Hammer",
    "Lightning Bolt",
    "Shock",
    "Searing Spear",
    "Monstrous Growth",
    "Giant Growth",
    "Hand of Death",
    "Wicked Pact",
    "Pacifism",
    "Holy Strength",
    "Unsummon",
}


def _spell_name_in_description(desc: str) -> str | None:
    """Extract the spell name from a 'Cast X' or 'Cast X → Y' description."""
    m = re.match(r"^Cast\s+([^→]+?)(?:\s*→.*)?$", desc.strip())
    if not m:
        return None
    return m.group(1).strip()


def test_cast_targeting_via_structured_decision() -> bool:
    """A targeted spell goes through the new CHOOSE_TARGETS path.

    Plays a real game; finds every 'Cast <targeted-spell>' action; for
    each, asserts that an engine CHOOSE_TARGETS decision surfaced before
    the SpellCast event, and that the SpellCast event names the bound
    target. Also asserts no cast description carries the legacy
    "Cast X → Y" arrow suffix (proving the gym kludge is gone).
    """
    print("\ntest_cast_targeting_via_structured_decision:")

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
            max_steps=250,
            verbose=False,
            game_id="cast-targeting",
        )

    # 1) No legacy "Cast X → Y" suffixes anywhere in cast actions.
    arrow_casts = [
        a
        for a in result.actions()
        if (a.get("description") or "").startswith("Cast ") and "→" in (a.get("description") or "")
    ]
    ok = True
    ok &= _check(
        "no cast action carries the legacy 'Cast X → Y' arrow suffix "
        "(gym single-target expansion is gone)",
        not arrow_casts,
        f"saw {len(arrow_casts)} arrow casts: {[a.get('description') for a in arrow_casts[:3]]}",
    )

    # 2) Identify casts of targeted spells.
    cast_actions = [a for a in result.actions() if (a.get("description") or "").startswith("Cast ")]
    targeted_casts: list[tuple[dict, str]] = []
    for a in cast_actions:
        name = _spell_name_in_description(a.get("description") or "")
        if name and name in _TARGETED_SPELL_NAMES:
            targeted_casts.append((a, name))

    ok &= _check(
        "at least one targeted spell was cast during the run (needed to exercise the new path)",
        len(targeted_casts) >= 1,
        f"targeted casts: {[name for _, name in targeted_casts]}",
    )
    if not targeted_casts:
        return ok

    # 3) For each targeted cast, verify the CHOOSE_TARGETS decision +
    # SpellCast-with-target appear after the cast action in the timeline.
    engine_events = result.engine_events()

    verified = 0
    for cast_action, name in targeted_casts:
        cast_seq = cast_action.get("seq", 0)
        # Look for matching engine events after this cast.
        decision_for_spell = next(
            (
                e
                for e in engine_events
                if e.get("_seq", 0) > cast_seq
                and e.get("type") == "DecisionRequested"
                and name in (e.get("text") or "")
                and "Choose targets" in (e.get("text") or "")
            ),
            None,
        )
        spellcast_with_target = next(
            (
                e
                for e in engine_events
                if e.get("_seq", 0) > cast_seq
                and e.get("type") == "SpellCast"
                and name in (e.get("text") or "")
                and "targeting" in (e.get("text") or "")
            ),
            None,
        )
        if decision_for_spell and spellcast_with_target:
            verified += 1
            print(
                f"    {name}: CHOOSE_TARGETS @ seq {decision_for_spell['_seq']} "
                f"→ SpellCast @ seq {spellcast_with_target['_seq']} "
                f"({spellcast_with_target.get('text')})"
            )

    ok &= _check(
        "every targeted cast paired with a CHOOSE_TARGETS decision and a "
        "SpellCast naming the bound target",
        verified == len(targeted_casts),
        f"{verified}/{len(targeted_casts)} casts verified through the new path",
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
        test_cast_targeting_via_structured_decision,
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
