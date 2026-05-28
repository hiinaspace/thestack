"""Entity-anchoring tests for the Phase 3 presentation overhaul.

Validates that ``_build_entity_label_map`` and the structured-decision
prompt's anchor block produce distinct labels for every visible entity —
especially the failure mode where two creatures share a name ("Goblin
Bully" on both sides of the board) and the LLM has to disambiguate by
entityId alone.

These are unit-style tests: they hand-build small obs dicts so the
scenarios are pinned without depending on RNG / a running gym-server.

CLI:
    uv run python -m tests.scripted.test_entity_anchoring
"""

from __future__ import annotations

import sys

from llm.prompts import _build_entity_label_map, format_structured_decision


def _check(label: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


def _mk_obs_with_same_name_creatures() -> dict:
    """Both players control a "Goblin Bully" — the classic disambiguation case."""
    return {
        "perspectivePlayerId": "p-aria",
        "turnNumber": 4,
        "phase": "COMBAT",
        "step": "DECLARE_ATTACKERS",
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
                        "entityId": "ent-aria-bully",
                        "name": "Goblin Bully",
                        "types": ["CREATURE"],
                        "power": 2,
                        "toughness": 2,
                    },
                ],
            },
            {
                "ownerId": "p-bryn",
                "zoneType": "Battlefield",
                "cards": [
                    {
                        "entityId": "ent-bryn-bully",
                        "name": "Goblin Bully",
                        "types": ["CREATURE"],
                        "power": 2,
                        "toughness": 2,
                    },
                ],
            },
        ],
    }


def test_label_map_distinguishes_same_name_creatures() -> bool:
    print("\ntest_label_map_distinguishes_same_name_creatures:")
    obs = _mk_obs_with_same_name_creatures()
    label_map = _build_entity_label_map(obs)

    aria_label = label_map.get("ent-aria-bully")
    bryn_label = label_map.get("ent-bryn-bully")

    ok = True
    ok &= _check("aria's Bully has a label", aria_label is not None, str(aria_label))
    ok &= _check("bryn's Bully has a label", bryn_label is not None, str(bryn_label))
    ok &= _check(
        "the two same-named creatures resolve to distinct labels",
        aria_label != bryn_label,
        f"aria={aria_label!r} bryn={bryn_label!r}",
    )
    ok &= _check(
        "aria's Bully label mentions aria",
        aria_label is not None and "aria" in aria_label,
    )
    ok &= _check(
        "bryn's Bully label mentions bryn",
        bryn_label is not None and "bryn" in bryn_label,
    )
    ok &= _check(
        "both player entities are themselves in the label map",
        label_map.get("p-aria") is not None and label_map.get("p-bryn") is not None,
    )
    return ok


def _mk_obs_with_choose_targets_decision() -> dict:
    """An obs frozen at a CHOOSE_TARGETS prompt for Lightning Bolt. The
    legal-targets block exposes two opposing creatures + the opponent's
    avatar, mirroring the live gym shape."""
    return {
        "perspectivePlayerId": "p-aria",
        "turnNumber": 3,
        "phase": "PRECOMBAT_MAIN",
        "step": "PRECOMBAT_MAIN",
        "players": [
            {"id": "p-aria", "name": "aria"},
            {"id": "p-bryn", "name": "bryn"},
        ],
        "zones": [
            {
                "ownerId": "p-bryn",
                "zoneType": "Battlefield",
                "cards": [
                    {
                        "entityId": "ent-bryn-courser",
                        "name": "Centaur Courser",
                        "types": ["CREATURE"],
                        "power": 3,
                        "toughness": 3,
                    },
                    {
                        "entityId": "ent-bryn-elf",
                        "name": "Llanowar Elves",
                        "types": ["CREATURE"],
                        "power": 1,
                        "toughness": 1,
                    },
                ],
            },
        ],
        "pendingDecision": {
            "decisionId": "decision-1",
            "kind": "CHOOSE_TARGETS",
            "playerId": "p-aria",
            "prompt": "Choose targets for Lightning Bolt",
            "requiresStructuredResponse": True,
            "shape": {},
            "targetRequirements": [
                {
                    "index": 0,
                    "description": "target creature or player",
                    "minTargets": 1,
                    "maxTargets": 1,
                }
            ],
            "legalTargets": {
                "0": [
                    {"entityId": "ent-bryn-courser", "label": "Centaur Courser"},
                    {"entityId": "ent-bryn-elf", "label": "Llanowar Elves"},
                    {"entityId": "p-bryn", "label": "bryn"},
                ]
            },
        },
    }


def test_structured_decision_prompt_renders_entity_anchors() -> bool:
    """The structured-decision prompt should include an explicit anchor
    block mapping every referenced entityId → its board label so the LLM
    can tell ``ent-bryn-courser`` from ``p-bryn`` even if it skipped the
    legal-targets prose."""
    print("\ntest_structured_decision_prompt_renders_entity_anchors:")
    obs = _mk_obs_with_choose_targets_decision()
    prompt = format_structured_decision(obs, "aria")

    ok = True
    ok &= _check("prompt mentions the anchor section", "Entity anchors" in prompt)
    ok &= _check(
        "Centaur Courser entityId resolves to its board location in the anchor block",
        "ent-bryn-courser" in prompt and "Centaur Courser" in prompt,
    )
    ok &= _check(
        "Llanowar Elves entityId resolves to its label",
        "ent-bryn-elf" in prompt and "Llanowar Elves" in prompt,
    )
    ok &= _check(
        "bryn's player entityId resolves to a player-typed label",
        "p-bryn = bryn" in prompt,
    )
    return ok


def main() -> int:
    tests = [
        test_label_map_distinguishes_same_name_creatures,
        test_structured_decision_prompt_renders_entity_anchors,
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
