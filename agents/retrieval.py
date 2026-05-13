from __future__ import annotations

import asyncio

from app.services.llm import LlmConfig
from app.services.retrieval import Retriever
from schemas.shared import RetrievalChunk, SharedState


class RetrievalAgent:
    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    async def retrieve(self, state: SharedState, limit: int = 5) -> list[RetrievalChunk]:
        results = await asyncio.to_thread(self._retriever.retrieve, state.query, limit)
        chunks: list[RetrievalChunk] = []
        for item in results:
            chunks.append(
                RetrievalChunk(
                    chunk_id=str(item.item.id),
                    source_kind=item.item.kind,
                    source_ts=item.item.ts,
                    text=item.item.text,
                    score=float(item.score),
                    metadata={"retrieval": "faiss"},
                )
            )
        return chunks
