from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Recommendation:
    answer: str
    bullets: list[str]
