from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    requests = None


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    timeout_s: float = 25.0


class LlmError(RuntimeError):
    pass


def ask_llm(cfg: LlmConfig, prompt: str) -> str:
    """
    Calls Ollama local API (no cloud):
    POST {base_url}/api/generate
    """
    url = f"{cfg.base_url}/api/generate"
    payload: dict[str, Any] = {
        "model": cfg.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.6},
    }

    if requests is None:
        raise LlmError("Missing dependency: requests. Install it with `pip install -r requirements.txt`.")

    try:
        resp = requests.post(url, json=payload, timeout=cfg.timeout_s)
    except requests.RequestException as e:
        raise LlmError(f"Could not reach Ollama at {cfg.base_url}: {e}") from e

    if resp.status_code != 200:
        raise LlmError(f"Ollama error {resp.status_code}: {resp.text[:400]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise LlmError(f"Invalid JSON response from Ollama: {e}") from e

    if not isinstance(data, dict) or "response" not in data:
        raise LlmError("Unexpected Ollama response shape (missing 'response').")

    return str(data["response"]).strip()
