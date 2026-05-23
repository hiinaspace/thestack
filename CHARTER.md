# The Stack — LLM Magic: The Gathering Arena

> *A harness for watching language models argue about spells, make questionable tactical decisions, and occasionally appeal to a judge who may also be wrong.*

**Working title:** The Stack (the MTG mechanic; also the system's layered architecture)
**Status:** Phase 3 — persona harness pivot (in progress)
**Primary goal:** Entertainment and research artifact, not performance optimization

---

## Purpose

This project builds an open harness for running Magic: The Gathering games driven by large language models — locally, visually, and with the LLM reasoning traces exposed as the main content. The intent is:

- **LLM vs LLM** games where natural language is the primary interaction channel between agents
- **Human vs LLM** games where the human writes actions in plain English
- **A judge LLM** that watches over play, resolves disputes, and looks up rules/errata when invoked — and may occasionally get it wrong in entertaining ways
- **A commentator LLM** that watches the game from a spectator perspective, providing tournament-style play-by-play and color commentary without knowledge of either player's hidden hand
- A **web spectator client** for watching live games and scrubbing through past game replays turn by turn
- A **publishable, reusable harness** that others can run locally against their own models or use as a base for other card/board game experiments

The project sits at the intersection of LLM game-playing research, generative commentary, and entertainment. Performance (winning) is explicitly not the goal. The interesting content is the reasoning trace: watching an LLM deliberate over an attack, misread a card, get corrected by the judge, or develop a read on its opponent's archetype across multiple games.

---

## What This Is Not

- A competitive MTG bot
- A replacement for MTGO, Arena, or XMage as a playable client
- A benchmark or evaluation framework (though it could be adapted into one)
- Dependent on cloud API tokens to run — local-first is a hard requirement
- An attempt to solve rules enforcement from scratch — we offload legality to an existing engine where practical

---

## Prior Art and Context

### LLM game-playing

**Claude Plays Pokémon / LLM agentic harnesses** — The direct ancestor. The entertainment value came not from performance but from exposing the LLM's chain-of-thought: its uncertainty, mistakes, and self-correction. This project extends that framing to a multi-agent competitive context where two LLMs can observe and contest each other's reasoning.

**Voyager (Wang et al., 2023)** — LLM-powered lifelong agent in Minecraft that uses code as its action space and builds a growing skill library. Established that "LLM writes/codes actions" is a viable alternative to direct button-press control for turn-based environments.

**RL-GPT (NeurIPS 2024)** — Two-level hierarchical framework: slow LLM decomposes tasks, fast LLM/RL agent executes. Demonstrated clean separation between strategic reasoning (LLM) and low-level execution (learned policy). The Stack uses a simpler version of this split: LLM reasons and calls structured tools; the harness handles legality and rendering.

**EUREKA (Ma et al., 2023)** — LLM generates reward functions that train RL agents, using environment source code as context. Relevant as a pattern for "LLM shapes the training process" rather than directly playing. Future direction: LLM-designed reward functions for fine-tuning smaller game-playing policies.

**HLA — Hierarchical Language Agent (AAMAS 2024)** — Three-tier architecture (Slow Mind LLM → Fast Mind lightweight LLM → reactive Executor) for real-time human-AI coordination in Overcooked. The closest published work to the "LLM coach + fast policy" structure this project may eventually extend toward for real-time game variants.

**LLM-based Explicit Models of Opponents / EMO (NAACL 2025)** — LLM maintains and iteratively refines an explicit model of each opponent, using it for decision-making. Directly relevant to the between-game deck adjustment and opponent archetype modeling that makes multi-game sessions interesting.

### Social/multi-agent LLM games

**LLM Werewolf/Mafia/Diplomacy experiments** — Established that LLMs can sustain multi-agent social reasoning over structured game turns, including bluffing, coalition-forming, and accusation. MTG has analogous social dynamics: bluffing about hand contents, representing mana, sandbagging threats.

**Generative Agents / Virtual Town (Park et al., 2023)** — LLM-driven agents with persistent memory competing and cooperating over resources. The multi-game session structure here (deck adjustment between games, opponent modeling across matches) is in this tradition.

### Generative commentary

**Sports commentary generation research (2024–2025)** — LLMs generating event-driven commentary from structured game state feeds. Current work is mostly template-driven narration over structured events; it lacks the inferential player modeling that good commentary actually requires ("he's been conditioning that block for three turns"). The Stack's commentator LLM is an attempt to do this properly: it has access to the full public game state and transcript, but not hidden hands, and is prompted to reason about player intent, archetype reads, and narrative arc — not just announce what happened.

**IBM ProsodyLM / Wimbledon tennis commentary (2025)** — End-to-end pipeline from game events through LLM script generation to neural TTS with prosody modeling. Demonstrates production-quality automated sports commentary. Text-to-speech is out of scope for v1 but a natural extension.

### MTG virtual tabletop landscape

Several tools exist for web-based MTG goldfishing/virtual play without rules enforcement. None are ideal for direct adaptation as a bot harness:

**Moxfield playtest mode** — The most polished goldfish tool; widely used with OBS for Spelltable sessions. Closed source. Its UI is the reference point for what the visual layer should feel like.

**untap.in** — Browser-based, no-download virtual tabletop supporting MTG and other CCGs. Handles deck import, card placement, and multiplayer sessions without rules enforcement — the closest existing tool to what The Stack needs. Appears closed-source; protocol not publicly documented.

**sboulema/mtgGoldfish** — Simple open-source JavaScript goldfish tool. Single player, handles deck import and draw simulation. No two-player board or WebSocket sync, but a reasonable starting point for the visual layer.

**Cockatrice** — The reference desktop implementation: open-source C++, card art via Scryfall, no rules enforcement, multiplayer via self-hosted server. Its protocol is documented and has been the basis for bot clients before, but it's a desktop app rather than web-native.

**Assessment** — No existing open-source web-based two-player MTG board has a clean bot API. The practical path is to build a thin custom board (WebSocket server + HTML canvas renderer, Scryfall art, drag/tap/zone primitives) rather than adapting any existing tool. This is not much work given that rules enforcement is handled separately, and it means the board API can be designed around what the LLM tool calls need.

### Replay and spectator interfaces for LLM games

**Generative Agents / Smallville (Park et al., 2023)** — The original repo ships a web replay frontend: simulation state is written to an append-only storage directory, a Django server replays it on demand, and the browser renders world state at step N via a Phaser.js tile map. The Claudeville fork (Claude-ported version) extended this with play/pause controls, a "skip to next LLM decision" mode that fast-forwards through non-decision steps, and a 2–3 step lookahead buffer for smooth playback. The core pattern — JSONL/append-only event log → frontend requests step N → renders state — is the right model for The Stack's replay client, with "turn N" substituting for "timestep N."

**Readable Minds: Emergent Theory-of-Mind in LLM Poker Agents (2025)** — The closest prior art to what The Stack's spectator interface should look like. Built a real-time spectator view showing each agent's cards, chips, and table position; a Theory of Mind panel displaying live ToM-level assessments for each agent; and agent memory notes rendered as speech bubbles showing the natural-language opponent models each pilot maintains. A statistics panel tracked behavioral metrics (VPIP, PFR, aggression) across the session. The speech-bubble rendering of "what each agent currently thinks about its opponent" maps directly to the pilot opponent model display in The Stack.

**PokerBattle AI / annotated hand histories (poker.org, 2025)** — Published post-tournament "annotated hand history" blog posts: LLM reasoning verbatim per decision, followed by human editorial analysis of each choice. This is the hybrid prose format — transcript interleaved with annotation — that the commentator LLM output should approximate. Also notable: LLMs explaining full reasoning per action is slow for poker but is the feature, not the bug, for MTG where deliberation time is unlimited and reasoning quality is the content.

**Agent Flow (patoles, 2025)** — Real-time visualizer for Claude Code agent orchestration. Streams JSONL event logs to a browser via WebSocket, with a timeline panel, file attention heatmap, and message transcript side-by-side. JSONL log files can also be loaded for replay of past sessions. The JSONL event log → SSE/WebSocket → React frontend pattern is a direct implementation reference for The Stack's spectator client. "Skip to next action" is a key UX feature worth carrying over.

**Chess game annotation / PGN format** — The longest-established format for serializing annotated game transcripts. PGN interleaves moves with freetext commentary in a standardized way; tools like python-chess-annotator add engine evaluations per move. The "evaluation bar" (advantage graph over the course of the game) is the key UX concept to adapt: for MTG, a card-advantage / board-presence curve scrubbed over turns gives spectators a narrative arc before drilling into any specific decision.

**Synthesis for The Stack** — The replay client should combine: Smallville's step-scrubber architecture (JSONL log → step-N rendering), Readable Minds' agent-annotation panels (opponent model, reasoning notes as overlay), PokerBattle's annotated-transcript publication format (commentator + editorial layer), Agent Flow's live-streaming + stored-replay duality, and chess's evaluation graph adapted to MTG board metrics. None of these exist for a card game with hidden state; the combination is novel.

### MTG-specific AI

**deep_mtg** — LLM system for MTG deck construction using embedding-based card retrieval and a reasoning model for theme coherence. Covers deck building; does not tackle game play.

**UrzaGPT** — LoRA-tuned LLM for card selection in CCG drafting contexts. Fine-tuning direction for improving local model play quality.

**XMage / Forge** — The two main open-source MTG rules engines. XMage has a client-server architecture that exposes game state and legal actions per player, making it a natural rules authority. Forge has an extensible Java architecture with existing AI hooks. Both can serve as the legality oracle that The Stack defers to. A unified MCP server composing Scryfall, Commander Spellbook, EDHREC, and Moxfield data (community project, GitHub) is also a useful reference for the tool surface an LLM MTG agent needs.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                       PYTHON HARNESS (thestack/)                       │
│                                                                        │
│  ┌────────────────────┐  natural-language     ┌────────────────────┐   │
│  │  PersonaAgent A    │  reasoning + tool     │  PersonaAgent B    │   │
│  │  (one Ollama       │  calls per decision   │  (one Ollama       │   │
│  │   conversation     │ ◄───── shared ──────► │   conversation     │   │
│  │   across the game) │  spectator transcript │   across the game) │   │
│  └─────────┬──────────┘                       └──────────┬─────────┘   │
│            │  submit_action(id, reasoning)               │             │
│            │  + take_note / recall_strategy              │             │
│            ▼                                             ▼             │
│            ┌──────────────────────────────────────────────┐            │
│            │  Argentum gym-server (Kotlin, REST :8081)    │            │
│            │  — rules engine, legality oracle,            │            │
│            │    canonical state, hidden info,             │            │
│            │    legalActions per decision point           │            │
│            └──────────────────────┬───────────────────────┘            │
│                                   │ observations                       │
│            ┌──────────────────────┴───────────────────────┐            │
│            │  Commentator (separate persistent agent)     │            │
│            │  Reflector (post-game; writes persona memory)│            │
│            └──────────────────────┬───────────────────────┘            │
│                                   ▼                                    │
│                       games/{id}.jsonl event log                       │
│                                   │                                    │
└───────────────────────────────────┼────────────────────────────────────┘
                                    ▼
                     ┌─────────────────────────────┐
                     │  Replay viewer (FastAPI +   │
                     │  vanilla HTML)              │
                     │  • board + scrubber         │
                     │  • per-persona thoughts     │
                     │  • commentary track         │
                     │  • persona memory snapshot  │
                     └─────────────────────────────┘
```

**Rules engine** — Argentum gym-server (`/home/s/lib/argentum-engine`) is the
sole source of truth for legality, zones, hidden info, and the stack. We talk
to it via plain REST (`POST /envs`, `POST /envs/{id}/step`, etc.). The Python
harness intentionally has no rules logic of its own.

**Player agents** — each `PersonaAgent` holds ONE Ollama conversation that
persists across every decision in a game. State accumulates: prior reasoning,
prior tool calls (`take_note` scratchpad), the running game transcript. There
is no per-decision prompt rebuild. Agents commit to legal actions via the
`submit_action` tool; the engine validates.

**Commentator** — separate persistent agent, public-state only, narrates each
turn with awareness of previous turns it has already narrated.

**Personas (Phase B)** — named identities with markdown files for cross-game
memory (`personas/<name>/identity.md`, `memory.md`, `opponents.md`,
`strategy.md`). Pre-game strategy pass reads the persona's notes; post-game
reflector writes new ones.

**Replay viewer (Phase C)** — FastAPI + vanilla HTML/JS. Reads the JSONL event
log; shows board state at any step, per-persona thinking/reasoning tabs,
commentary track, and the persona memory snapshot as it was at game start.

---

## Interaction Model

Each `PersonaAgent` is a single Ollama conversation that lasts the whole game.
The harness feeds it a new user turn each time Argentum yields control:

```
User:  <game state at turn 3, combat step>
       <numbered legal actions>
       Choose one and call submit_action.

Tools available to the player agent:
  take_note(note)         — append a strategic note to the persistent scratchpad
  recall_strategy()       — read back every note saved so far
  submit_action(id, why)  — commit to one legal action and end this decision
                            (the `why` text is the in-character spectator line)
```

Because each agent is a single conversation, by turn 5 it has the full text of
its own deliberations from turns 1-4, every action it took, and every tool
result it received. That is the "running context" the entertainment value
depends on.

---

## Commentator System

The commentator LLM runs with a system prompt framing it as a tournament coverage analyst — think competitive MTG coverage with a color commentator and a play-by-play announcer, collapsed into one voice. Key constraints:

- **No access to hidden hands.** It can speculate ("A seems to be holding up mana, possibly Counterspell or a combat trick") but cannot know.
- **Receives the player transcript.** So it can observe when a player announced a plan and then deviated, or when one player's stated reasoning turned out to be mistaken.
- **Updated per turn** with a brief for that turn's events plus the board state delta. Produces a paragraph or two of commentary, saved to the event log alongside the turn.
- **Develops a narrative arc** across a game: who's ahead, who's been adapting, what the critical turning point was. The between-game session summary is partly built from the commentator's arc notes.

This is the component closest to the gap in existing sports commentary research: it does actual inferential player modeling rather than template narration over events.

---

## Multi-Game Session Structure

Personas have markdown files that carry across games (target of Phase B):

```
personas/
  aria/
    identity.md     # name, voice, playstyle — fixed personality
    memory.md       # rolling log of past games (LLM-curated)
    opponents.md    # what Aria remembers about specific opponents
    strategy.md     # current-deck strategy notes
  bryn/
    …
```

Per-game flow:
1. **Pre-game**: agent reads `identity.md` + `memory.md` + `opponents.md` (the
   relevant opponent entry) + their deck list. Writes a fresh `strategy.md`
   block for this game.
2. **In-game**: as described above — one persistent Ollama conversation per
   agent, scratchpad via `take_note`.
3. **Post-game**: a Reflector pass takes the full game JSONL + scratchpad +
   existing memory files and writes structured updates back into
   `memory.md` / `opponents.md`. The persona is now "smarter" for next game.

Markdown is the chosen format because it is human-inspectable and
hand-editable — operator can read what the AI thinks it knows, and tweak the
persona's voice or facts between sessions if desired.

---

## Replay and Spectator Web Client

Since game state is structured data (an append-only event log), no video recording is needed. The web client:

- **Live view**: connects to the event stream via SSE or WebSocket; board, transcript, and commentator output update in real time
- **Replay scrubber**: for completed games, renders any turn on demand — board state at that moment, the active player's reasoning trace for that turn, commentator paragraph, any judge rulings
- **Reasoning trace overlay**: each card play or action is annotated with the LLM's stated reasoning; clicking a turn shows the full chain-of-thought
- **Shareable permalinks**: games identified by UUID; any turn reachable by URL
- **Session view**: shows the arc across multiple games with the commentator's between-game summaries

This is a materially better format than a video stream for this content: searchable, zoomable to any decision point, and the reasoning trace is the commentary track rather than a separate overlay.

---

## Local-First Constraint

Designed to run entirely on local hardware (reference: RTX 4090, Linux):
- Primary models: quantized 70B-class (Qwen2.5 72B Q4, Llama 3.3 70B Q4, DeepSeek-R1 distill variants) via Ollama or llama.cpp
- No mandatory cloud API dependency; cloud models are an optional upgrade path
- Token budget per game effectively zero — run overnight experiments freely
- Published as a standalone tool; users bring their own models via OpenAI-compatible API endpoint

---

## Entertainment and Publishing Goals

- Reasoning traces logged verbatim and formatted for readability
- Spectator web client as the primary viewing interface (not OBS/video)
- Session summaries auto-generated from commentator arc notes
- Human-vs-LLM mode as a first-class supported configuration
- Harness published open-source so others can run their own model matchups and contribute decks/formats

---

## Explicit Non-Goals (for v1)

- Real-time game variants (fighting games, RTS) — separate project
- Full card set coverage — Argentum's Portal set is the current pool
- Fine-tuning models on game data — harness first, training loop later
- Python-side rules enforcement — Argentum is the only rules authority
- Multiplayer (>2 players) — Commander is tempting but complexity is high; defer
- Text-to-speech commentary audio — the text layer is enough for v1
- Judge LLM — removed for now. Argentum makes ruling disputes nearly impossible
  (every action in the list is already legal). May reintroduce later as a
  commentary-tier voice ("the judge would have caught that…") rather than as
  an oracle.

---

## References

- Wang et al. (2023). *Voyager: An Open-Ended Embodied Agent with Large Language Models.* arXiv:2305.16291
- Ma et al. (2023). *Eureka: Human-Level Reward Design via Coding Large Language Models.* arXiv:2310.12931
- Liu et al. (2024). *LLM-Powered Hierarchical Language Agent for Real-time Human-AI Coordination.* AAMAS 2024. arXiv:2312.15224
- Liu et al. (2024). *RL-GPT: Integrating Reinforcement Learning and Code-as-policy.* NeurIPS 2024. arXiv:2402.19299
- Park et al. (2023). *Generative Agents: Interactive Simulacra of Human Behavior.* arXiv:2304.03442
- Card-Forge/forge — https://github.com/Card-Forge/forge
- GilesStrong/deep_mtg — https://github.com/GilesStrong/deep_mtg
- sboulema/mtgGoldfish — https://github.com/sboulema/mtgGoldfish
- Scryfall API — https://scryfall.com/docs/api
- untap.in — https://untap.in (reference for UX; closed source)
- patoles/agent-flow — https://github.com/patoles/agent-flow
- AlexHarn/claudeville — https://github.com/AlexHarn/claudeville
- joonspk-research/generative_agents — https://github.com/joonspk-research/generative_agents
- "Readable Minds: Emergent Theory-of-Mind-Like Behavior in LLM Poker Agents" (2025) — https://arxiv.org/abs/2604.04157
- PokerBattle AI annotated hand histories — https://www.poker.org/poker-strategy/the-ai-poker-battle-of-the-llms-a-detailed-analysis-as5Bg7J3P4g2/
