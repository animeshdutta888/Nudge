from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.models.log import LogEntry
from app.models.note import Note
from app.services.retrieval import Retriever
from app.services.storage import read_json, write_json
from app.utils.logger import warn
from app.utils.time import now_local_iso, parse_iso_to_local_date, today_local_date


class Memory:
    """Local-first storage for logs + notes, with FAISS indexing."""

    def __init__(self, logs_path: Path, notes_path: Path, retriever: Retriever) -> None:
        self._logs_path = logs_path
        self._notes_path = notes_path
        self._retriever = retriever

    def append_log(self, text: str) -> LogEntry:
        entry = LogEntry(ts=now_local_iso(), text=text.strip())
        logs = self._load_list(self._logs_path)
        logs.append(asdict(entry))
        write_json(self._logs_path, logs)
        try:
            self._retriever.add(kind="log", ts=entry.ts, text=entry.text)
        except Exception as e:
            warn(f"Indexing skipped for log (retrieval unavailable): {e}")
        return entry

    def recent_logs(self, n: int) -> list[LogEntry]:
        logs = self._load_list(self._logs_path)
        tail = logs[-n:] if n > 0 else []
        out: list[LogEntry] = []
        for item in tail:
            if isinstance(item, dict) and "ts" in item and "text" in item:
                out.append(LogEntry(ts=str(item["ts"]), text=str(item["text"])))
        return out

    def all_logs(self) -> list[LogEntry]:
        logs = self._load_list(self._logs_path)
        out: list[LogEntry] = []
        for item in logs:
            if isinstance(item, dict) and "ts" in item and "text" in item:
                out.append(LogEntry(ts=str(item["ts"]), text=str(item["text"])))
        return out

    def logs_today(self) -> list[LogEntry]:
        today = today_local_date()
        out: list[LogEntry] = []
        for e in self.all_logs():
            d = parse_iso_to_local_date(e.ts)
            if d is not None and d == today:
                out.append(e)
        return out

    def logs_in_last_days(self, days: int) -> list[LogEntry]:
        cutoff = today_local_date().toordinal() - max(0, int(days)) + 1
        out: list[LogEntry] = []
        for e in self.all_logs():
            d = parse_iso_to_local_date(e.ts)
            if d is not None and d.toordinal() >= cutoff:
                out.append(e)
        return out

    def add_note(self, text: str, tags: list[str] | None = None) -> Note:
        note = Note(ts=now_local_iso(), text=text.strip(), tags=tags or [])
        notes = self._load_list(self._notes_path)
        notes.append(asdict(note))
        write_json(self._notes_path, notes)
        try:
            self._retriever.add(kind="note", ts=note.ts, text=note.text)
        except Exception as e:
            warn(f"Indexing skipped for note (retrieval unavailable): {e}")
        return note

    def recent_notes(self, n: int) -> list[Note]:
        notes = self._load_list(self._notes_path)
        tail = notes[-n:] if n > 0 else []
        out: list[Note] = []
        for item in tail:
            if isinstance(item, dict) and "ts" in item and "text" in item:
                out.append(
                    Note(
                        ts=str(item["ts"]),
                        text=str(item["text"]),
                        tags=[str(x) for x in item.get("tags", [])] if isinstance(item.get("tags"), list) else [],
                    )
                )
        return out

    def all_notes(self) -> list[Note]:
        notes = self._load_list(self._notes_path)
        out: list[Note] = []
        for item in notes:
            if isinstance(item, dict) and "ts" in item and "text" in item:
                out.append(
                    Note(
                        ts=str(item["ts"]),
                        text=str(item["text"]),
                        tags=[str(x) for x in item.get("tags", [])] if isinstance(item.get("tags"), list) else [],
                    )
                )
        return out

    def notes_in_last_days(self, days: int) -> list[Note]:
        cutoff = today_local_date().toordinal() - max(0, int(days)) + 1
        out: list[Note] = []
        for n in self.all_notes():
            d = parse_iso_to_local_date(n.ts)
            if d is not None and d.toordinal() >= cutoff:
                out.append(n)
        return out

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """
        Local deterministic recall that does not depend on embeddings.
        Returns most-recent matches first across logs and notes.
        """
        q = (query or "").strip().lower()
        if not q:
            return []

        tokens = [t for t in _tokenize(q) if t not in _STOPWORDS]
        if not tokens:
            tokens = [q]

        hits: list[dict[str, str]] = []

        logs = self._load_list(self._logs_path)
        notes = self._load_list(self._notes_path)

        def matches(text: str) -> bool:
            low = (text or "").lower()
            return all(t in low for t in tokens)

        # Iterate newest first.
        for item in reversed(logs):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text or matches(text) is False:
                continue
            hits.append({"kind": "log", "ts": str(item.get("ts", "")), "text": text})
            if len(hits) >= int(limit):
                return hits

        for item in reversed(notes):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text or matches(text) is False:
                continue
            hits.append({"kind": "note", "ts": str(item.get("ts", "")), "text": text})
            if len(hits) >= int(limit):
                return hits

        return hits

    def _load_list(self, path: Path) -> list[dict[str, Any]]:
        data = read_json(path, default=[])
        return data if isinstance(data, list) else []


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "did",
    "do",
    "earlier",
    "for",
    "from",
    "have",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "show",
    "tell",
    "that",
    "the",
    "to",
    "was",
    "we",
    "what",
    "where",
    "you",
    "your",
}


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    for ch in text:
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return [t.lower() for t in out if t]
