from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.models.reminder import Reminder
from app.services.storage import read_json, write_json
from app.utils.time import now_local_iso


@dataclass(frozen=True)
class ReminderResolution:
    due_ts: str | None
    text: str
    error: str = ""


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


def resolve_reminder_request(raw_text: str, *, when_hint: str = "", text_hint: str = "") -> ReminderResolution:
    candidates: list[tuple[str | None, str]] = []
    stale_candidate = False

    when_hint = when_hint.strip()
    text_hint = text_hint.strip()
    raw_text = raw_text.strip()

    if when_hint and text_hint:
        parsed_when = _parse_time_fragment(when_hint)
        if parsed_when is not None:
            candidates.append((parsed_when, text_hint))
        else:
            candidates.append(parse_remind_command(f"{when_hint} {text_hint}"))

    if text_hint:
        candidates.append(parse_remind_command(text_hint))

    normalized_raw = _normalize_reminder_request_text(raw_text)
    if normalized_raw:
        candidates.append(parse_remind_command(normalized_raw))

    for due_ts, body in candidates:
        clean_body = body.strip()
        if due_ts and clean_body:
            if _is_future_due(due_ts):
                return ReminderResolution(due_ts=due_ts, text=clean_body)
            stale_candidate = True

    fallback_text = text_hint or _strip_reminder_prefix(raw_text)
    if stale_candidate:
        return ReminderResolution(due_ts=None, text=fallback_text.strip(), error="That reminder time looks like it is already in the past.")
    if fallback_text:
        return ReminderResolution(due_ts=None, text=fallback_text.strip())
    return ReminderResolution(due_ts=None, text="", error="Tell me what you want to be reminded about.")


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


def snooze_reminder(reminders_path: Path, reminder_id: int, minutes: int = 1) -> bool:
    reminders = _load_list(reminders_path)
    changed = False
    snooze_for = max(1, int(minutes))
    next_due = (datetime.now().astimezone() + timedelta(minutes=snooze_for)).isoformat(timespec="seconds")
    for item in reminders:
        if not isinstance(item, dict):
            continue
        if int(item.get("id", 0) or 0) == int(reminder_id):
            item["done"] = False
            item["done_ts"] = None
            item["due_ts"] = next_due
            changed = True
            break
    if changed:
        write_json(reminders_path, reminders)
    return changed


def next_due_reminder(reminders_path: Path) -> Reminder | None:
    now = datetime.now().astimezone()
    due_items: list[Reminder] = []
    for reminder in list_reminders(reminders_path, upcoming_days=365):
        if reminder.done:
            continue
        if reminder.due_ts is None:
            continue
        due_dt = _safe_parse_dt(reminder.due_ts)
        if due_dt is not None and due_dt <= now:
            due_items.append(reminder)
    if not due_items:
        return None
    return sorted(due_items, key=lambda item: item.due_ts or item.created_ts)[0]


def parse_remind_command(raw: str) -> tuple[str | None, str]:
    """
    Very small local parser.

    Supported:
    - "YYYY-MM-DD <text>"
    - "YYYY-MM-DD HH:MM <text>"
    - "today [HH:MM] <text>"
    - "tomorrow [HH:MM] <text>"
    - "in N days|hours|minutes|seconds <text>"
    Otherwise: due_ts=None, text=raw.
    """
    s = raw.strip()
    if not s:
        return None, ""

    now = datetime.now().astimezone()

    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?)\s+(.+)$", s)
    if m:
        iso_s, msg = m.group(1), m.group(2)
        parsed = _safe_parse_dt(iso_s)
        if parsed is not None:
            return parsed.isoformat(timespec="seconds"), msg.strip()

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

    m = re.match(r"^in\s+(\d+)\s+(days?|hours?|minutes?|seconds?)\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        msg = m.group(3).strip()
        if unit.startswith("day"):
            dt = now + timedelta(days=n)
        elif unit.startswith("hour"):
            dt = now + timedelta(hours=n)
        elif unit.startswith("second"):
            dt = now + timedelta(seconds=n)
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


def _is_future_due(ts: str, *, grace_seconds: int = 2) -> bool:
    dt = _safe_parse_dt(ts)
    if dt is None:
        return False
    threshold = datetime.now().astimezone() - timedelta(seconds=max(0, int(grace_seconds)))
    return dt >= threshold


def _parse_time_fragment(fragment: str) -> str | None:
    due_ts, _ = parse_remind_command(f"{fragment.strip()} placeholder")
    return due_ts


def _strip_reminder_prefix(text: str) -> str:
    raw = (text or "").strip()
    low = raw.lower()
    prefixes = (
        "remind me ",
        "set a reminder ",
        "set reminder ",
        "create a reminder ",
        "remember to ",
    )
    for prefix in prefixes:
        if low.startswith(prefix):
            return raw[len(prefix):].strip()
    return raw


def _normalize_reminder_request_text(text: str) -> str:
    body = _strip_reminder_prefix(text)
    if body.lower().startswith("for "):
        body = body[4:].strip()

    suffix_match = re.match(r"^to\s+(.+?)\s+in\s+(\d+)\s+(seconds?|minutes?|hours?|days?)$", body, flags=re.IGNORECASE)
    if suffix_match:
        reminder_text, amount_s, unit_s = suffix_match.groups()
        body = f"in {amount_s} {unit_s.lower()} {reminder_text.strip()}"

    suffix_match = re.match(r"^(.+?)\s+in\s+(\d+)\s+(seconds?|minutes?|hours?|days?)$", body, flags=re.IGNORECASE)
    if suffix_match and not body.lower().startswith("in "):
        reminder_text, amount_s, unit_s = suffix_match.groups()
        body = f"in {amount_s} {unit_s.lower()} {reminder_text.strip()}"

    match = re.match(r"^(today|tomorrow)\s+at\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$", body, flags=re.IGNORECASE)
    if match:
        day_s, hour_s, minute_s, rest = match.groups()
        body = f"{day_s.lower()} {int(hour_s):02d}:{int(minute_s or '0'):02d} {rest.strip()}"

    if body.lower().startswith(("in ", "today ", "tomorrow ")):
        body = body.replace(" to ", " ", 1)

    return body.strip()


def _load_list(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    return data if isinstance(data, list) else []
