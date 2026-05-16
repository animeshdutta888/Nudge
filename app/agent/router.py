from __future__ import annotations

from dataclasses import dataclass
import json

from app.agent import prompts
from app.services.llm import LlmConfig, LlmError, ask_llm


_VALID_INTENTS = {
    "none",
    "save_note",
    "save_log",
    "save_candidate",
    "add_reminder",
    "list_reminders",
    "complete_reminder",
    "add_project",
    "add_goal",
    "list_projects",
    "show_project",
    "complete_goal",
    "show_persona",
    "show_priorities",
    "show_insights",
    "notes_create",
    "notes_search",
    "notes_list",
    "filesystem_list",
    "filesystem_read",
    "shell_run",
    "approve",
    "skip",
}


@dataclass(frozen=True)
class RoutedIntent:
    intent: str
    text: str = ""
    project: str = ""
    goal: str = ""
    goal_index: int = 0
    reminder_id: int = 0
    when: str = ""
    query: str = ""
    path: str = ""
    command: str = ""
    reason: str = ""


class IntentRouter:
    def __init__(self, llm_cfg: LlmConfig) -> None:
        self._llm_cfg = llm_cfg

    def route(self, user_text: str, *, persona: dict, context: str = "") -> RoutedIntent:
        prompt = prompts.ROUTE_INTENT.format(
            persona=json.dumps(persona or {}, ensure_ascii=True, indent=2),
            context=context or "- none",
            user=user_text.strip(),
        )
        try:
            raw = ask_llm(self._llm_cfg, prompt)
        except LlmError:
            return RoutedIntent(intent="none")
        data = _parse_json(raw)
        intent = str(data.get("intent", "none")).strip().lower()
        if intent not in _VALID_INTENTS:
            return RoutedIntent(intent="none")
        return RoutedIntent(
            intent=intent,
            text=str(data.get("text", "")).strip(),
            project=str(data.get("project", "")).strip(),
            goal=str(data.get("goal", "")).strip(),
            goal_index=_to_int(data.get("goal_index")),
            reminder_id=_to_int(data.get("reminder_id")),
            when=str(data.get("when", "")).strip(),
            query=str(data.get("query", "")).strip(),
            path=str(data.get("path", "")).strip(),
            command=str(data.get("command", "")).strip(),
            reason=str(data.get("reason", "")).strip(),
        )


def _parse_json(text: str) -> dict:
    clean = text.strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        obj = json.loads(clean[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
