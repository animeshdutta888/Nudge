from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.services.storage import read_json, write_json
from app.utils.time import now_local_iso, today_local_date


def load_daily_plans(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    return data if isinstance(data, list) else []


def save_daily_plan(
    path: Path,
    priorities: list[str],
    *,
    summary: str = "",
    source: str = "local",
    carry_forward: Optional[list[str]] = None,
) -> dict[str, Any]:
    plans = load_daily_plans(path)
    now = now_local_iso()
    today = today_local_date().isoformat()
    cleaned = [str(item).strip() for item in priorities if str(item).strip()][:3]
    plan = {
        "id": f"daily-plan-{today}",
        "date": today,
        "created_at": now,
        "updated_at": now,
        "source": source,
        "summary": summary.strip(),
        "priorities": cleaned,
        "carry_forward": [str(item).strip() for item in (carry_forward or []) if str(item).strip()][:3],
        "status": "approved",
    }

    replaced = False
    for index, existing in enumerate(plans):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("date", "")).strip() == today:
            plans[index] = plan
            replaced = True
            break
    if not replaced:
        plans.append(plan)
    write_json(path, plans)
    return plan


def latest_daily_plan(path: Path) -> Optional[dict[str, Any]]:
    plans = _sorted_valid_plans(path)
    return plans[0] if plans else None


def previous_daily_plan(path: Path) -> Optional[dict[str, Any]]:
    today = today_local_date().isoformat()
    for item in _sorted_valid_plans(path):
        if str(item.get("date", "")).strip() != today:
            return item
    return None


def add_priority_to_today_plan(path: Path, priority: str) -> Optional[dict[str, Any]]:
    cleaned = str(priority).strip()
    if not cleaned:
        return None
    plans = load_daily_plans(path)
    today = today_local_date().isoformat()
    now = now_local_iso()
    for index, existing in enumerate(plans):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("date", "")).strip() != today:
            continue
        priorities_raw = existing.get("priorities", [])
        priorities = [str(item).strip() for item in priorities_raw if str(item).strip()] if isinstance(priorities_raw, list) else []
        if cleaned not in priorities:
            priorities.append(cleaned)
        existing["priorities"] = priorities[:3]
        existing["updated_at"] = now
        plans[index] = existing
        write_json(path, plans)
        return existing
    return None


def remove_priority_from_today_plan(path: Path, priority: str) -> Optional[dict[str, Any]]:
    cleaned = str(priority).strip()
    if not cleaned:
        return None
    plans = load_daily_plans(path)
    today = today_local_date().isoformat()
    now = now_local_iso()
    for index, existing in enumerate(plans):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("date", "")).strip() != today:
            continue
        priorities_raw = existing.get("priorities", [])
        priorities = [str(item).strip() for item in priorities_raw if str(item).strip()] if isinstance(priorities_raw, list) else []
        lowered = cleaned.lower()
        updated = [item for item in priorities if item.lower() != lowered]
        if len(updated) == len(priorities):
            updated = [item for item in priorities if lowered not in item.lower()]
        existing["priorities"] = updated[:3]
        existing["updated_at"] = now
        plans[index] = existing
        write_json(path, plans)
        return existing
    return None


def close_today_plan(
    path: Path,
    *,
    wins: list[str],
    blockers: list[str],
    carry_forward: list[str],
    summary: str,
    source: str = "close_day",
) -> dict[str, Any]:
    plans = load_daily_plans(path)
    today = today_local_date().isoformat()
    now = now_local_iso()
    cleaned_wins = [str(item).strip() for item in wins if str(item).strip()][:5]
    cleaned_blockers = [str(item).strip() for item in blockers if str(item).strip()][:5]
    cleaned_carry = [str(item).strip() for item in carry_forward if str(item).strip()][:3]
    for index, existing in enumerate(plans):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("date", "")).strip() != today:
            continue
        existing["updated_at"] = now
        existing["source"] = source
        existing["close_day_summary"] = summary.strip()
        existing["wins"] = cleaned_wins
        existing["blockers"] = cleaned_blockers
        existing["carry_forward"] = cleaned_carry
        existing["status"] = "closed"
        plans[index] = existing
        write_json(path, plans)
        return existing

    plan = {
        "id": f"daily-plan-{today}",
        "date": today,
        "created_at": now,
        "updated_at": now,
        "source": source,
        "summary": "",
        "priorities": [],
        "carry_forward": cleaned_carry,
        "close_day_summary": summary.strip(),
        "wins": cleaned_wins,
        "blockers": cleaned_blockers,
        "status": "closed",
    }
    plans.append(plan)
    write_json(path, plans)
    return plan


def _sorted_valid_plans(path: Path) -> list[dict[str, Any]]:
    plans = load_daily_plans(path)
    valid = [item for item in plans if isinstance(item, dict)]
    valid.sort(key=lambda item: str(item.get("date", "")), reverse=True)
    return valid
