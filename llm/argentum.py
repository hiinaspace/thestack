"""HTTP client for the Argentum Engine gym-server."""

from __future__ import annotations

import os

import requests

ARGENTUM_HOST = os.environ.get("ARGENTUM_HOST", "http://localhost:8081")
_SESSION = requests.Session()


def create_env(
    player_a: str,
    deck_a: dict[str, int],
    player_b: str,
    deck_b: dict[str, int],
    perspective_index: int = 0,
    reveal_all: bool = True,
) -> tuple[str, dict]:
    """Create a new game env. Returns (env_id, opening_observation)."""
    payload = {
        "players": [
            {"name": player_a, "deck": {"type": "Explicit", "cards": deck_a}},
            {"name": player_b, "deck": {"type": "Explicit", "cards": deck_b}},
        ],
        "skipMulligans": True,
        "perspectivePlayerIndex": perspective_index,
        "revealAll": reveal_all,
    }
    r = _SESSION.post(f"{ARGENTUM_HOST}/envs", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["envId"], data["observation"]


def observe(env_id: str, reveal_all: bool = True) -> dict:
    params = {"revealAll": "true" if reveal_all else "false"}
    r = _SESSION.get(f"{ARGENTUM_HOST}/envs/{env_id}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def step(env_id: str, action_id: int) -> dict:
    r = _SESSION.post(
        f"{ARGENTUM_HOST}/envs/{env_id}/step",
        json={"actionId": action_id},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def dispose(env_id: str) -> None:
    _SESSION.delete(f"{ARGENTUM_HOST}/envs", json={"envIds": [env_id]})


def health() -> bool:
    try:
        r = _SESSION.get(f"{ARGENTUM_HOST}/health", timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False
