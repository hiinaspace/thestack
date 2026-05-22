"""Ollama native client."""

from __future__ import annotations

import os

import ollama

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://192.168.1.26:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:latest")


def make_client() -> ollama.Client:
    return ollama.Client(host=OLLAMA_HOST)
