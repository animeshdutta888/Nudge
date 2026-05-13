from __future__ import annotations

from storage.local import LocalWorkspace
from schemas.shared import MemoryRecord, SharedState


class MemoryAgent:
    def __init__(self, workspace: LocalWorkspace) -> None:
        self._workspace = workspace

    async def recall(self, state: SharedState, limit: int = 6) -> list[MemoryRecord]:
        hits = await self._workspace.search_memories(state.query, limit=limit)
        if hits:
            return hits
        return await self._workspace.recent_memories(limit=min(limit, 4))

    async def save_explicit(self, kind: str, text: str) -> MemoryRecord:
        return await self._workspace.append_memory(kind, text)
