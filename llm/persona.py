"""Persona: a named LLM player with markdown files for cross-game memory.

A persona lives in `personas/<name>/` and owns four markdown files:

  identity.md   — hand-written voice & playstyle, treated as immutable in code
  memory.md     — rolling log of past games (LLM-appended after each game)
  opponents.md  — per-opponent notes, sectioned by "## <opponent name>"
  strategy.md   — current-deck strategy notes (LLM-overwritten before each game)

The Persona class reads/writes those files. It does NOT make LLM calls; the
Strategist (pre-game) and Reflector (post-game) do that and call back here.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

PERSONAS_DIR = Path("personas")

_FILES = ("identity.md", "memory.md", "opponents.md", "strategy.md")


class Persona:
    def __init__(self, name: str, root: Path | None = None) -> None:
        self.name = name
        self.root = root if root is not None else PERSONAS_DIR / name
        if not self.root.is_dir():
            raise FileNotFoundError(
                f"Persona directory not found: {self.root} "
                f"(create it with at least an identity.md file)"
            )
        for fname in _FILES:
            (self.root / fname).touch(exist_ok=True)

    # ------------------------------------------------------------------ reads

    @property
    def identity(self) -> str:
        return (self.root / "identity.md").read_text().strip()

    @property
    def memory(self) -> str:
        return (self.root / "memory.md").read_text().strip()

    @property
    def opponents(self) -> str:
        return (self.root / "opponents.md").read_text().strip()

    @property
    def strategy(self) -> str:
        return (self.root / "strategy.md").read_text().strip()

    def opponent_entry(self, opponent_name: str) -> str:
        """Extract the `## <opponent_name>` block from opponents.md, or ""."""
        text = self.opponents
        if not text:
            return ""
        # Split on level-2 headings; find the one for this opponent.
        pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
        parts: list[tuple[str, int]] = [(m.group(1), m.start()) for m in pattern.finditer(text)]
        for i, (heading, start) in enumerate(parts):
            if heading.lower().strip() == opponent_name.lower().strip():
                end = parts[i + 1][1] if i + 1 < len(parts) else len(text)
                return text[start:end].rstrip()
        return ""

    def recent_memory(self, max_chars: int = 4000) -> str:
        """Return the tail of memory.md, sliced on a heading boundary if possible."""
        text = self.memory
        if len(text) <= max_chars:
            return text
        tail = text[-max_chars:]
        # Try to start at the first '## ' heading inside the tail for cleanliness.
        m = re.search(r"^##\s", tail, re.MULTILINE)
        return tail[m.start() :] if m else tail

    # ----------------------------------------------------------------- writes

    def append_memory(self, entry: str) -> None:
        path = self.root / "memory.md"
        sep = "\n\n" if path.read_text().strip() else ""
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{sep}{entry.strip()}\n")

    def upsert_opponent(self, opponent_name: str, body: str) -> None:
        """Replace (or append) the `## <opponent_name>` block in opponents.md."""
        existing = self.opponents
        body = body.strip()
        new_block = f"## {opponent_name}\n\n{body}\n"

        if not existing:
            (self.root / "opponents.md").write_text(new_block)
            return

        # Find existing section and splice it out.
        pattern = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
        parts = [(m.group(1), m.start()) for m in pattern.finditer(existing)]
        for i, (heading, start) in enumerate(parts):
            if heading.lower().strip() == opponent_name.lower().strip():
                end = parts[i + 1][1] if i + 1 < len(parts) else len(existing)
                updated = existing[:start].rstrip() + "\n\n" + new_block + existing[end:].lstrip()
                (self.root / "opponents.md").write_text(updated.strip() + "\n")
                return

        # Section did not exist — append.
        (self.root / "opponents.md").write_text(existing.rstrip() + "\n\n" + new_block)

    def write_strategy(self, text: str) -> None:
        (self.root / "strategy.md").write_text(text.strip() + "\n")

    # --------------------------------------------------------------- snapshot

    def snapshot_to(self, dest: Path) -> None:
        """Copy all persona files into `dest/` so the replay viewer can show
        what the agent knew at the start of this game."""
        dest.mkdir(parents=True, exist_ok=True)
        for fname in _FILES:
            src = self.root / fname
            if src.exists():
                shutil.copy2(src, dest / fname)
