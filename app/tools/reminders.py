from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.models.reminder import Reminder
from app.services.storage import read_json, write_json
from app.utils.time import now_local_iso


def add_reminder(reminders_path: Path, text: str, due_ts: str | None) -> Reminder:
    reminders = _load_list(reminders_path)
    next_id = (max((int(r.get("id", 0)) for r in reminders if isinstance(r, dict)), default=0) + 1) if reminders else 1
    r = Reminder(
        id=int(next_id),
        created_ts=now_local_iso(),
        due_ts=due_ts,
        text=text.strip(),
        done=False,
        done_ts=None,
    )
    reminders.append(asdict(r))
    write_json(reminders_path, reminders)
    return r


def list_reminders(reminders_path: Path, upcoming_days: int = 14) -> list[Reminder]:
    raw = _load_list(reminders_path)
    out: list[Reminder] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            Reminder(
                id=int(item.get("id", 0) or 0),
                created_ts=str(item.get("created_ts", "")),
                due_ts=(str(item.get("due_ts")) if item.get("due_ts") is not None else None),
                text=str(item.get("text", "")),
                done=bool(item.get("done", False)),
                done_ts=(str(item.get("done_ts")) if item.get("done_ts") is not None else None),
            )
        )

    now = datetime.now().astimezone()
    horizon = now + timedelta(days=int(upcoming_days))

    def key(r: Reminder) -> tuple[int, str]:
        if r.due_ts:
            return (0, r.due_ts)
        return (1, r.created_ts)

    filtered: list[Reminder] = []
    for r in out:
        if r.done:
            continue
        if r.due_ts:
            dt = _safe_parse_dt(r.due_ts)
            if dt is not None and dt <= horizon:
                filtered.append(r)
            elif dt is None:
                filtered.append(r)
        else:
            filtered.append(r)
    return sorted(filtered, key=key)


def mark_done(reminders_path: Path, reminder_id: int) -> bool:
    reminders = _load_list(reminders_path)
    changed = False
    for item in reminders:
        if not isinstance(item, dict):
            continue
        if int(item.get("id", 0) or 0) == int(reminder_id):
            item["done"] = True
            item["done_ts"] = now_local_iso()
            changed = True
            break
    if changed:
        write_json(reminders_path, reminders)
    return changed


def parse_remind_command(raw: str) -> tuple[str | None, str]:
    """
    Very small local parser.

    Supported:
    - "YYYY-MM-DD <text>"
    - "YYYY-MM-DD HH:MM <text>"
    - "today [HH:MM] <text>"
    - "tomorrow [HH:MM] <text>"
    - "in N days|hours|minutes <text>"
    Otherwise: due_ts=None, text=raw.
    """
    s = raw.strip()
    if not s:
        return None, ""

    now = datetime.now().astimezone()

    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}))?\s+(.+)$", s)
    if m:
        date_s, time_s, msg = m.group(1), m.group(2), m.group(3)
        hh, mm = (9, 0) if not time_s else (int(time_s.split(":")[0]), int(time_s.split(":")[1]))
        dt = datetime.fromisoformat(date_s).replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=now.tzinfo)
        return dt.isoformat(timespec="seconds"), msg.strip()

    m = re.match(r"^(today|tomorrow)(?:\s+(\d{1,2}:\d{2}))?\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        day_s, time_s, msg = m.group(1).lower(), m.group(2), m.group(3)
        base = now.date() if day_s == "today" else (now + timedelta(days=1)).date()
        hh, mm = (9, 0) if not time_s else (int(time_s.split(":")[0]), int(time_s.split(":")[1]))
        dt = datetime(base.year, base.month, base.day, hh, mm, tzinfo=now.tzinfo)
        return dt.isoformat(timespec="seconds"), msg.strip()

    m = re.match(r"^in\s+(\d+)\s+(days?|hours?|minutes?)\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        msg = m.group(3).strip()
        if unit.startswith("day"):
            dt = now + timedelta(days=n)
        elif unit.startswith("hour"):
            dt = now + timedelta(hours=n)
        else:
            dt = now + timedelta(minutes=n)
        return dt.isoformat(timespec="seconds"), msg

    return None, s


def _safe_parse_dt(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


def _load_list(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    return data if isinstance(data, list) else []
