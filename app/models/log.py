from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LogEntry:
    ts: str
    text: str
