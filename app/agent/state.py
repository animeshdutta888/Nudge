from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.storage import read_json, write_json, ensure_json_file
from app.utils.time import now_local_iso


@dataclass(frozen=True)
class PendingSave:
    kind: str  # log | note
    text: str
    reason: str


@dataclass(frozen=True)
class PendingPlan:
    project: str
    summary: str
    goals: list[str]
    reason: str


@dataclass(frozen=True)
class PendingToolAction:
    tool: str
    action: str
    payload: dict[str, Any]
    reason: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        ensure_json_file(
            self._path,
            {
                "pending_save": None,
                "pending_plan": None,
                "pending_tool_action": None,
                "autosave_enabled": True,
                "asked_questions": [],
                "last_question": None,
                "daily_checkin": {
                    "last_prompt_date": None,
                    "dismissed_date": None,
                },
            },
        )

    def _default_state(self) -> dict[str, Any]:
        return {
            "pending_save": None,
            "pending_plan": None,
            "pending_tool_action": None,
            "autosave_enabled": True,
            "asked_questions": [],
            "last_question": None,
            "daily_checkin": {
                "last_prompt_date": None,
                "dismissed_date": None,
            },
        }

    def autosave_enabled(self) -> bool:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            return True
        val = state.get("autosave_enabled", True)
        return bool(val)

    def set_autosave_enabled(self, enabled: bool) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["autosave_enabled"] = bool(enabled)
        write_json(self._path, state)

    def asked_question_ids(self) -> set[str]:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            return set()
        raw = state.get("asked_questions", [])
        if not isinstance(raw, list):
            return set()
        return {str(x) for x in raw if str(x).strip()}

    def mark_question_asked(self, qid: str, text: str) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        asked = state.get("asked_questions", [])
        if not isinstance(asked, list):
            asked = []
        qid_s = str(qid).strip()
        if qid_s and qid_s not in asked:
            asked.append(qid_s)
        state["asked_questions"] = asked[-200:]  # cap growth
        state["last_question"] = {"id": qid_s, "text": str(text).strip(), "ts": now_local_iso()}
        write_json(self._path, state)

    def last_question(self) -> dict[str, Any] | None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            return None
        raw = state.get("last_question")
        return raw if isinstance(raw, dict) else None

    def clear_last_question(self) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["last_question"] = None
        write_json(self._path, state)

    def reset_questions(self) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["asked_questions"] = []
        state["last_question"] = None
        write_json(self._path, state)

    def get_pending_save(self) -> PendingSave | None:
        state = read_json(self._path, default=self._default_state())
        raw = state.get("pending_save") if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind", "")).strip()
        text = str(raw.get("text", "")).strip()
        reason = str(raw.get("reason", "")).strip()
        if kind not in {"log", "note"} or not text:
            return None
        return PendingSave(kind=kind, text=text, reason=reason)

    def set_pending_save(self, pending: PendingSave | None) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["pending_save"] = None if pending is None else pending.__dict__
        if "autosave_enabled" not in state:
            state["autosave_enabled"] = True
        if "pending_plan" not in state:
            state["pending_plan"] = None
        if "asked_questions" not in state:
            state["asked_questions"] = []
        if "last_question" not in state:
            state["last_question"] = None
        if "pending_tool_action" not in state:
            state["pending_tool_action"] = None
        write_json(self._path, state)

    def get_pending_plan(self) -> PendingPlan | None:
        state = read_json(self._path, default=self._default_state())
        raw = state.get("pending_plan") if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return None
        project = str(raw.get("project", "")).strip()
        summary = str(raw.get("summary", "")).strip()
        reason = str(raw.get("reason", "")).strip()
        goals_raw = raw.get("goals", [])
        goals = [str(x).strip() for x in goals_raw if str(x).strip()] if isinstance(goals_raw, list) else []
        if not project or not goals:
            return None
        return PendingPlan(project=project, summary=summary, goals=goals, reason=reason)

    def set_pending_plan(self, pending: PendingPlan | None) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["pending_plan"] = None if pending is None else pending.__dict__
        if "pending_save" not in state:
            state["pending_save"] = None
        if "autosave_enabled" not in state:
            state["autosave_enabled"] = True
        if "asked_questions" not in state:
            state["asked_questions"] = []
        if "last_question" not in state:
            state["last_question"] = None
        if "pending_tool_action" not in state:
            state["pending_tool_action"] = None
        write_json(self._path, state)

    def get_pending_tool_action(self) -> PendingToolAction | None:
        state = read_json(self._path, default=self._default_state())
        raw = state.get("pending_tool_action") if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return None
        tool = str(raw.get("tool", "")).strip()
        action = str(raw.get("action", "")).strip()
        payload = raw.get("payload", {})
        reason = str(raw.get("reason", "")).strip()
        if not tool or not action or not isinstance(payload, dict):
            return None
        return PendingToolAction(tool=tool, action=action, payload=payload, reason=reason)

    def set_pending_tool_action(self, pending: PendingToolAction | None) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["pending_tool_action"] = None if pending is None else pending.__dict__
        if "pending_save" not in state:
            state["pending_save"] = None
        if "pending_plan" not in state:
            state["pending_plan"] = None
        if "autosave_enabled" not in state:
            state["autosave_enabled"] = True
        if "asked_questions" not in state:
            state["asked_questions"] = []
        if "last_question" not in state:
            state["last_question"] = None
        write_json(self._path, state)

    def daily_checkin_state(self) -> dict[str, Any]:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            return {"last_prompt_date": None, "dismissed_date": None}
        raw = state.get("daily_checkin")
        if not isinstance(raw, dict):
            return {"last_prompt_date": None, "dismissed_date": None}
        return {
            "last_prompt_date": raw.get("last_prompt_date"),
            "dismissed_date": raw.get("dismissed_date"),
        }

    def mark_daily_checkin_prompted(self, day_iso: str) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        raw = state.get("daily_checkin")
        daily = raw if isinstance(raw, dict) else {}
        daily["last_prompt_date"] = str(day_iso)
        state["daily_checkin"] = daily
        write_json(self._path, state)

    def dismiss_daily_checkin_for_day(self, day_iso: str) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        raw = state.get("daily_checkin")
        daily = raw if isinstance(raw, dict) else {}
        daily["dismissed_date"] = str(day_iso)
        daily["last_prompt_date"] = str(day_iso)
        state["daily_checkin"] = daily
        write_json(self._path, state)

    def clear_daily_checkin_dismissal(self, day_iso: str) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        raw = state.get("daily_checkin")
        daily = raw if isinstance(raw, dict) else {}
        if daily.get("dismissed_date") == str(day_iso):
            daily["dismissed_date"] = None
        state["daily_checkin"] = daily
        write_json(self._path, state)
