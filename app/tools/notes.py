from __future__ import annotations

from app.agent.memory import Memory


def add_note(memory: Memory, text: str) -> str:
    note = memory.add_note(text)
    return f"Saved note at {note.ts}."


def list_notes(memory: Memory, n: int = 10) -> str:
    notes = memory.recent_notes(n)
    if not notes:
        return "No notes yet."
    lines = ["Recent notes:"]
    for i, note in enumerate(reversed(notes), start=1):
        lines.append(f"{i}. {note.ts}: {note.text}")
    return "\n".join(lines)

