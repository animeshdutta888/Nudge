from __future__ import annotations

from agents.manager import ManagerAgent
from schemas.shared import SharedState


class OrchestrationEngine:
    def __init__(self, manager: ManagerAgent) -> None:
        self._manager = manager

    async def execute(self, state: SharedState) -> SharedState:
        return await self._manager.run(state)
