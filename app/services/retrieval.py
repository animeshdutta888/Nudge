from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.storage import read_json, write_json
from app.services.llm import LlmConfig, LlmError

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    requests = None

try:
    import faiss  # type: ignore
    import numpy as np  # type: ignore
except ModuleNotFoundError as e:  # pragma: no cover
    faiss = None
    np = None
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


@dataclass(frozen=True)
class RetrievalItem:
    id: int
    kind: str  # log | note
    ts: str
    text: str


@dataclass(frozen=True)
class RetrievalResult:
    item: RetrievalItem
    score: float


class Retriever:
    """
    Offline RAG store:
    - Embeddings via local Ollama embedding model (e.g. nomic-embed-text)
    - FAISS index for similarity search (cosine via normalized inner product)
    - JSON mapping id -> item (text + metadata)
    """

    def __init__(self, index_path: Path, map_path: Path, ollama: LlmConfig, embed_model: str) -> None:
        self._index_path = index_path
        self._map_path = map_path
        self._ollama = ollama
        self._embed_model = embed_model

        self._enabled = faiss is not None and np is not None and requests is not None
        self._state = _load_or_init_state(map_path)
        self._index = _load_index(index_path) if self._enabled else None
        if self._enabled and self._index is not None and int(self._state.get("dim") or 0) <= 0:
            self._state["dim"] = int(getattr(self._index, "d", 0) or 0)

    def add(self, kind: str, ts: str, text: str) -> int:
        item_id = int(self._state["next_id"])
        self._state["next_id"] = item_id + 1

        if not self._enabled:
            # Still persist the text metadata locally; retrieval will be disabled.
            self._state["items"][str(item_id)] = {"kind": kind, "ts": ts, "text": text}
            self.save()
            return item_id

        vec = self._embed(text)
        if self._index is None:
            dim = int(vec.shape[1])
            self._state["dim"] = dim
            self._index = _create_index(dim)
        ids = np.array([item_id], dtype="int64")
        self._index.add_with_ids(vec, ids)
        self._state["items"][str(item_id)] = {"kind": kind, "ts": ts, "text": text}
        self.save()
        return item_id

    def retrieve(self, query: str, k: int = 5) -> list[RetrievalResult]:
        if not self._enabled:
            return []
        if self._index is None or self._index.ntotal == 0:
            return []
        q = self._embed(query)
        k = max(1, int(k))
        distances, ids = self._index.search(q, k)
        out: list[RetrievalResult] = []
        for dist, item_id in zip(distances[0].tolist(), ids[0].tolist()):
            if item_id == -1:
                continue
            raw = self._state["items"].get(str(int(item_id)))
            if not isinstance(raw, dict):
                continue
            item = RetrievalItem(
                id=int(item_id),
                kind=str(raw.get("kind", "")),
                ts=str(raw.get("ts", "")),
                text=str(raw.get("text", "")),
            )
            out.append(RetrievalResult(item=item, score=float(dist)))
        return out

    def save(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        if self._enabled and self._index is not None:
            faiss.write_index(self._index, str(self._index_path))
        write_json(self._map_path, self._state)

    def _embed(self, text: str):
        vec = _ollama_embed(self._ollama, self._embed_model, text)
        arr = np.asarray(vec, dtype="float32")
        if arr.ndim != 1:
            raise RuntimeError("Embedding shape mismatch.")
        # Normalize so inner product ~= cosine similarity.
        norm = float(np.linalg.norm(arr)) or 1.0
        arr = arr / norm
        return arr.reshape(1, -1)


def _load_or_init_state(path: Path) -> dict[str, Any]:
    state = read_json(path, default={"next_id": 1, "dim": 0, "items": {}})
    if not isinstance(state, dict):
        return {"next_id": 1, "dim": 0, "items": {}}
    if "next_id" not in state or not isinstance(state.get("next_id"), int):
        state["next_id"] = 1
    if "dim" not in state or not isinstance(state.get("dim"), int):
        state["dim"] = 0
    if "items" not in state or not isinstance(state.get("items"), dict):
        state["items"] = {}
    return state


def _load_index(path: Path):
    if not path.exists():
        return None
    idx = faiss.read_index(str(path))
    if isinstance(idx, faiss.IndexIDMap2):
        return idx
    return faiss.IndexIDMap2(idx)


def _create_index(dim: int):
    # Inner product on normalized vectors -> cosine similarity
    base = faiss.IndexFlatIP(dim)
    return faiss.IndexIDMap2(base)


def _ollama_embed(ollama: LlmConfig, model: str, text: str) -> list[float]:
    base = ollama.base_url.rstrip("/")
    timeout = ollama.timeout_s

    if requests is None:  # pragma: no cover
        raise RuntimeError("Missing dependency: requests. Install with: pip install -r requirements.txt")

    url = f"{base}/api/embeddings"
    payload = {"model": model, "prompt": text}
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach local Ollama embeddings at {base}: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama embeddings error {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from Ollama embeddings: {e}") from e

    emb = data.get("embedding")
    if not isinstance(emb, list) or not emb:
        raise RuntimeError("Ollama did not return an embedding. Run: `ollama pull nomic-embed-text`.")
    return [float(x) for x in emb]
