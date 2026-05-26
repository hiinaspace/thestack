"""Harness-dev telemetry for LLM calls.

Two purposes:

1.  Always-on: per-call Ollama metrics (prompt_eval_count, eval_count,
    *_duration, etc.) get appended to a side-file ``<game_dir>/dev.jsonl``.
    These never touch ``game.jsonl`` / the viewer per the
    [[feedback-dev-vs-spectator]] rule.

2.  Opt-in: if MLflow is installed AND ``THESTACK_MLFLOW=1`` is set in the
    environment, each call is also wrapped in an MLflow span carrying the
    same attributes. This is the on-ramp for the DSPy prompt-optimization
    work the project is heading toward; MLflow has first-class DSPy
    autolog support whereas a PydanticAI-style agent abstraction would
    have forced us to rewrite the existing tool loop.

The module is intentionally tiny and import-safe — neither MLflow nor a
game directory needs to be configured for the harness to run.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------- mlflow

# Lazy import: keep MLflow optional. The harness must import cleanly even
# when the `telemetry` extra hasn't been installed.
_MLFLOW = None
_MLFLOW_ACTIVE = False


def _maybe_init_mlflow() -> None:
    """Initialize MLflow once if the env var asks for it and it imports."""
    global _MLFLOW, _MLFLOW_ACTIVE
    if _MLFLOW_ACTIVE or _MLFLOW is False:
        return
    if os.environ.get("THESTACK_MLFLOW", "").lower() not in {"1", "true", "yes"}:
        _MLFLOW = False
        return
    try:
        import mlflow  # type: ignore

        tracking_uri = os.environ.get("MLFLOW_TRACKING_URI") or "file:./mlruns"
        mlflow.set_tracking_uri(tracking_uri)
        experiment = os.environ.get("MLFLOW_EXPERIMENT_NAME") or "thestack"
        mlflow.set_experiment(experiment)
        _MLFLOW = mlflow
        _MLFLOW_ACTIVE = True
    except Exception:
        # Missing extra or broken install — fall back silently. Dev-only.
        _MLFLOW = False


# --------------------------------------------------------------------- state


@dataclass
class _Sink:
    game_id: str | None = None
    game_dir: Path | None = None
    path: Path | None = None
    _file: Any = None  # IO[str] but typed loose to keep this module dep-free
    counters: dict[str, int] = field(default_factory=dict)

    def open(self, game_id: str, game_dir: Path) -> None:
        self.close()
        self.game_id = game_id
        self.game_dir = game_dir
        game_dir.mkdir(parents=True, exist_ok=True)
        self.path = game_dir / "dev.jsonl"
        self._file = self.path.open("a", encoding="utf-8")
        self.counters = {}

    def write(self, payload: dict[str, Any]) -> None:
        if self._file is None:
            return
        self._file.write(json.dumps(payload, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            with suppress(Exception):
                self._file.close()
        self._file = None


_SINK = _Sink()


# --------------------------------------------------------------------- public


def init_for_game(game_id: str, game_dir: Path) -> None:
    """Open / re-open the dev.jsonl sidecar for a new game.

    Safe to call before the gym is up; the file is just append-only JSONL.
    """
    _SINK.open(game_id, game_dir)
    _maybe_init_mlflow()


def close() -> None:
    _SINK.close()


def write_event(kind: str, **fields: Any) -> None:
    """Append a free-form dev event to the sidecar."""
    _SINK.write(
        {
            "ts": time.time(),
            "game_id": _SINK.game_id,
            "kind": kind,
            **fields,
        }
    )


@contextmanager
def call_span(operation: str, **attrs: Any):
    """Wrap an LLM call site so MLflow (if enabled) gets a span.

    Yields a mutable dict the caller can mutate with metrics post-call;
    on context exit, those metrics are set as span attributes AND
    recorded to the dev.jsonl sidecar.

    Always safe to use even without MLflow — degrades to a plain dict +
    a sidecar entry.
    """
    bag: dict[str, Any] = {"operation": operation, **attrs}
    started = time.perf_counter()

    if _MLFLOW_ACTIVE and _MLFLOW:  # pragma: no cover - exercised in dev
        with _MLFLOW.start_span(name=operation) as span:
            try:
                yield bag
            finally:
                bag.setdefault("wall_seconds", time.perf_counter() - started)
                with suppress(Exception):
                    span.set_attributes({k: _coerce(v) for k, v in bag.items()})
                write_event("ollama_call", **bag)
        return

    try:
        yield bag
    finally:
        bag.setdefault("wall_seconds", time.perf_counter() - started)
        write_event("ollama_call", **bag)


def record_ollama_response(bag: dict[str, Any], response: Any) -> None:
    """Copy the per-call metrics off an ollama ChatResponse into the bag.

    Ollama returns these on every /api/chat response:
        total_duration, load_duration, prompt_eval_count,
        prompt_eval_duration, eval_count, eval_duration.
    All durations are nanoseconds.
    """
    for field_name in (
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "done_reason",
        "model",
    ):
        try:
            value = getattr(response, field_name, None)
        except Exception:
            value = None
        if value is not None:
            bag[field_name] = value
    # Derived: tokens/sec for the new tokens (eval side) — handy for at-a-
    # glance reads in the sidecar.
    eval_count = bag.get("eval_count") or 0
    eval_duration = bag.get("eval_duration") or 0
    if eval_count and eval_duration:
        bag["eval_tokens_per_sec"] = round(eval_count / (eval_duration / 1e9), 2)


def bump_counter(key: str, by: int = 1) -> int:
    """Per-game running counter (e.g. tool iterations across the game)."""
    _SINK.counters[key] = _SINK.counters.get(key, 0) + by
    return _SINK.counters[key]


def _coerce(value: Any) -> Any:
    if isinstance(value, str | int | float | bool):
        return value
    return str(value)
