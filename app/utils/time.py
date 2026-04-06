from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local_now() -> datetime:
    tz_name = os.getenv("NUDGE_TIMEZONE", "").strip()
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now().astimezone()


def now_local_iso() -> str:
    return _local_now().isoformat(timespec="seconds")


def today_local_date() -> date:
    return _local_now().date()


def parse_iso_to_local_date(ts: str) -> date | None:
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_now().tzinfo)
    return dt.astimezone(_local_now().tzinfo).date()
