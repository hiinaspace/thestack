"""FastAPI server for The Stack replay viewer.

Reads games written by run_game.py at games/<id>/game.jsonl and serves them
plus their persona snapshots to a vanilla-HTML single-page client.

Run:
  uv run viewer
  uv run uvicorn viewer.server:app --reload
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

GAMES_DIR = Path(os.environ.get("THESTACK_GAMES_DIR", "games"))
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="The Stack — Replay Viewer")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/games")
def list_games() -> JSONResponse:
    if not GAMES_DIR.is_dir():
        return JSONResponse([])
    entries = []
    for child in sorted(GAMES_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        log = child / "game.jsonl"
        if not log.exists():
            continue
        meta = _summarize_game(child.name, log)
        entries.append(meta)
    return JSONResponse(entries)


@app.get("/api/games/{game_id}")
def game_events(game_id: str) -> JSONResponse:
    log = GAMES_DIR / game_id / "game.jsonl"
    if not log.exists():
        raise HTTPException(404, "unknown game")
    events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return JSONResponse({"game_id": game_id, "events": events})


@app.get("/api/games/{game_id}/personas")
def game_personas(game_id: str) -> JSONResponse:
    pdir = GAMES_DIR / game_id / "personas"
    if not pdir.is_dir():
        raise HTTPException(404, "no persona snapshot for this game")
    out: dict[str, dict[str, str]] = {}
    for persona_dir in sorted(pdir.iterdir()):
        if not persona_dir.is_dir():
            continue
        files: dict[str, str] = {}
        for md in persona_dir.glob("*.md"):
            files[md.stem] = md.read_text()
        out[persona_dir.name] = files
    return JSONResponse(out)


# ----------------------------------------------------------------- helpers


def _summarize_game(game_id: str, log_path: Path) -> dict:
    """Pull a few headline fields from the JSONL so the index page can show
    a one-line summary without loading the whole game."""
    players: list[str] = []
    turns = 0
    winner: str | None = None
    stop_reason: str | None = None

    for raw in log_path.read_text().splitlines():
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev["event"] == "observation":
            obs = ev.get("obs", {})
            ps = obs.get("players")
            if ps and not players:
                players = [p.get("name", "?") for p in ps]
            turns = max(turns, int(obs.get("turnNumber", 0)))
        elif ev["event"] == "game_over":
            winner = ev.get("winner")
            stop_reason = ev.get("reason")

    return {
        "game_id": game_id,
        "players": players,
        "turns": turns,
        "winner": winner,
        "stop_reason": stop_reason,
        "mtime": log_path.stat().st_mtime,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        "viewer.server:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
