# forked_lightning_three_targets — haiku
_Captured 2026-05-25T00:02:30+00:00_

**Validated:** PASS

## Model emission (raw, via submit_decision)
```json
{
  "decisionId": "dec-forked-lightning-1",
  "distribution": {
    "C-elf-ranger-1": 1,
    "C-lion-1": 1,
    "C-treefolk-1": 2
  },
  "type": "DistributionResponse"
}
```

## In-character reasoning attached to the decision
Kill the Jungle Lion and Elvish Ranger—both 1-toughness threats that trade for 1 damage each. The Ranger's 4 power is the bigger problem if it stays on the board. Rowan Treefolk takes 2, leaving it at 1/4. Clears the pressure, leaves me room to swing.

## Out-of-character reflection
RATING: 4/5

The decision presentation is well-structured and mostly unambiguous. Entity IDs are provided clearly, constraints are listed, and the response format is shown. A human reads this without much friction.

ROUGH EDGES:

- The response template shows `"target-entity-id":1` as a placeholder, but it's not immediately obvious that you're supposed to substitute the actual IDs (e.g., `C-lion-1`) in place of that string. A smaller model could literally output `"target-entity-id"` as the key.

- The constraint "min positive 0, max 4" is grammatically awkward. Zero is not positive. This could confuse a smaller model about whether 0 damage is actually allowed, or it might misparse what "positive" means here.

- The line `minSelections=1, maxSelections=3` is clear, but the phrasing doesn't explicitly call out that you're choosing *how many creatures to target* (not that all creatures are mandatory). A 4B model might default to hitting all 3 targets unnecessarily.

- No explicit global constraint stated: "the sum of all damages must equal 4." Each target shows a "max 4" but the interaction between targets (that they're a constrained pool) is left implicit in the Shape notation rather than spelled out as a rule.

- The mana costs in the target list (`{G}`, `{2}{G}`, `{3}{G}`) add visual clutter; they're irrelevant to the distribution decision and might distract.

- The response template shows only one target in the example; it doesn't illustrate the full syntax with multiple keys in the distribution object.

SMALLER LLM CHECK:

- **Entity ID typos**: Model outputs `C-lion` or `lion-1` instead of the exact ID `C-lion-1`. Parser rejects it silently.

- **Distribution totals wrong**: Model outputs `1+1+3=5` or `2+2=4` (leaving one creature out), violating the "exactly 4 across all targets" constraint.

- **JSON nesting error**: Model renders distribution as an array `[{"C-lion-1": 1}, ...]` instead of a flat object, or invents extra nesting layers that break deserialization.
