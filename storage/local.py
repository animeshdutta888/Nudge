from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.models.log import LogEntry
from app.models.note import Note
from app.services.conversations import append_conversation
from app.services.storage import ensure_json_file, read_json, write_json
from app.utils.time import now_local_iso
from schemas.shared import MemoryRecord


class LocalWorkspace:
    def __init__(self, data_dir: Path, traces_db_path: Path) -> None:
        self.data_dir = data_dir
        self.logs_path = data_dir / "logs.json"
        self.notes_path = data_dir / "notes.json"
        self.persona_path = data_dir / "persona.json"
        self.state_path = data_dir / "state.json"
        self.reminders_path = data_dir / "reminders.json"
        self.conversations_path = data_dir / "conversations.json"
        self.projects_path = data_dir / "projects.json"
        self.semantic_cache_path = data_dir / "semantic_cache.json"
        self.traces_db_path = traces_db_path
        self._ensure_files()

    def _ensure_files(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        ensure_json_file(self.logs_path, [])
        ensure_json_file(self.notes_path, [])
        ensure_json_file(self.persona_path, {})
        ensure_json_file(
            self.state_path,
            {
                "pending_save": None,
                "pending_plan": None,
                "autosave_enabled": True,
                "asked_questions": [],
                "last_question": None,
                "daily_checkin": {"last_prompt_date": None, "dismissed_date": None},
            },
        )
        ensure_json_file(self.reminders_path, [])
        ensure_json_file(self.conversations_path, [])
        ensure_json_file(self.projects_path, [])
        ensure_json_file(self.semantic_cache_path, {"items": []})

    async def append_memory(self, kind: str, text: str) -> MemoryRecord:
        return await asyncio.to_thread(self._append_memory_sync, kind, text)

    def _append_memory_sync(self, kind: str, text: str) -> MemoryRecord:
        clean = text.strip()
        ts = now_local_iso()
        if kind == "log":
            items = read_json(self.logs_path, default=[])
            if not isinstance(items, list):
                items = []
            items.append(asdict(LogEntry(ts=ts, text=clean)))
            write_json(self.logs_path, items)
            return MemoryRecord(record_id=f"log:{len(items)}", kind="log", ts=ts, text=clean)

        items = read_json(self.notes_path, default=[])
        if not isinstance(items, list):
            items = []
        items.append(asdict(Note(ts=ts, text=clean, tags=[])))
        write_json(self.notes_path, items)
        return MemoryRecord(record_id=f"note:{len(items)}", kind="note", ts=ts, text=clean)

    async def recent_memories(self, limit: int = 20) -> list[MemoryRecord]:
        return await asyncio.to_thread(self._recent_memories_sync, limit)

    def _recent_memories_sync(self, limit: int = 20) -> list[MemoryRecord]:
        logs = read_json(self.logs_path, default=[])
        notes = read_json(self.notes_path, default=[])
        memories: list[MemoryRecord] = []
        for idx, item in enumerate(logs if isinstance(logs, list) else [], start=1):
            if isinstance(item, dict):
                memories.append(
                    MemoryRecord(
                        record_id=f"log:{idx}",
                        kind="log",
                        ts=str(item.get("ts", "")),
                        text=str(item.get("text", "")),
                    )
                )
        for idx, item in enumerate(notes if isinstance(notes, list) else [], start=1):
            if isinstance(item, dict):
                memories.append(
                    MemoryRecord(
                        record_id=f"note:{idx}",
                        kind="note",
                        ts=str(item.get("ts", "")),
                        text=str(item.get("text", "")),
                        metadata={"tags": item.get("tags", [])},
                    )
                )
        memories.sort(key=lambda item: item.ts, reverse=True)
        return memories[: max(1, int(limit))]

    async def search_memories(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        return await asyncio.to_thread(self._search_memories_sync, query, limit)

    def _search_memories_sync(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        tokens = _tokenize(query)
        if not tokens:
            return []
        hits: list[MemoryRecord] = []
        for item in self._recent_memories_sync(limit=500):
            hay = item.text.lower()
            if all(token in hay for token in tokens):
                hits.append(item)
            if len(hits) >= limit:
                break
        return hits

    async def append_conversation(self, user_text: str, assistant_text: str, source: str) -> None:
        await asyncio.to_thread(append_conversation, self.conversations_path, user_text, assistant_text, source)

    def load_state(self) -> dict[str, Any]:
        raw = read_json(self.state_path, default={})
        return raw if isinstance(raw, dict) else {}

    def save_state(self, state: dict[str, Any]) -> None:
        write_json(self.state_path, state)

    def merge_state(self, updates: dict[str, Any]) -> dict[str, Any]:
        state = self.load_state()
        state.update(updates)
        write_json(self.state_path, state)
        return state

    def load_persona(self) -> dict[str, Any]:
        raw = read_json(self.persona_path, default={})
        return raw if isinstance(raw, dict) else {}

    def refresh_persona_snapshot(self) -> dict[str, Any]:
        memories = self._recent_memories_sync(limit=40)
        focus: list[str] = []
        wins: list[str] = []
        for item in memories:
            low = item.text.lower()
            if "focus=" in low:
                focus.append(item.text)
            if "win=" in low:
                wins.append(item.text)
        persona = self.load_persona()
        persona["updated_at"] = now_local_iso()
        persona["current_focus"] = focus[:3]
        persona["recent_wins"] = wins[:3]
        write_json(self.persona_path, persona)
        return persona

    def data_version_hash(self) -> str:
        hasher = hashlib.sha256()
        for path in (
            self.logs_path,
            self.notes_path,
            self.reminders_path,
            self.projects_path,
            self.persona_path,
            self.conversations_path,
        ):
            try:
                stat = path.stat()
            except OSError:
                continue
            hasher.update(str(path.name).encode("utf-8"))
            hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
            hasher.update(str(stat.st_size).encode("utf-8"))
        return hasher.hexdigest()


def _tokenize(text: str) -> list[str]:
    tokens = [part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if part]
    return [token for token in tokens if token not in _STOPWORDS]


_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "did",
    "do",
    "earlier",
    "i",
    "learn",
    "me",
    "my",
    "of",
    "remember",
    "show",
    "tell",
    "that",
    "the",
    "what",
    "where",
    "you",
}
