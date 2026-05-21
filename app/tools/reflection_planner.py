from __future__ import annotations

import re
from typing import Any, Optional


def parse_close_day_reflection(text: str) -> Optional[dict[str, list[str]]]:
    normalized = normalize_close_day_text(text)
    if not normalized:
        return None
    lower = normalized.lower()
    finished_markers = ("finished:", "wins:", "done:")
    stuck_markers = ("stuck:", "blockers:", "blocked:")
    carry_markers = ("carry:", "carry forward:", "tomorrow:")
    if not any(marker in lower for marker in finished_markers + stuck_markers + carry_markers):
        guessed = guess_close_day_sections(normalized)
        if guessed is not None:
            return guessed
        return None
    finished = extract_segment(normalized, finished_markers, stuck_markers + carry_markers)
    blockers = extract_segment(normalized, stuck_markers, finished_markers + carry_markers)
    carry = extract_segment(normalized, carry_markers, finished_markers + stuck_markers)
    parsed = {
        "wins": split_close_day_items(finished),
        "blockers": split_close_day_items(blockers),
        "carry_forward": split_close_day_items(carry),
    }
    if any(parsed.values()):
        return parsed
    return guess_close_day_sections(normalized)


def normalize_close_day_text(text: str) -> str:
    normalized = re.sub(r"[ \t]+", " ", str(text).strip())
    replacements = (
        (r"\bfinshed\s*:", "finished:"),
        (r"\bfinised\s*:", "finished:"),
        (r"\bfinishd\s*:", "finished:"),
        (r"\bfinished\s*[-]\s*", "finished: "),
        (r"\bstuck\s*[-]\s*", "stuck: "),
        (r"\bcarry(?:\s+forward)?\s*[-]\s*", "carry: "),
        (r"\btomorrow\s*[-]\s*", "tomorrow: "),
        (r"\bwinns\s*:", "wins:"),
        (r"\bblokers\s*:", "blockers:"),
        (r"\bcary\s*:", "carry:"),
        (r"\bcarryforward\s*:", "carry forward:"),
    )
    fixed = normalized
    for pattern, replacement in replacements:
        fixed = re.sub(pattern, replacement, fixed, flags=re.IGNORECASE)
    return fixed


def guess_close_day_sections(text: str) -> Optional[dict[str, list[str]]]:
    segments = [part.strip() for part in re.split(r"\s*(?:;|\n)+\s*", text) if part.strip()]
    if not segments:
        return None
    wins: list[str] = []
    blockers: list[str] = []
    carry_forward: list[str] = []
    for segment in segments:
        lowered = segment.lower()
        if any(token in lowered for token in ("finish", "win", "done", "shipped", "completed")):
            wins.extend(split_close_day_items(re.sub(r"^(?:i\s+)?(?:finished|finish|won|done|completed|shipped)\s+", "", segment, flags=re.IGNORECASE)))
            continue
        if any(token in lowered for token in ("stuck", "block", "blocked", "issue", "problem")):
            blockers.extend(split_close_day_items(re.sub(r"^(?:i\s+)?(?:am\s+)?(?:stuck|blocked|blockers?)\s+", "", segment, flags=re.IGNORECASE)))
            continue
        if any(token in lowered for token in ("carry", "tomorrow", "next", "later")):
            carry_forward.extend(split_close_day_items(re.sub(r"^(?:i\s+will\s+)?(?:carry|tomorrow|next|later)\s+", "", segment, flags=re.IGNORECASE)))
    if not wins and not blockers and not carry_forward:
        return None
    return {
        "wins": wins,
        "blockers": blockers,
        "carry_forward": carry_forward,
    }


def extract_segment(text: str, starters: tuple[str, ...], others: tuple[str, ...]) -> str:
    lower = text.lower()
    start_index = -1
    marker_used = ""
    for marker in starters:
        idx = lower.find(marker)
        if idx != -1 and (start_index == -1 or idx < start_index):
            start_index = idx
            marker_used = marker
    if start_index == -1:
        return ""
    content_start = start_index + len(marker_used)
    end_index = len(text)
    for marker in others:
        idx = lower.find(marker, content_start)
        if idx != -1:
            end_index = min(end_index, idx)
    return text[content_start:end_index].strip(" ;,.")


def split_close_day_items(segment: str) -> list[str]:
    if not segment:
        return []
    parts = re.split(r"\s*(?:;|,|\n|\band\b)\s*", segment)
    return [part.strip(" .") for part in parts if part.strip(" .")]


def build_close_day_summary(parsed: dict[str, list[str]]) -> str:
    wins = parsed.get("wins", [])
    blockers = parsed.get("blockers", [])
    carry = parsed.get("carry_forward", [])
    pieces = []
    if wins:
        pieces.append(f"{len(wins)} win" + ("" if len(wins) == 1 else "s"))
    if blockers:
        pieces.append(f"{len(blockers)} blocker" + ("" if len(blockers) == 1 else "s"))
    if carry:
        pieces.append(f"{len(carry)} carry-forward item" + ("" if len(carry) == 1 else "s"))
    return "Close day summary: " + (", ".join(pieces) if pieces else "reflection captured")


def render_close_day_response(parsed: dict[str, list[str]]) -> str:
    wins = parsed.get("wins", [])
    blockers = parsed.get("blockers", [])
    carry = parsed.get("carry_forward", [])
    lines = ["Here is your close-day reflection draft:"]
    lines.append("Wins:")
    lines.extend(f"- {item}" for item in wins[:5] or ["None captured"])
    lines.append("Blockers:")
    lines.extend(f"- {item}" for item in blockers[:5] or ["None captured"])
    lines.append("Carry forward:")
    lines.extend(f"- {item}" for item in carry[:3] or ["None captured"])
    lines.append("")
    lines.append("I can save this reflection, close today's plan, and carry unfinished work forward. Approve?")
    return "\n".join(lines)


def render_close_day_log(wins: list[str], blockers: list[str], carry_forward: list[str]) -> str:
    wins_text = ", ".join(wins) if wins else "none"
    blockers_text = ", ".join(blockers) if blockers else "none"
    carry_text = ", ".join(carry_forward) if carry_forward else "none"
    return f"Daily reflection: wins={wins_text}; blockers={blockers_text}; carry_forward={carry_text}"


def infer_goal_completions(projects: list[dict[str, Any]], wins: list[str]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    wins_lower = [item.lower() for item in wins]
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_name = str(project.get("name", "")).strip()
        goals = project.get("goals", [])
        if not project_name or not isinstance(goals, list):
            continue
        for index, goal in enumerate(goals, start=1):
            if not isinstance(goal, dict) or bool(goal.get("done", False)):
                continue
            goal_text = str(goal.get("text", "")).strip()
            if not goal_text:
                continue
            goal_key = goal_text.lower()
            if any(goal_key in win or win in goal_key for win in wins_lower):
                updates.append({"project": project_name, "goal_index": index, "goal_text": goal_text})
    return updates
