# Rules Engine Investigation Notes

## Summary

Two engines investigated: **Forge** (Java, no Python API) and **Argentum Engine** (Kotlin + HTTP gym-server, Python-friendly). Argentum is the practical path if we want externally-validated legal actions. For Phase 2 we're keeping the in-harness rules engine and deferring integration.

---

## Forge (`~/lib/forge`)

**What it is:** The most complete open-source MTG implementation, with full rules, 25k+ cards, and an AI. Written in Java, Swing/JavaFX GUI.

**Integration verdict: not worth it for Phase 2.**

- No REST or gRPC API. The codebase is a monolithic desktop app.
- Possible paths: fork and add REST (~significant Java dev work, brittle to upstream); subprocess wrapper via stdin/stdout + disk I/O (very fragile).
- The AI lives in `forge-ai` and operates on Forge's internal game model — it can't be surgically extracted.
- Useful as a reference for rules text and card interactions. Clone is at `~/lib/forge`.

---

## Argentum Engine (`~/lib/argentum-engine`)

**What it is:** A Kotlin MTG rules engine purpose-built for Python AI training loops (RL/MCTS). Pure ECS, immutable `GameState`, functional `(GameState, GameAction) -> ExecutionResult`. Has a Spring Boot HTTP gym-server.

**Stack:** Kotlin 2.3 / JDK 21 / Gradle 8 / Spring Boot 4. Build: `just build`. Start gym-server: `just gym-server` (port 8081).

### HTTP API (`/envs`)

The gym-server exposes a clean REST interface:

| Endpoint | What it does |
|---|---|
| `POST /envs` | Create env from deck config, returns opening observation + legal action IDs |
| `GET /envs/{id}` | Get current observation without advancing |
| `POST /envs/{id}/step` | Submit an `actionId` from the last observation, get next observation |
| `POST /envs/step-batch` | Advance N envs in parallel (one action each) |
| `POST /envs/{id}/decision` | Submit structured decisions (ChooseTargets, AssignDamage, etc.) |
| `POST /envs/{id}/fork` | Clone an env cheaply (immutable state = free branching for MCTS) |
| `POST /envs/{id}/snapshot` / `restore` | Persist and restore game states |

**Key design:** Each observation includes a `legalActions` list with opaque integer IDs. The client picks an ID and sends it back via `/step`. The server validates it against the pre-computed legal action set — no client-side legality logic needed.

**Decision protocol:** Uses `PausedForDecision` — the engine runs until a player choice is required, then suspends and returns the decision context with all legal options pre-calculated. The caller resumes by posting the selected option.

### Deck config format

```json
{
  "players": [
    {"name": "Alice", "deck": {"type": "Explicit", "cards": {"Mountain": 17, "Lightning Bolt": 4}}},
    {"name": "Bob",   "deck": {"type": "Explicit", "cards": {"Forest": 17, "Grizzly Bears": 4}}}
  ],
  "skipMulligans": true,
  "perspectivePlayerIndex": 0,
  "revealAll": false
}
```

### Card set coverage

Card sets are registered as Kotlin source (`mtg-sets` module). Portal, Alpha, and Onslaught are present; the Starter Kit 2024 cards are not. Adding a card requires implementing it in Kotlin via the `cardDef { }` DSL (the `add-card` skill handles this). This is the main integration cost.

### Architectural fit

Our current harness polls the engine phase-by-phase and lets the LLM pick from open-ended tool calls. Argentum works inversely: the engine decides when a player needs to act and provides the exact legal action list. Integrating it would mean:

1. The LLM receives `legalActions` (list of action descriptions + IDs) instead of open-ended tools.
2. The LLM picks an action ID and we POST `/envs/{id}/step`.
3. No more need for `dispatch_tool`, `can_cast_spell`, mana auto-tapping, etc. — Argentum handles all legality.

This is strictly better for correctness but requires implementing ~30 Starter Kit cards in Kotlin before we can run a game.

---

## Recommendation

**Phase 2 (current):** Keep the in-harness rules engine. It's good enough for the Starter Kit card pool and the LLMs already reason about it via the system prompt.

**Phase 3:** If LLM move quality is limited by illegal-action noise, integrate Argentum:
1. Implement Starter Kit cards in `~/lib/argentum-engine` using the `add-card` skill.
2. Replace `dispatch_tool` calls with HTTP calls to the gym-server.
3. LLM prompt changes: present `legalActions` list instead of full tool schema.

The MCTS fork API (`/envs/{id}/fork`) is a bonus — it would allow running shallow tree search to filter obviously bad LLM suggestions before committing.

**Forge:** Reference only. Don't attempt integration.
