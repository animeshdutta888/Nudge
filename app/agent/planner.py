from __future__ import annotations

from dataclasses import dataclass
import json

from app.agent import prompts
from app.services.llm import LlmConfig, LlmError, ask_llm


@dataclass(frozen=True)
class Plan:
    kind: str  # log | note | question | reflection
    clean_text: str
    explicit: bool  # True only when user explicitly asked to store (log:/note:/save:/remember:)


class Planner:
    def __init__(self, llm_cfg: LlmConfig) -> None:
        self._llm_cfg = llm_cfg

    def classify(self, user_text: str) -> Plan:
        # Hard overrides for explicit prefixes (fast path, deterministic).
        raw = user_text.strip()
        low = raw.lower()
        if low.startswith("log:"):
            return Plan(kind="log", clean_text=raw.split(":", 1)[1].strip(), explicit=True)
        if low.startswith("note:"):
            return Plan(kind="note", clean_text=raw.split(":", 1)[1].strip(), explicit=True)
        if low.startswith("save:"):
            return Plan(kind="note", clean_text=raw.split(":", 1)[1].strip(), explicit=True)
        if low.startswith("remember:"):
            return Plan(kind="note", clean_text=raw.split(":", 1)[1].strip(), explicit=True)
        if _looks_like_question(raw):
            return Plan(kind="question", clean_text=raw, explicit=False)

        prompt = prompts.CLASSIFY.format(user=user_text)
        try:
            out = ask_llm(self._llm_cfg, prompt)
            data = _parse_json(out)
            kind = str(data.get("kind", "")).strip().lower()
            clean = str(data.get("clean_text", "")).strip() or raw
            if kind in {"log", "note", "question", "reflection"}:
                # Never store implicitly. We can later *suggest* saving with approval.
                return Plan(kind=kind, clean_text=clean, explicit=False)
        except LlmError:
            pass

        # Fallback heuristic (still offline) if LLM isn't reachable.
        if raw.endswith("?"):
            return Plan(kind="question", clean_text=raw, explicit=False)
        return Plan(kind="reflection", clean_text=raw, explicit=False)


def _parse_json(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _looks_like_question(text: str) -> bool:
    raw = text.strip().lower()
    if not raw:
        return False
    if raw.endswith("?"):
        return True
    starters = (
        "who ",
        "what ",
        "when ",
        "where ",
        "why ",
        "how ",
        "do i ",
        "am i ",
        "did i ",
        "can you ",
        "could you ",
        "would you ",
        "should i ",
    )
    return any(raw.startswith(s) for s in starters)
