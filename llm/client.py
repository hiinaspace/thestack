"""OpenAI-compatible client pointed at Ollama."""

from __future__ import annotations

import os

from openai import OpenAI

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://192.168.1.26:11434/v1")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:latest")


def make_client() -> OpenAI:
    return OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
