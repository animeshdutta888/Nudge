from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.services.storage import read_json, write_json


def recent_items(path: Path, limit: int = 10) -> list[dict[str, Any]]:
    items = _load(path)
    out: list[dict[str, Any]] = []
    for i, item in enumerate(reversed(items[-max(1, int(limit)) :]), start=1):
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload["recent_index"] = i
        out.append(payload)
    return out


def delete_recent(path: Path, recent_index: int) -> bool:
    items = _load(path)
    idx = _resolve_recent_index(items, recent_index)
    if idx is None:
        return False
    del items[idx]
    write_json(path, items)
    return True


def edit_recent(path: Path, recent_index: int, text: str) -> bool:
    items = _load(path)
    idx = _resolve_recent_index(items, recent_index)
    if idx is None:
        return False
    if not isinstance(items[idx], dict):
        return False
    items[idx]["text"] = text.strip()
    write_json(path, items)
    return True


def pin_recent(path: Path, recent_index: int, pinned: bool) -> bool:
    items = _load(path)
    idx = _resolve_recent_index(items, recent_index)
    if idx is None:
        return False
    if not isinstance(items[idx], dict):
        return False
    items[idx]["pinned"] = bool(pinned)
    write_json(path, items)
    return True


def _resolve_recent_index(items: list[dict[str, Any]], recent_index: int) -> Optional[int]:
    if recent_index < 1:
        return None
    if recent_index > len(items):
        return None
    return len(items) - recent_index


def _load(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    return data if isinstance(data, list) else []
