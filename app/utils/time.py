from __future__ import annotations

from datetime import date, datetime, timezone


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_local_date() -> date:
    return datetime.now().astimezone().date()


def parse_iso_to_local_date(ts: str) -> date | None:
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone().date()
