from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from app.agent import prompts
from app.models.log import LogEntry
from app.models.note import Note
from app.services.llm import LlmConfig, LlmError, ask_llm
from app.utils.logger import warn
from app.utils.time import now_local_iso

from .schema import Persona


def build_persona_from_logs(
    llm_cfg: LlmConfig, logs: list[LogEntry], notes: list[Note], existing: dict[str, Any]
) -> dict[str, Any]:
    source = _source_text(logs, notes)
    prompt = prompts.PERSONA_UPDATE.format(logs=_format_logs(logs), notes=_format_notes(notes))

    try:
        raw = ask_llm(llm_cfg, prompt)
    except LlmError as e:
        warn(f"LLM persona extraction failed; keeping existing persona. ({e})")
        return _prune_to_source(_normalize_persona(existing), source)

    data = _parse_json_object(raw)
    normalized = _prune_to_source(_normalize_persona(existing), source)
    filtered = _filter_extracted_to_source(data, source)
    merged = _merge_persona(normalized, filtered)
    merged["updated_at"] = now_local_iso()
    return merged


def _normalize_persona(p: dict[str, Any]) -> dict[str, Any]:
    persona = Persona()
    base = asdict(persona)
    if isinstance(p, dict):
        base.update({k: p.get(k, base[k]) for k in base.keys()})
    base["interests"] = _uniq_strs(base.get("interests", []))
    base["habits"] = _uniq_strs(base.get("habits", []))
    base["current_focus"] = _uniq_strs(base.get("current_focus", []))
    if not isinstance(base.get("mood_trends"), str):
        base["mood_trends"] = ""
    return base


def _merge_persona(existing: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(extracted, dict):
        return existing

    interests = extracted.get("interests")
    habits = extracted.get("habits")
    mood_trends = extracted.get("mood_trends")
    current_focus = extracted.get("current_focus")

    if isinstance(interests, list):
        existing["interests"] = _uniq_strs([str(x) for x in interests])
    if isinstance(habits, list):
        existing["habits"] = _uniq_strs([str(x) for x in habits])
    if isinstance(mood_trends, str):
        existing["mood_trends"] = mood_trends.strip()
    if isinstance(current_focus, list):
        existing["current_focus"] = _uniq_strs([str(x) for x in current_focus])

    return existing


def _format_logs(logs: list[LogEntry]) -> str:
    lines = [f"- {l.ts}: {l.text}" for l in logs if l.text.strip()]
    return "\n".join(lines[-50:]) if lines else "- (no logs)"


def _format_notes(notes: list[Note]) -> str:
    lines = [f"- {n.ts}: {n.text}" for n in notes if n.text.strip()]
    return "\n".join(lines[-50:]) if lines else "- (no notes)"


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = text[start : end + 1]
    try:
        obj = json.loads(snippet)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _uniq_strs(items: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _source_text(logs: list[LogEntry], notes: list[Note]) -> str:
    parts: list[str] = []
    for l in logs:
        if l.text.strip():
            parts.append(l.text.strip())
    for n in notes:
        if n.text.strip():
            parts.append(n.text.strip())
    return "\n".join(parts).lower()


def _filter_extracted_to_source(extracted: dict[str, Any], source: str) -> dict[str, Any]:
    if not isinstance(extracted, dict):
        return {}
    out: dict[str, Any] = {}

    interests = extracted.get("interests")
    habits = extracted.get("habits")
    current_focus = extracted.get("current_focus")
    mood_trends = extracted.get("mood_trends")

    if isinstance(interests, list):
        out["interests"] = [s for s in _uniq_strs([str(x) for x in interests]) if _grounded(s, source)]
    if isinstance(habits, list):
        out["habits"] = [s for s in _uniq_strs([str(x) for x in habits]) if _grounded(s, source)]
    if isinstance(current_focus, list):
        out["current_focus"] = [s for s in _uniq_strs([str(x) for x in current_focus]) if _grounded(s, source)]
    if isinstance(mood_trends, str) and _grounded(mood_trends, source):
        out["mood_trends"] = mood_trends.strip()
    else:
        out["mood_trends"] = ""

    return out


def _prune_to_source(persona: dict[str, Any], source: str) -> dict[str, Any]:
    # Remove hallucinated persona entries that don't appear in the source text.
    for k in ("interests", "habits", "current_focus"):
        items = persona.get(k, [])
        if isinstance(items, list):
            persona[k] = [s for s in _uniq_strs(items) if _grounded(s, source)]
    mood = persona.get("mood_trends", "")
    if isinstance(mood, str) and mood and not _grounded(mood, source):
        persona["mood_trends"] = ""
    return persona


def _grounded(phrase: str, source: str) -> bool:
    p = phrase.strip().lower()
    if not p:
        return False
    # Simple grounding: require substring match in user's own text.
    return p in source
