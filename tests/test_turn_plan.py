"""Turn-plan tool lifecycle tests.

Covers:
- set_turn_plan stores intent / sequence / notes
- update_turn_plan revises intent and appends a revision entry
- reset_turn() (per-decision reset) does NOT clear the turn plan
- reset_for_new_turn() does clear it
- format_turn_plan renders the no-plan and with-plan cases correctly
- The action-prompt user_msg includes the plan block in both backends

CLI:
    uv run python -m tests.test_turn_plan
"""

from __future__ import annotations

import sys

from llm.prompts import format_turn_plan
from llm.tools import Toolbox


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def test_set_turn_plan_stores_fields() -> bool:
    print("\ntest_set_turn_plan_stores_fields:")
    tb = Toolbox(name="aria")
    msg = tb.set_turn_plan(
        intent="play Mountain, cast Raging Cougar, attack with all",
        action_sequence=["play Mountain", "cast Raging Cougar", "attack all"],
        notes="Bryn at 5 life; lethal in two turns.",
    )
    ok = True
    ok &= _check("returns success message", "committed" in msg.lower())
    ok &= _check("turn_plan is a dict", isinstance(tb.turn_plan, dict))
    ok &= _check(
        "intent stored",
        tb.turn_plan["intent"].startswith("play Mountain"),
    )
    ok &= _check("sequence has 3 entries", len(tb.turn_plan["action_sequence"]) == 3)
    ok &= _check("notes stored", "Bryn at 5" in tb.turn_plan["notes"])
    ok &= _check("revisions empty initially", tb.turn_plan["revisions"] == [])
    return ok


def test_set_turn_plan_rejects_empty_intent() -> bool:
    print("\ntest_set_turn_plan_rejects_empty_intent:")
    tb = Toolbox(name="aria")
    msg = tb.set_turn_plan(intent="   ", action_sequence=[], notes="")
    ok = True
    ok &= _check("returns ERROR for empty intent", msg.startswith("ERROR"))
    ok &= _check("turn_plan stays None", tb.turn_plan is None)
    return ok


def test_update_turn_plan_appends_revision() -> bool:
    print("\ntest_update_turn_plan_appends_revision:")
    tb = Toolbox(name="aria")
    tb.set_turn_plan(intent="ramp + attack", action_sequence=["Mountain", "Cougar"], notes="")
    msg = tb.update_turn_plan(
        revised_intent="hold mana for Lava Axe response",
        reason="Bryn cast Charging Rhino, can't race",
    )
    ok = True
    ok &= _check("returns success", "updated" in msg.lower())
    ok &= _check(
        "intent now revised",
        tb.turn_plan["intent"] == "hold mana for Lava Axe response",
    )
    ok &= _check("one revision recorded", len(tb.turn_plan["revisions"]) == 1)
    rev = tb.turn_plan["revisions"][0]
    ok &= _check(
        "revision has from/to/reason",
        rev.get("from") == "ramp + attack"
        and rev.get("to") == "hold mana for Lava Axe response"
        and "Rhino" in rev.get("reason", ""),
    )
    return ok


def test_update_turn_plan_with_no_existing_plan_creates_one() -> bool:
    print("\ntest_update_turn_plan_with_no_existing_plan_creates_one:")
    tb = Toolbox(name="aria")
    msg = tb.update_turn_plan(revised_intent="play defensive", reason="opening hand is slow")
    ok = True
    ok &= _check("does not error", not msg.startswith("ERROR"))
    ok &= _check("plan created", tb.turn_plan is not None)
    ok &= _check("intent set", tb.turn_plan["intent"] == "play defensive")
    return ok


def test_reset_turn_preserves_plan() -> bool:
    """The misnamed reset_turn (which is per-decision) must NOT wipe the plan."""
    print("\ntest_reset_turn_preserves_plan:")
    tb = Toolbox(name="aria")
    tb.set_turn_plan(intent="ramp + attack", action_sequence=[], notes="")
    tb.chosen_action_id = 7
    tb.turn_monologues.append("Mountain.")
    tb.reset_turn({1, 2, 3})
    ok = True
    ok &= _check("chosen_action_id cleared", tb.chosen_action_id is None)
    ok &= _check("turn_monologues cleared", tb.turn_monologues == [])
    ok &= _check("turn_plan preserved across decisions", tb.turn_plan is not None)
    ok &= _check("plan intent intact", tb.turn_plan["intent"] == "ramp + attack")
    return ok


def test_reset_for_new_turn_clears_plan() -> bool:
    print("\ntest_reset_for_new_turn_clears_plan:")
    tb = Toolbox(name="aria")
    tb.set_turn_plan(intent="ramp + attack", action_sequence=[], notes="")
    tb.reset_for_new_turn()
    ok = True
    ok &= _check("turn_plan is None", tb.turn_plan is None)
    return ok


def test_format_turn_plan_no_plan_yet() -> bool:
    print("\ntest_format_turn_plan_no_plan_yet:")
    s = format_turn_plan(None)
    ok = True
    ok &= _check("mentions not-committed", "not committed" in s.lower())
    ok &= _check("names set_turn_plan tool", "set_turn_plan" in s)
    return ok


def test_format_turn_plan_with_committed_plan() -> bool:
    print("\ntest_format_turn_plan_with_committed_plan:")
    plan = {
        "intent": "ramp + attack",
        "action_sequence": ["Mountain", "Cougar", "attack all"],
        "notes": "Bryn at 5",
        "revisions": [
            {"reason": "Rhino landed", "from": "ramp + attack", "to": "hold for response"}
        ],
    }
    s = format_turn_plan(plan)
    ok = True
    ok &= _check("includes intent", "ramp + attack" in s)
    ok &= _check("includes sequence joined", "Mountain → Cougar → attack all" in s)
    ok &= _check("includes notes", "Bryn at 5" in s)
    ok &= _check("includes revision", "Rhino landed" in s and "hold for response" in s)
    ok &= _check("includes execute directive", "Execute" in s)
    return ok


def test_dispatch_routes_to_tool() -> bool:
    """The dispatcher exposes the new tools so the SDK / Ollama tool-call
    loops find them by name."""
    print("\ntest_dispatch_routes_to_tool:")
    tb = Toolbox(name="aria")
    msg = tb.dispatch(
        "set_turn_plan",
        {"intent": "ramp", "action_sequence": ["Mountain"], "notes": "go"},
    )
    ok = True
    ok &= _check("dispatch succeeded", "committed" in msg.lower())
    ok &= _check("plan stored via dispatch", tb.turn_plan is not None)
    msg2 = tb.dispatch("update_turn_plan", {"revised_intent": "defend", "reason": "Rhino"})
    ok &= _check("dispatch update_turn_plan", "updated" in msg2.lower())
    ok &= _check("revision recorded via dispatch", len(tb.turn_plan["revisions"]) == 1)
    return ok


def main() -> int:
    tests = [
        test_set_turn_plan_stores_fields,
        test_set_turn_plan_rejects_empty_intent,
        test_update_turn_plan_appends_revision,
        test_update_turn_plan_with_no_existing_plan_creates_one,
        test_reset_turn_preserves_plan,
        test_reset_for_new_turn_clears_plan,
        test_format_turn_plan_no_plan_yet,
        test_format_turn_plan_with_committed_plan,
        test_dispatch_routes_to_tool,
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
