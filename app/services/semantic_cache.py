from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.services.llm import LlmConfig
from app.services.storage import ensure_json_file, read_json, write_json

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    requests = None


@dataclass(frozen=True)
class SemanticCacheHit:
    query: str
    answer: str
    citations: list[str]
    score: float


class SemanticCache:
    def __init__(
        self,
        path: Path,
        ollama: LlmConfig,
        embed_model: str,
        threshold: float,
        *,
        model_id: str,
        data_version_provider,
    ) -> None:
        self._path = path
        self._ollama = ollama
        self._embed_model = embed_model
        self._threshold = float(threshold)
        self._model_id = model_id
        self._data_version_provider = data_version_provider
        ensure_json_file(self._path, {"items": []})

    @property
    def threshold(self) -> float:
        return self._threshold

    async def lookup(self, query: str) -> Optional[SemanticCacheHit]:
        return await asyncio.to_thread(self._lookup_sync, query)

    def _lookup_sync(self, query: str) -> Optional[SemanticCacheHit]:
        if requests is None:
            return None
        clean = query.strip()
        if not clean:
            return None
        normalized_query = _normalize_query(clean)
        data_version = self._data_version_provider()
        query_vec = _embed(self._ollama, self._embed_model, normalized_query)
        state = _load_state(self._path)
        best: Optional[SemanticCacheHit] = None
        for item in state["items"]:
            if not isinstance(item, dict):
                continue
            if str(item.get("model_id", "")) != self._model_id:
                continue
            if str(item.get("data_version", "")) != data_version:
                continue
            embedding = item.get("embedding", [])
            if not isinstance(embedding, list) or not embedding:
                continue
            score = _cosine(query_vec, [float(x) for x in embedding])
            if score < self._threshold:
                continue
            hit = SemanticCacheHit(
                query=str(item.get("normalized_query", item.get("query", ""))),
                answer=str(item.get("answer", "")),
                citations=[str(x) for x in item.get("citations", []) if str(x).strip()] if isinstance(item.get("citations"), list) else [],
                score=score,
            )
            if best is None or hit.score > best.score:
                best = hit
        return best

    async def store(self, query: str, answer: str, citations: list[str]) -> None:
        await asyncio.to_thread(self._store_sync, query, answer, citations)

    def _store_sync(self, query: str, answer: str, citations: list[str]) -> None:
        if requests is None:
            return
        clean_query = query.strip()
        clean_answer = answer.strip()
        if not clean_query or not clean_answer:
            return
        if _is_sensitive_query(clean_query):
            return
        normalized_query = _normalize_query(clean_query)
        vec = _embed(self._ollama, self._embed_model, normalized_query)
        data_version = self._data_version_provider()
        state = _load_state(self._path)
        items = [item for item in state["items"] if isinstance(item, dict)]
        items = [
            item
            for item in items
            if str(item.get("model_id", "")) == self._model_id
            and str(item.get("data_version", "")) == data_version
        ]
        items.append(
            {
                "query": clean_query,
                "normalized_query": normalized_query,
                "answer": clean_answer,
                "citations": [str(x) for x in citations if str(x).strip()],
                "embedding": vec,
                "model_id": self._model_id,
                "data_version": data_version,
            }
        )
        state["items"] = items[-200:]
        write_json(self._path, state)


def _load_state(path: Path) -> dict[str, Any]:
    raw = read_json(path, default={"items": []})
    if not isinstance(raw, dict):
        return {"items": []}
    items = raw.get("items", [])
    raw["items"] = items if isinstance(items, list) else []
    return raw


def _embed(ollama: LlmConfig, model: str, text: str) -> list[float]:
    if requests is None:  # pragma: no cover
        raise RuntimeError("Missing dependency: requests. Install with: pip install -r requirements.txt")
    url = f"{ollama.base_url.rstrip('/')}/api/embeddings"
    payload = {"model": model, "prompt": text}
    resp = requests.post(url, json=payload, timeout=ollama.timeout_s)
    resp.raise_for_status()
    data = resp.json()
    emb = data.get("embedding")
    if not isinstance(emb, list) or not emb:
        raise RuntimeError("Embedding response missing vector")
    return [float(x) for x in emb]


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return -1.0
    return float(dot / (left_norm * right_norm))


def _normalize_query(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return normalized


def _is_sensitive_query(text: str) -> bool:
    low = text.lower()
    return bool(re.search(r"\b(password|passcode|secret|api key|token|ssn|social security|credit card|debit card|cvv)\b", low))
