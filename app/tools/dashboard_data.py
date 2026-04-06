from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from app.services.conversations import load_conversations
from app.services.storage import read_json
from app.agent.state import StateStore
from app.tools.projects import load_projects
from app.tools.repair import recent_items
from app.utils.time import parse_iso_to_local_date, today_local_date


def build_dashboard_payload(data_dir: Path) -> dict[str, Any]:
    logs = _load_list(data_dir / "logs.json")
    notes = _load_list(data_dir / "notes.json")
    reminders = _load_list(data_dir / "reminders.json")
    persona = read_json(data_dir / "persona.json", default={})
    projects = load_projects(data_dir / "projects.json")
    conversations = load_conversations(data_dir / "conversations.json", limit=80)
    recent_logs = recent_items(data_dir / "logs.json", limit=10)
    recent_notes = recent_items(data_dir / "notes.json", limit=10)
    daily_checkin = _daily_checkin_prompt(data_dir, logs)

    today = today_local_date()
    logs_today = [x for x in logs if _same_day(x.get("ts", ""), today)]
    notes_this_week = [x for x in notes if _in_last_days(x.get("ts", ""), 7)]
    open_reminders = [x for x in reminders if not bool(x.get("done", False))]

    log_days = Counter()
    for item in logs:
        day = parse_iso_to_local_date(str(item.get("ts", "")))
        if day is not None:
            log_days[day.isoformat()] += 1

    top_interests = _as_list(persona.get("interests"))[:6]
    top_focus = _as_list(persona.get("current_focus"))[:4]

    return {
        "summary": {
            "logs_total": len(logs),
            "notes_total": len(notes),
            "conversations_total": len(conversations),
            "logs_today": len(logs_today),
            "notes_this_week": len(notes_this_week),
            "open_reminders": len(open_reminders),
            "projects_total": len(projects),
        },
        "persona": persona if isinstance(persona, dict) else {},
        "focus": top_focus,
        "interests": top_interests,
        "recent_logs": recent_logs,
        "recent_notes": recent_notes,
        "recent_conversations": [_clean_conversation(x) for x in reversed(conversations[-24:])],
        "reminders": open_reminders[:12],
        "projects": projects[:8],
        "timeline": _timeline(logs, notes, reminders, conversations),
        "activity_by_day": [
            {"day": day, "count": count}
            for day, count in sorted(log_days.items())[-14:]
        ],
        "pending_action": _pending_action(data_dir / "state.json"),
        "daily_checkin": daily_checkin,
    }


def search_dashboard_card(data_dir: Path, card: str, query: str) -> dict[str, Any]:
    q = (query or "").strip()
    card_key = (card or "").strip().lower()
    if not q:
        return {}

    logs = _load_list(data_dir / "logs.json")
    notes = _load_list(data_dir / "notes.json")
    reminders = _load_list(data_dir / "reminders.json")
    projects = load_projects(data_dir / "projects.json")
    conversations = load_conversations(data_dir / "conversations.json", limit=500)
    timeline = _timeline(logs, notes, reminders, conversations, full=True)

    if card_key == "memory":
        return {
            "recent_logs": _search_memory_items(logs, q, limit=30),
            "recent_notes": _search_memory_items(notes, q, limit=30),
        }
    if card_key == "projects":
        return {"projects": _search_projects(projects, q, limit=20)}
    if card_key == "timeline":
        return {"timeline": _search_items(timeline, q, text_keys=("kind", "text"), limit=40, preserve_order=True)}
    if card_key == "reminders":
        return {"reminders": _search_items(reminders, q, text_keys=("text", "due_ts"), limit=30)}
    if card_key == "conversation":
        return {"recent_conversations": [_clean_conversation(x) for x in _search_items(conversations, q, text_keys=("user", "assistant", "source"), limit=40)]}
    return {}


