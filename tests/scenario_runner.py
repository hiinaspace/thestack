"""Pinned game-state scenarios — loader + helpers.

Companion to ``tests/decision_runner.py``, which drives LLM-facing
structured-decision fixtures. Scenarios under ``tests/fixtures/scenarios/``
are full observation snapshots captured from real game logs, used to
regression-test harness behavior against specific game states without
fuzzing for them.

Today the only scenario kind is ``ACTION_SUBMISSION``, used for the T12
block-bug regression: it captures the observation a player had priority
on, the action they submitted, and the resulting observations — so the
no-progress detection / fallback logic can be exercised deterministically.
Future kinds (full multi-turn pins driven through argentum) are out of
scope here; see plan ``deep-honking-gadget`` Phase 2.5.
"""

from __future__ import annotations

import json
from pathlib import Path

SCENARIO_ROOT = Path(__file__).parent / "fixtures" / "scenarios"


def load_scenario(path: Path) -> dict:
    """Load a scenario fixture; validate the minimal required shape."""
    with path.open() as f:
        data = json.load(f)
    meta = data.get("_meta") or {}
    if not meta.get("fixture"):
        raise ValueError(f"Scenario {path} missing _meta.fixture")
    if not meta.get("kind"):
        raise ValueError(f"Scenario {path} missing _meta.kind")
    return data


def list_scenarios(kind: str | None = None) -> list[Path]:
    paths = sorted(SCENARIO_ROOT.glob("*.json"))
    if kind is None:
        return paths
    out = []
    for p in paths:
        try:
            data = load_scenario(p)
        except (ValueError, json.JSONDecodeError):
            continue
        if (data.get("_meta") or {}).get("kind") == kind:
            out.append(p)
    return out
