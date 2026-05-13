from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Reminder:
    id: int
    created_ts: str
    due_ts: Optional[str]
    text: str
    done: bool
    done_ts: Optional[str]
