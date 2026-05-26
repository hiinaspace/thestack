"""Ollama native client + shared per-call options.

The model defaults live here rather than on the Agent class so every Ollama
caller in the harness (Agent, the meta strategist/reflector, future direct
callers) picks them up the same way.

`DEFAULT_NUM_CTX` is the single most important knob: stock Ollama defaults
`num_ctx` to 4096 even when the model has a 131k window. That truncation
is the primary cause of long-game decision slowdown (the prompt fits in
the window early, slowly falls off the back as the persistent agent
history grows, and re-evaluation churn balloons). 16384 keeps a comfortable
fit through ~20+ turns on the player conversation; gemma4 supports much
larger if needed via the env override below.
"""

from __future__ import annotations

import os
from typing import Any

import ollama

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://192.168.1.26:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:latest")

# Tune via THESTACK_NUM_CTX / THESTACK_NUM_PREDICT in the environment to
# override without code changes.
DEFAULT_NUM_CTX = int(os.environ.get("THESTACK_NUM_CTX", "16384"))
# Hard cap on tokens generated per single chat completion. Stops a runaway
# monologue from eating minutes of GPU time before submit_action lands.
DEFAULT_NUM_PREDICT = int(os.environ.get("THESTACK_NUM_PREDICT", "1024"))


def make_client() -> ollama.Client:
    return ollama.Client(host=OLLAMA_HOST)


def chat_options(
    *,
    temperature: float,
    num_ctx: int | None = None,
    num_predict: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the `options` payload for ollama.Client.chat.

    Centralises the per-call knobs so the harness can't accidentally fall
    back to Ollama's 4096-ctx default. Pass `extra` for one-off overrides
    (e.g. a stop sequence on a narrow narration call).
    """
    options: dict[str, Any] = {
        "temperature": temperature,
        "num_ctx": num_ctx if num_ctx is not None else DEFAULT_NUM_CTX,
        "num_predict": num_predict if num_predict is not None else DEFAULT_NUM_PREDICT,
    }
    if extra:
        options.update(extra)
    return options
