# personal_tutor — haiku
_Captured 2026-05-25T00:03:06+00:00_

**Validated:** PASS

## Model emission (raw, via submit_decision)
```json
{
  "decisionId": "dec-personal-tutor-1",
  "selectedCards": [
    "L-time-ebb"
  ],
  "type": "CardsSelectedResponse"
}
```

## In-character reasoning attached to the decision
Time Ebb on top of deck. aria's Goblin Bully is her only threat; resetting it buys me a turn to press flying superiority. Hold the answer, keep swinging.

## Out-of-character reflection
## RATING
**4/5** — The decision was clearly structured and I identified the right choice without ambiguity. The three options were distinct, costs were visible, and the JSON shape was specified. Not trivial because of the entity-ID layer, but straightforward enough.

## ROUGH EDGES

- **Entity IDs are opaque.** What does `L-` prefix mean? Why not allow card names as fallback? A player reading "L-time-ebb" has to map it back to "Time Ebb" mentally, adding friction.

- **Prose + structured data mismatch.** The decision prompt mixes conversational text ("Search your library for a sorcery card") with machine-readable fields (Shape, Response shape). It reads like two documents taped together.

- **"Effect hint" is redundant.** I already know what Personal Tutor does; restating "Reveal it, then shuffle and put that card on top" doesn't add new information for this decision.

- **Missing card availability context.** If I have three copies of Wind Drake, do they matter? The prompt doesn't clarify whether quantity affects the decision. (Probably doesn't for search, but it's an ambiguity.)

- **"Shape: maxSelections=1" is jargon.** A player doesn't know if "maxSelections" means "pick exactly one" or "up to one." Plain English ("Pick one card") would be clearer.

- **Response shape buried in code example.** The JSON structure is shown as an example rather than stated: "Reply with a JSON object containing type, decisionId, and selectedCards array." Smaller models will guess the structure instead of following it.

## SMALLER LLM CHECK

- **Entity ID hallucination.** A 4B model would likely invent IDs like `L-brilliance` or `L-time-bouncewave` instead of using the exact provided IDs, breaking the backend.

- **JSON array vs. string confusion.** The selectedCards field expects an array (`["L-time-ebb"]`) but a weaker model might return a string (`"L-time-ebb"`) or a bare card name, failing validation.

- **Forgotten required fields.** A smaller model might drop the `type` or `decisionId` field entirely, or misspell `CardsSelectedResponse` as `CardSelectedResponse`, causing a parse error.
