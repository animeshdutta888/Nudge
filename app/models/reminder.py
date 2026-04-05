from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reminder:
    id: int
    created_ts: str
    due_ts: str | None
    text: str
    done: bool
    done_ts: str | None

