from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.agent.memory import Memory
from app.agent import prompts
from app.services.llm import LlmConfig, LlmError, ask_llm
from app.utils.logger import warn
from app.models.reminder import Reminder
from app.utils.time import now_local_iso


def weekly_window(days: int = 7) -> tuple[str, str]:
    end = datetime.now().astimezone()
    start = end - timedelta(days=int(days))
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def weekly_insights(llm_cfg: LlmConfig, memory: Memory, days: int = 7) -> str:
    start, end = weekly_window(days)
    logs = memory.logs_in_last_days(days)
    header = f"Weekly insights window: {start} .. {end} (local time)"
    if not logs:
        return header + "\nNo logs yet."

    prompt = prompts.INSIGHTS_WEEKLY.format(logs=_format_logs(logs))
    try:
        raw = ask_llm(llm_cfg, prompt)
        data = _parse_json_obj(raw)
    except LlmError as e:
        warn(f"LLM insights failed; using fallback. ({e})")
        data = {"summary": "Not enough signal yet.", "patterns": [], "suggestions": []}

    lines = [header, f"Generated at: {now_local_iso()}", ""]
    lines.append(str(data.get("summary", "")).strip() or "Summary unavailable.")
    patterns = data.get("patterns", [])
    suggestions = data.get("suggestions", [])
    if isinstance(patterns, list) and patterns:
        lines.append("")
        lines.append("Patterns:")
        for p in patterns[:8]:
            lines.append(f"- {str(p).strip()}")
    if isinstance(suggestions, list) and suggestions:
        lines.append("")
        lines.append("Suggestions:")
        for s in suggestions[:8]:
            lines.append(f"- {str(s).strip()}")
    return "\n".join(lines).strip()


def weekly_review(
    llm_cfg: LlmConfig,
    memory: Memory,
    persona: dict | None = None,
    reminders: list[Reminder] | None = None,
    days: int = 7,
) -> str:
    start, end = weekly_window(days)
    logs = memory.logs_in_last_days(days)
    notes = memory.notes_in_last_days(days)
    header = f"Weekly review window: {start} .. {end} (local time)"
    if not logs and not notes:
        return header + "\nNo logs/notes yet."

    prompt = prompts.REVIEW_WEEKLY.format(
        persona=json.dumps(persona or {}, ensure_ascii=True),
        logs=_format_items(logs),
        notes=_format_items(notes),
    )
    try:
        raw = ask_llm(llm_cfg, prompt)
        data = _parse_json_obj(raw)
    except LlmError as e:
        warn(f"LLM weekly review failed; using fallback. ({e})")
        data = {"summary": "Not enough signal yet.", "patterns": [], "activities": []}

    summary = str(data.get("summary", "")).strip() or "Summary unavailable."
    patterns = data.get("patterns", [])
    activities = data.get("activities", [])

    lines = [header, f"Generated at: {now_local_iso()}", "", summary]
    if isinstance(patterns, list) and patterns:
        lines.append("")
        lines.append("Patterns:")
        for p in patterns[:6]:
            s = str(p).strip()
            if s:
                lines.append(f"- {s}")
    if isinstance(activities, list) and activities:
        lines.append("")
        lines.append("Activities:")
        for a in activities[:6]:
            s = str(a).strip()
            if s:
                lines.append(f"- {s}")

    if reminders:
        lines.append("")
        lines.append("Reminders (next 7 days):")
        for r in reminders[:6]:
            lines.append(f"- {r.text}")
    return "\n".join(lines).strip()


def _format_logs(logs) -> str:
    lines = [f"- {l.ts}: {l.text}" for l in logs if getattr(l, "text", "").strip()]
    return "\n".join(lines[-200:]) if lines else "- (none)"


def _format_items(items) -> str:
    # Privacy: no timestamps. Keep only text.
    lines = [f"- {getattr(i, 'text', '').strip()}" for i in items if getattr(i, "text", "").strip()]
    return "\n".join(lines[-250:]) if lines else "- (none)"


def _parse_json_obj(text: str) -> dict[str, Any]:
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
