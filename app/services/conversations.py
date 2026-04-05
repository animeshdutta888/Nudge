from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.storage import read_json, write_json
from app.utils.time import now_local_iso


def append_conversation(path: Path, user_text: str, assistant_text: str, source: str) -> None:
    items = read_json(path, default=[])
    if not isinstance(items, list):
        items = []
    items.append(
        {
            "ts": now_local_iso(),
            "user": user_text.strip(),
            "assistant": assistant_text.strip(),
            "source": source.strip() or "cli",
        }
    )
    write_json(path, items[-500:])


def load_conversations(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    items = read_json(path, default=[])
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items[-max(1, int(limit)) :]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "ts": str(item.get("ts", "")),
                "user": str(item.get("user", "")),
                "assistant": str(item.get("assistant", "")),
                "source": str(item.get("source", "cli")),
            }
        )
    return out

