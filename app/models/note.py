from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Note:
    ts: str
    text: str
    tags: list[str]

