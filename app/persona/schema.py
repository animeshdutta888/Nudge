from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Persona:
    interests: list[str] = field(default_factory=list)
    habits: list[str] = field(default_factory=list)
    mood_trends: str = ""
    current_focus: list[str] = field(default_factory=list)
    updated_at: str = ""
