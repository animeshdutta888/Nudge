from __future__ import annotations

from pathlib import Path
from typing import Any

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
