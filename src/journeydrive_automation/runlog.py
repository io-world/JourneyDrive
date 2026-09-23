from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Tools whose `text` argument could be a password or other sensitive content —
# logged as a character count only, never the text itself (same carve-out as
# ws_client.py's keyboard_type/clipboard_set and the MCP server's type_text).
_SENSITIVE_TEXT_TOOLS = {"type_text", "set_clipboard"}


def redact_tool_input(name: str, tool_input: dict) -> dict:
    if name in _SENSITIVE_TEXT_TOOLS and isinstance(tool_input.get("text"), str):
        return {**{k: v for k, v in tool_input.items() if k != "text"}, "chars": len(tool_input["text"])}
    return tool_input


class RunLog:
    """One folder per run: events.jsonl (every node transition), every image the
    agent or verifier saw, and summary.json — enough to replay afterwards what
    the model saw and why it did what it did."""

    def __init__(self, base_dir: str | Path, script_name: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", script_name).strip("-") or "script"
        self.dir = Path(base_dir) / f"{timestamp}_{slug}"
        suffix = 1
        while self.dir.exists():
            self.dir = Path(base_dir) / f"{timestamp}_{slug}_{suffix}"
            suffix += 1
        self.dir.mkdir(parents=True)
        self._events = self.dir / "events.jsonl"
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def event(self, kind: str, **fields: Any) -> None:
        record = {"seq": self._next_seq(), "ts": datetime.now(timezone.utc).isoformat(), "event": kind, **fields}
        with self._events.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def save_image(self, data: bytes, label: str) -> str:
        """Save an image the model saw; returns its filename (relative to the run dir)."""
        name = f"{self._next_seq():03d}_{label}.png"
        (self.dir / name).write_bytes(data)
        return name

    def write_summary(self, summary: dict) -> None:
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")

    @contextmanager
    def capture_console(self) -> Iterator[None]:
        """Copy every log record emitted during the run into this run's own
        run.log, so the folder holds the full console output alongside events."""
        handler = logging.FileHandler(self.dir / "run.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            yield
        finally:
            root.removeHandler(handler)
            handler.close()
