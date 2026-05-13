from __future__ import annotations

from schemas.shared import CriticFeedback, SharedState


class CriticAgent:
    async def validate(self, state: SharedState) -> list[CriticFeedback]:
        findings: list[CriticFeedback] = []
        if not (state.synthesis_output or "").strip():
            findings.append(CriticFeedback(severity="error", code="EMPTY_RESPONSE", message="Synthesis returned an empty response"))
        if not state.retrieved_chunks and not state.memory_context:
            findings.append(
                CriticFeedback(
                    severity="warning",
                    code="NO_CONTEXT",
                    message="No validated retrieval or memory context was available",
                )
            )
        return findings