def _load_list(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    return data if isinstance(data, list) else []


def _same_day(ts: str, target) -> bool:
    d = parse_iso_to_local_date(str(ts))
    return d == target if d is not None else False


def _in_last_days(ts: str, days: int) -> bool:
    d = parse_iso_to_local_date(str(ts))
    if d is None:
        return False
    return d.toordinal() >= today_local_date().toordinal() - max(0, int(days)) + 1


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _timeline(logs, notes, reminders, conversations, full: bool = False) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in logs if isinstance(logs, list) else []:
        if isinstance(item, dict):
            items.append({"kind": "log", "text": str(item.get("text", "")), "ts": str(item.get("ts", ""))})
    for item in notes if isinstance(notes, list) else []:
        if isinstance(item, dict):
            items.append({"kind": "note", "text": str(item.get("text", "")), "ts": str(item.get("ts", ""))})
    for item in reminders if isinstance(reminders, list) else []:
        if isinstance(item, dict):
            items.append({"kind": "reminder", "text": str(item.get("text", "")), "ts": str(item.get("created_ts", ""))})
    for item in conversations if isinstance(conversations, list) else []:
        if isinstance(item, dict):
            items.append({"kind": "chat", "text": str(item.get("user", "")), "ts": str(item.get("ts", ""))})
    ordered = sorted(items, key=lambda x: x.get("ts", ""), reverse=True)
    return ordered if full else ordered[:18]


def _search_items(
    items: list[dict[str, Any]],
    query: str,
    text_keys: tuple[str, ...],
    limit: int,
    preserve_order: bool = False,
) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    if not tokens:
        return []
    matches: list[dict[str, Any]] = []
    source = items if preserve_order else reversed(items)
    for item in source:
        if not isinstance(item, dict):
            continue
        hay = " ".join(str(item.get(k, "")) for k in text_keys).lower()
        if all(token in hay for token in tokens):
            matches.append(item)
        if len(matches) >= limit:
            break
    return matches


def _search_projects(projects: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    if not tokens:
        return []
    matches: list[dict[str, Any]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        goals = project.get("goals", [])
        goal_text = " ".join(str(g.get("text", "")) for g in goals if isinstance(g, dict)) if isinstance(goals, list) else ""
        hay = f"{project.get('name', '')} {project.get('status', '')} {goal_text}".lower()
        if all(token in hay for token in tokens):
            matches.append(project)
        if len(matches) >= limit:
            break
    return matches


def _search_memory_items(items: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    if not tokens:
        return []
    matches: list[dict[str, Any]] = []
    total = len(items)
    for idx in range(total - 1, -1, -1):
        item = items[idx]
        if not isinstance(item, dict):
            continue
        hay = str(item.get("text", "")).lower()
        if all(token in hay for token in tokens):
            payload = dict(item)
            payload["recent_index"] = total - idx
            matches.append(payload)
        if len(matches) >= limit:
            break
    return matches


def _tokens(text: str) -> list[str]:
    return [part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if part]


def _clean_conversation(item: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(item)
    assistant = str(cleaned.get("assistant", ""))
    for marker in (
        "\n\n(I noticed something that may be worth remembering.",
        "\n\n(Pending save:",
        "\n\nApprove to create this project and goals, or skip to ignore.",
    ):
        idx = assistant.find(marker)
        if idx != -1:
            assistant = assistant[:idx].strip()
            break
    cleaned["assistant"] = assistant
    return cleaned


def _pending_action(path: Path) -> dict[str, Any] | None:
    raw = read_json(path, default={})
    if not isinstance(raw, dict):
        return None
    pending_plan = raw.get("pending_plan")
    if isinstance(pending_plan, dict):
        return {"kind": "plan", **pending_plan}
    pending_save = raw.get("pending_save")
    if isinstance(pending_save, dict):
        return {"kind": "save", **pending_save}
    return None


def _daily_checkin_prompt(data_dir: Path, logs: list[dict[str, Any]]) -> dict[str, Any]:
    today = today_local_date().isoformat()
    has_completed = any(
        isinstance(item, dict)
        and _same_day(str(item.get("ts", "")), today_local_date())
        and str(item.get("text", "")).strip().lower().startswith("daily check-in:")
        for item in logs
    )
    state = StateStore(data_dir / "state.json").daily_checkin_state()
    dismissed_date = str(state.get("dismissed_date") or "")
    last_prompt_date = str(state.get("last_prompt_date") or "")
    should_prompt = not has_completed and dismissed_date != today and last_prompt_date != today
    return {
        "should_prompt": should_prompt,
        "completed_today": has_completed,
        "dismissed_today": dismissed_date == today,
    }
