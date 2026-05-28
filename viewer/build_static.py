"""Build a static replay viewer bundle.

The FastAPI viewer is useful locally, but publishing to a plain web root needs
precomputed API responses. This copies viewer/static and writes a data/ tree:

  data/games.json
  data/oracle.json
  data/games/<game_id>/game.jsonl
  data/games/<game_id>/personas.json
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a static The Stack viewer")
    parser.add_argument("games", nargs="+", type=Path, help="game directories to include")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    args = parser.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    clean_bundle_paths(out)
    copy_static_assets(out)

    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for game_dir in args.games:
        game_dir = game_dir if game_dir.is_absolute() else ROOT / game_dir
        log_path = game_dir / "game.jsonl"
        if not log_path.exists():
            raise SystemExit(f"missing transcript: {log_path}")

        game_id = game_dir.name
        game_out = data_dir / "games" / game_id
        game_out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log_path, game_out / "game.jsonl")
        write_json(game_out / "personas.json", read_personas(game_dir / "personas"))
        entries.append(summarize_game(game_id, log_path))

    entries.sort(key=lambda e: e["mtime"], reverse=True)
    write_json(data_dir / "games.json", entries)
    write_json(data_dir / "oracle.json", slim_oracle())


def clean_bundle_paths(out: Path) -> None:
    for child in ["static", "data"]:
        path = out / child
        if path.exists():
            shutil.rmtree(path)
    for page in ["index.html", "theater.html"]:
        path = out / page
        if path.exists():
            path.unlink()


def copy_static_assets(out: Path) -> None:
    shutil.copy2(STATIC_DIR / "index.html", out / "index.html")
    shutil.copy2(STATIC_DIR / "theater.html", out / "theater.html")
    static_out = out / "static"
    static_out.mkdir(parents=True, exist_ok=True)
    for asset in ["app.js", "board.js", "gamedata.js", "theater.js", "style.css", "theater.css"]:
        shutil.copy2(STATIC_DIR / asset, static_out / asset)
    portraits = STATIC_DIR / "portraits"
    if portraits.is_dir():
        shutil.copytree(portraits, static_out / "portraits")


def read_personas(personas_dir: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not personas_dir.is_dir():
        return out
    for persona_dir in sorted(personas_dir.iterdir()):
        if not persona_dir.is_dir():
            continue
        files: dict[str, str] = {}
        for md in sorted(persona_dir.glob("*.md")):
            files[md.stem] = md.read_text()
        out[persona_dir.name] = files
    return out


def summarize_game(game_id: str, log_path: Path) -> dict:
    players: list[str] = []
    turns = 0
    winner: str | None = None
    stop_reason: str | None = None
    event_count = 0
    observation_count = 0

    for raw in log_path.read_text().splitlines():
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event_count += 1
        if ev.get("event") == "observation":
            observation_count += 1
            obs = ev.get("obs", {})
            ps = obs.get("players")
            if ps and not players:
                players = [p.get("name", "?") for p in ps]
            turns = max(turns, int(obs.get("turnNumber", 0)))
        elif ev.get("event") == "game_over":
            winner = ev.get("winner")
            stop_reason = ev.get("reason")

    return {
        "game_id": game_id,
        "players": players,
        "turns": turns,
        "winner": winner,
        "stop_reason": stop_reason,
        "event_count": event_count,
        "observation_count": observation_count,
        "transcript_path": f"data/games/{game_id}/game.jsonl",
        "mtime": log_path.stat().st_mtime,
    }


def slim_oracle() -> dict[str, dict]:
    try:
        from llm.oracle import _load  # type: ignore[attr-defined]
    except Exception:
        return {}

    out: dict[str, dict] = {}
    for name, card in _load().items():
        slim = {
            "type_line": card.get("type_line", ""),
            "mana_cost": card.get("mana_cost", ""),
            "oracle_text": card.get("oracle_text", ""),
        }
        if card.get("power") is not None:
            slim["power"] = card["power"]
            slim["toughness"] = card.get("toughness")
        out[name] = slim
    return out


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
