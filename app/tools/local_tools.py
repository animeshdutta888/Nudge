from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

from app.models.note import Note
from app.services.storage import read_json, write_json
from app.utils.time import now_local_iso


class ToolError(RuntimeError):
    pass


class LocalToolExecutor:
    def __init__(self, *, notes_path: Path, workspace_root: Path) -> None:
        self._notes_path = notes_path
        self._workspace_root = workspace_root.resolve()

    def execute(self, tool: str, action: str, payload: dict[str, Any]) -> str:
        if tool == "notes":
            return self._notes(action, payload)
        if tool == "filesystem":
            return self._filesystem(action, payload)
        if tool == "shell":
            return self._shell(action, payload)
        raise ToolError("Unsupported tool.")

    def explain(self, tool: str, action: str, payload: dict[str, Any]) -> str:
        if tool == "shell":
            command = str(payload.get("command", "")).strip()
            return f"Run shell command `{command}` in `{self._display_path(self._workspace_root)}`."
        if tool == "filesystem":
            path = self._resolve_payload_path(payload)
            return f"{action.replace('_', ' ').title()} `{self._display_path(path)}`."
        if tool == "notes":
            text = str(payload.get("text", "") or payload.get("query", "")).strip()
            return f"{action.replace('_', ' ').title()} note action for `{text[:80]}`."
        return "Pending tool action."

    def _notes(self, action: str, payload: dict[str, Any]) -> str:
        if action == "create":
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ToolError("Missing note text.")
            notes = self._load_notes()
            notes.append(Note(ts=now_local_iso(), text=text, tags=[]).__dict__)
            write_json(self._notes_path, notes)
            return "Saved note."
        if action == "search":
            query = str(payload.get("query", "")).strip()
            if not query:
                raise ToolError("Missing note query.")
            notes = self._load_notes()
            tokens = _tokenize(query)
            matches = [item for item in reversed(notes) if all(token in str(item.get("text", "")).lower() for token in tokens)]
            if not matches:
                return "No matching notes found."
            lines = ["Matching notes:"]
            for item in matches[:5]:
                lines.append(f"- {str(item.get('text', '')).strip()}")
            return "\n".join(lines)
        if action == "list":
            notes = self._load_notes()
            if not notes:
                return "No notes saved yet."
            lines = ["Recent notes:"]
            for item in reversed(notes[-5:]):
                lines.append(f"- {str(item.get('text', '')).strip()}")
            return "\n".join(lines)
        raise ToolError("Unsupported notes action.")

    def _filesystem(self, action: str, payload: dict[str, Any]) -> str:
        path = self._resolve_payload_path(payload)
        if action == "list":
            if not path.exists():
                raise ToolError("That path does not exist.")
            if path.is_file():
                return f"`{self._display_path(path)}` is a file."
            items = sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
            if not items:
                return f"`{self._display_path(path)}` is empty."
            lines = [f"Contents of `{self._display_path(path)}`:"]
            for item in items[:20]:
                label = "file" if item.is_file() else "dir"
                lines.append(f"- [{label}] {item.name}")
            if len(items) > 20:
                lines.append(f"- ... and {len(items) - 20} more")
            return "\n".join(lines)
        if action == "read":
            if not path.exists() or not path.is_file():
                raise ToolError("That file does not exist.")
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise ToolError("That file is not UTF-8 text.")
            lines = text.splitlines()
            snippet = lines[:80]
            header = f"File: `{self._display_path(path)}`"
            body = "\n".join(snippet).strip()
            if len(lines) > 80:
                body += "\n... (truncated)"
            return header if not body else f"{header}\n{body}"
        raise ToolError("Unsupported filesystem action.")

    def _shell(self, action: str, payload: dict[str, Any]) -> str:
        if action != "run":
            raise ToolError("Unsupported shell action.")
        command = str(payload.get("command", "")).strip()
        if not command:
            raise ToolError("Missing shell command.")
        workdir = self._workspace_root
        timeout_s = min(max(int(payload.get("timeout_s", 20) or 20), 1), 30)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout_s}s.")
        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        parts = [f"Shell command: `{command}`", f"Exit code: {completed.returncode}"]
        if output:
            parts.append(output[:4000])
        if error:
            parts.append("stderr:\n" + error[:2000])
        return "\n".join(parts)

    def _load_notes(self) -> list[dict[str, Any]]:
        raw = read_json(self._notes_path, default=[])
        return raw if isinstance(raw, list) else []

    def _resolve_payload_path(self, payload: dict[str, Any]) -> Path:
        raw = str(payload.get("path", "")).strip()
        if not raw:
            return self._workspace_root
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (self._workspace_root / path).resolve()
        else:
            path = path.resolve()
        try:
            path.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ToolError(f"Path must stay inside `{self._display_path(self._workspace_root)}`.") from exc
        return path

    def _display_path(self, path: Path) -> str:
        try:
            rel = path.relative_to(self._workspace_root)
        except ValueError:
            return str(path)
        return "." if str(rel) == "." else f"./{rel}"


def normalize_shell_command(command: str) -> str:
    text = command.strip()
    if not text:
        return ""
    lowered = text.lower()
    for prefix in ("run ", "execute ", "shell ", "command "):
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if text.startswith("`") and text.endswith("`") and len(text) > 1:
        text = text[1:-1].strip()
    return text


def extract_remind_text(text: str) -> str:
    raw = text.strip()
    lowered = raw.lower()
    for prefix in ("remind me to ", "remember to ", "save a note ", "create a note "):
        if lowered.startswith(prefix):
            return raw[len(prefix):].strip()
    return raw


def _tokenize(text: str) -> list[str]:
    return [part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if part]
