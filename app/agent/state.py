from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.services.storage import read_json, write_json, ensure_json_file
from app.utils.time import now_local_iso


def _next_action_id(prefix: str) -> str:
    return f"{prefix}_{now_local_iso().replace(':', '').replace('-', '').replace('+', '_')}"


def _base_action_dict(
    *,
    action_id: str,
    action_type: str,
    risk: str,
    requires_approval: bool,
    reason: str,
    payload: dict[str, Any],
    source: str,
    status: str = "pending",
    metadata: Optional[dict[str, Any]] = None,
    version: int = 1,
) -> dict[str, Any]:
    now = now_local_iso()
    return {
        "id": action_id,
        "action_id": action_id,
        "type": action_type,
        "risk": risk,
        "requires_approval": requires_approval,
        "reason": reason,
        "payload": payload,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "source": source,
        "metadata": metadata or {},
        "version": version,
    }


@dataclass(frozen=True)
class PendingSave:
    kind: str  # log | note
    text: str
    reason: str
    source: str = "local"
    risk: str = "low"

    def to_state_dict(self) -> dict[str, Any]:
        payload = {"kind": self.kind, "text": self.text}
        action = _base_action_dict(
            action_id=_next_action_id("act_save"),
            action_type="save_memory",
            risk=self.risk,
            requires_approval=True,
            reason=self.reason,
            payload=payload,
            source=self.source,
        )
        action["kind"] = self.kind
        action["text"] = self.text
        return action


@dataclass(frozen=True)
class PendingPlan:
    project: str
    summary: str
    goals: list[str]
    reason: str
    plan_kind: str = "project_plan"
    priorities: Optional[list[str]] = None
    carry_forward: Optional[list[str]] = None
    previous_plan_date: str = ""
    source: str = "local"
    risk: str = "low"

    def to_state_dict(self) -> dict[str, Any]:
        priorities = self.priorities if isinstance(self.priorities, list) else self.goals
        payload = {
            "plan_kind": self.plan_kind,
            "project": self.project,
            "summary": self.summary,
            "goals": self.goals,
            "priorities": priorities,
            "carry_forward": self.carry_forward or [],
            "previous_plan_date": self.previous_plan_date,
        }
        action = _base_action_dict(
            action_id=_next_action_id("act_plan"),
            action_type="create_daily_plan" if self.plan_kind == "daily_plan" else "create_project",
            risk=self.risk,
            requires_approval=True,
            reason=self.reason,
            payload=payload,
            source=self.source,
        )
        action["plan_kind"] = self.plan_kind
        action["project"] = self.project
        action["summary"] = self.summary
        action["goals"] = self.goals
        action["priorities"] = priorities
        action["carry_forward"] = self.carry_forward or []
        action["previous_plan_date"] = self.previous_plan_date
        return action


@dataclass(frozen=True)
class PendingToolAction:
    tool: str
    action: str
    payload: dict[str, Any]
    reason: str
    source: str = "local"
    risk: str = "medium"

    def to_state_dict(self) -> dict[str, Any]:
        payload = {"tool": self.tool, "action": self.action, "payload": self.payload}
        action = _base_action_dict(
            action_id=_next_action_id("act_tool"),
            action_type=f"{self.tool}_{self.action}",
            risk=self.risk,
            requires_approval=True,
            reason=self.reason,
            payload=payload,
            source=self.source,
        )
        action["tool"] = self.tool
        action["action"] = self.action
        return action


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
                "close_day_session": None,
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
            "close_day_session": None,
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

    def last_question(self) -> Optional[dict[str, Any]]:
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

    def get_pending_save(self) -> Optional[PendingSave]:
        state = read_json(self._path, default=self._default_state())
        raw = state.get("pending_save") if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return None
        payload = raw.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        kind = str(payload_dict.get("kind", raw.get("kind", ""))).strip()
        text = str(payload_dict.get("text", raw.get("text", ""))).strip()
        reason = str(raw.get("reason", "")).strip()
        if kind not in {"log", "note"} or not text:
            return None
        return PendingSave(kind=kind, text=text, reason=reason, source=str(raw.get("source", "local")).strip() or "local", risk=str(raw.get("risk", "low")).strip() or "low")

    def set_pending_save(self, pending: Optional[PendingSave]) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["pending_save"] = None if pending is None else pending.to_state_dict()
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

    def get_pending_plan(self) -> Optional[PendingPlan]:
        state = read_json(self._path, default=self._default_state())
        raw = state.get("pending_plan") if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return None
        payload = raw.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}
        project = str(payload_dict.get("project", raw.get("project", ""))).strip()
        summary = str(payload_dict.get("summary", raw.get("summary", ""))).strip()
        reason = str(raw.get("reason", "")).strip()
        goals_raw = payload_dict.get("goals", raw.get("goals", []))
        goals = [str(x).strip() for x in goals_raw if str(x).strip()] if isinstance(goals_raw, list) else []
        if not project or not goals:
            return None
        priorities_raw = payload_dict.get("priorities", raw.get("priorities", goals))
        carry_forward_raw = payload_dict.get("carry_forward", raw.get("carry_forward", []))
        return PendingPlan(
            project=project,
            summary=summary,
            goals=goals,
            reason=reason,
            plan_kind=str(payload_dict.get("plan_kind", raw.get("plan_kind", "project_plan"))).strip() or "project_plan",
            priorities=[str(x).strip() for x in priorities_raw if str(x).strip()] if isinstance(priorities_raw, list) else goals,
            carry_forward=[str(x).strip() for x in carry_forward_raw if str(x).strip()] if isinstance(carry_forward_raw, list) else [],
            previous_plan_date=str(payload_dict.get("previous_plan_date", raw.get("previous_plan_date", ""))).strip(),
            source=str(raw.get("source", "local")).strip() or "local",
            risk=str(raw.get("risk", "low")).strip() or "low",
        )

    def set_pending_plan(self, pending: Optional[PendingPlan]) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["pending_plan"] = None if pending is None else pending.to_state_dict()
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

    def get_pending_tool_action(self) -> Optional[PendingToolAction]:
        state = read_json(self._path, default=self._default_state())
        raw = state.get("pending_tool_action") if isinstance(state, dict) else None
        if not isinstance(raw, dict):
            return None
        payload_raw = raw.get("payload")
        payload_dict = payload_raw if isinstance(payload_raw, dict) else {}
        tool = str(payload_dict.get("tool", raw.get("tool", ""))).strip()
        action = str(payload_dict.get("action", raw.get("action", ""))).strip()
        payload = payload_dict.get("payload", raw.get("payload", {}))
        reason = str(raw.get("reason", "")).strip()
        if not tool or not action or not isinstance(payload, dict):
            return None
        return PendingToolAction(
            tool=tool,
            action=action,
            payload=payload,
            reason=reason,
            source=str(raw.get("source", "local")).strip() or "local",
            risk=str(raw.get("risk", "medium")).strip() or "medium",
        )

    def set_pending_tool_action(self, pending: Optional[PendingToolAction]) -> None:
        state = read_json(self._path, default=self._default_state())
        if not isinstance(state, dict):
            state = self._default_state()
        state["pending_tool_action"] = None if pending is None else pending.to_state_dict()
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
