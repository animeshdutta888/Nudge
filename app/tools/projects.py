from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.services.storage import read_json, write_json
from app.utils.time import now_local_iso


def load_projects(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    return data if isinstance(data, list) else []


def add_project(path: Path, name: str) -> bool:
    clean = name.strip()
    if not clean:
        return False
    projects = load_projects(path)
    if any(str(p.get("name", "")).strip().lower() == clean.lower() for p in projects if isinstance(p, dict)):
        return False
    projects.append(
        {
            "name": clean,
            "created_ts": now_local_iso(),
            "status": "active",
            "goals": [],
        }
    )
    write_json(path, projects)
    return True


def add_goal(path: Path, project_name: str, text: str) -> bool:
    pname = project_name.strip()
    goal_text = text.strip()
    if not pname or not goal_text:
        return False
    projects = load_projects(path)
    for project in projects:
        if not isinstance(project, dict):
            continue
        if str(project.get("name", "")).strip().lower() != pname.lower():
            continue
        goals = project.get("goals", [])
        if not isinstance(goals, list):
            goals = []
        goals.append(
            {
                "text": goal_text,
                "done": False,
                "created_ts": now_local_iso(),
                "done_ts": None,
            }
        )
        project["goals"] = goals
        write_json(path, projects)
        return True
    return False


def delete_project(path: Path, project_name: str) -> bool:
    pname = project_name.strip()
    if not pname:
        return False
    projects = load_projects(path)
    kept = [
        project
        for project in projects
        if not (
            isinstance(project, dict)
            and str(project.get("name", "")).strip().lower() == pname.lower()
        )
    ]
    if len(kept) == len(projects):
        return False
    write_json(path, kept)
    return True


def edit_goal(path: Path, project_name: str, goal_index: int, text: str) -> bool:
    pname = project_name.strip()
    goal_text = text.strip()
    if not pname or not goal_text:
        return False
    projects = load_projects(path)
    for project in projects:
        if not isinstance(project, dict):
            continue
        if str(project.get("name", "")).strip().lower() != pname.lower():
            continue
        goals = project.get("goals", [])
        if not isinstance(goals, list) or goal_index < 1 or goal_index > len(goals):
            return False
        goal = goals[goal_index - 1]
        if not isinstance(goal, dict):
            return False
        goal["text"] = goal_text
        write_json(path, projects)
        return True
    return False


def delete_goal(path: Path, project_name: str, goal_index: int) -> bool:
    pname = project_name.strip()
    projects = load_projects(path)
    for project in projects:
        if not isinstance(project, dict):
            continue
        if str(project.get("name", "")).strip().lower() != pname.lower():
            continue
        goals = project.get("goals", [])
        if not isinstance(goals, list) or goal_index < 1 or goal_index > len(goals):
            return False
        del goals[goal_index - 1]
        project["goals"] = goals
        write_json(path, projects)
        return True
    return False


def mark_goal(path: Path, project_name: str, goal_index: int, done: bool) -> bool:
    pname = project_name.strip()
    projects = load_projects(path)
    for project in projects:
        if not isinstance(project, dict):
            continue
        if str(project.get("name", "")).strip().lower() != pname.lower():
            continue
        goals = project.get("goals", [])
        if not isinstance(goals, list):
            return False
        if goal_index < 1 or goal_index > len(goals):
            return False
        goal = goals[goal_index - 1]
        if not isinstance(goal, dict):
            return False
        goal["done"] = bool(done)
        goal["done_ts"] = now_local_iso() if done else None
        write_json(path, projects)
        return True
    return False


def set_project_status(path: Path, project_name: str, status: str) -> bool:
    pname = project_name.strip()
    clean_status = status.strip().lower()
    if not pname or clean_status not in {"active", "archived", "done"}:
        return False
    projects = load_projects(path)
    for project in projects:
        if not isinstance(project, dict):
            continue
        if str(project.get("name", "")).strip().lower() != pname.lower():
            continue
        project["status"] = clean_status
        write_json(path, projects)
        return True
    return False


def projects_summary(path: Path) -> str:
    projects = load_projects(path)
    if not projects:
        return "No projects yet. Use `project add <name>`."
    lines: list[str] = []
    for project in projects[:12]:
        if not isinstance(project, dict):
            continue
        name = str(project.get("name", "")).strip()
        status = str(project.get("status", "active")).strip()
        goals = project.get("goals", [])
        done_count = 0
        total = 0
        if isinstance(goals, list):
            total = len(goals)
            done_count = sum(1 for g in goals if isinstance(g, dict) and bool(g.get("done", False)))
        lines.append(f"- {name} ({status}) goals: {done_count}/{total}")
    return "\n".join(lines) if lines else "No projects yet."


def find_project(path: Path, query: str) -> Optional[dict[str, Any]]:
    clean = query.strip()
    if not clean:
        return None
    projects = load_projects(path)
    query_key = _project_key(clean)
    exact_match: Optional[dict[str, Any]] = None
    partial_match: Optional[dict[str, Any]] = None
    for project in projects:
        if not isinstance(project, dict):
            continue
        name = str(project.get("name", "")).strip()
        if not name:
            continue
        name_key = _project_key(name)
        if name_key == query_key:
            exact_match = project
            break
        if query_key in name_key or name_key in query_key:
            partial_match = partial_match or project
    return exact_match or partial_match


def describe_project(path: Path, query: str) -> str:
    project = find_project(path, query)
    if not isinstance(project, dict):
        return "I couldn't find a project with that name."
    name = str(project.get("name", "")).strip() or "Unnamed project"
    status = str(project.get("status", "active")).strip() or "active"
    goals_raw = project.get("goals", [])
    goals = goals_raw if isinstance(goals_raw, list) else []
    done_count = sum(1 for goal in goals if isinstance(goal, dict) and bool(goal.get("done", False)))
    lines = [
        f"Project: {name}",
        f"Status: {status}",
        f"Goals: {done_count}/{len(goals)} done",
    ]
    for idx, goal in enumerate(goals[:5], start=1):
        if not isinstance(goal, dict):
            continue
        text = str(goal.get("text", "")).strip()
        if not text:
            continue
        marker = "done" if bool(goal.get("done", False)) else "open"
        lines.append(f"- {idx}. [{marker}] {text}")
    if len(lines) == 3:
        lines.append("- No goals yet.")
    return "\n".join(lines)


def _project_key(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())
