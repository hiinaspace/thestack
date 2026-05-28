"""Hermetic wrapper around ``run_game.run_game()`` for scripted-agent tests.

Pins the library seed, swaps in ScriptedAgents, runs the harness against a
temp-dir game log, and returns the parsed game.jsonl event list so tests
can assert on engine events / harness behavior end-to-end.

See [[feedback-live-harness-tests]] for the testing philosophy this scaffold
exists to support.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from llm import argentum
from run_game import run_game

from .scripted_agent import ScriptedAgent


@dataclass
class ScriptedRunResult:
    """Output of one scripted-harness run.

    ``events`` is the full parsed game.jsonl. ``game_dir`` is kept around
    so tests can inspect side files (decisions/, etc.) if needed; it
    lives in a tmp dir so successful tests don't pollute games/.
    """

    events: list[dict]
    game_dir: Path
    agents: dict[str, ScriptedAgent]
    stop_reason: str | None
    winner_name: str | None
    final_obs: dict | None

    def by_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e.get("event") == event_type]

    def engine_events(self) -> list[dict]:
        """All flattened engine events (across ENGINE_EVENT rows)."""
        out: list[dict] = []
        for row in self.by_type("engine_event"):
            for ev in row.get("events") or []:
                out.append({**ev, "_seq": row.get("seq")})
        return out

    def actions(self) -> list[dict]:
        return self.by_type("action")

    def autopasses(self) -> list[dict]:
        return self.by_type("autopass")


def run_scripted_game(
    *,
    persona_a: str,
    persona_b: str,
    deck_a: str,
    deck_b: str,
    library_seed: int,
    script_a: list,
    script_b: list,
    tmp_root: Path,
    max_steps: int = 300,
    verbose: bool = False,
    game_id: str = "scripted",
    require_gym: bool = True,
    skip_mulligans: bool = True,
    policy_a: Callable[[dict, list[dict]], int] | None = None,
    policy_b: Callable[[dict, list[dict]], int] | None = None,
) -> ScriptedRunResult:
    """Drive run_game with two ScriptedAgents.

    Args mirror run_game.run_game where they overlap; ``script_a`` /
    ``script_b`` are lists of ``Directive`` (see scripted_agent.py).

    ``tmp_root`` is the parent dir under which ``games/<game_id>`` is
    created; pass a pytest ``tmp_path`` so the run is hermetic.

    ``skip_mulligans`` defaults True for tests: the mulligan-order
    decision (who-acts-first-to-mulligan) appears to be the only
    non-deterministic signal in the gym even with ``librarySeed`` set, so
    skipping it gives us byte-stable runs. Pass False if a test needs to
    exercise mulligan decisions specifically.

    Raises RuntimeError if the gym server isn't reachable and
    ``require_gym`` is True. Tests should generally let this raise — a
    silent skip for an unreachable gym hides real environment problems.
    """
    if require_gym and not argentum.health():
        raise RuntimeError(
            f"Argentum gym-server not reachable at {argentum.ARGENTUM_HOST} — "
            "start it with `just gym-server` from the engine repo."
        )

    # ScriptedAgent is constructed without an EventLog; run_game owns the
    # log and late-binds via bind_event_log on each override agent. Keeps
    # one writer to one file (no interleaved seq numbers).
    agent_a = ScriptedAgent(persona_a, script=script_a, default_action_pick=policy_a)
    agent_b = ScriptedAgent(persona_b, script=script_b, default_action_pick=policy_b)

    run_game(
        persona_a_name=persona_a,
        persona_b_name=persona_b,
        deck_a_name=deck_a,
        deck_b_name=deck_b,
        model="<scripted>",
        max_steps=max_steps,
        game_id=game_id,
        verbose=verbose,
        random_agents=False,
        library_seed=library_seed,
        agents_override={persona_a: agent_a, persona_b: agent_b},
        games_root=tmp_root / "games",
        skip_mulligans=skip_mulligans,
    )

    game_dir = tmp_root / "games" / game_id
    log_path = game_dir / "game.jsonl"
    events = _read_events(log_path)

    game_over = next(
        (e for e in reversed(events) if e.get("event") == "game_over"),
        None,
    )
    stop_reason = game_over.get("reason") if game_over else None
    winner_name = game_over.get("winner") if game_over else None
    final_obs = None
    for e in reversed(events):
        if e.get("event") == "observation":
            final_obs = e.get("obs")
            break

    return ScriptedRunResult(
        events=events,
        game_dir=game_dir,
        agents={persona_a: agent_a, persona_b: agent_b},
        stop_reason=stop_reason,
        winner_name=winner_name,
        final_obs=final_obs,
    )


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    out: list[dict] = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
