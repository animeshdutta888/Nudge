from __future__ import annotations

import os
from pathlib import Path
import subprocess
import re
from typing import Any

from app.models.note import Note
from app.services.storage import read_json, write_json
from app.utils.time import now_local_iso


class ToolError(RuntimeError):
    pass


class ToolExecution:
    def __init__(self, text: str, result: dict[str, Any] | None = None) -> None:
        self.text = text
        self.result = result


class LocalToolExecutor:
    def __init__(self, *, notes_path: Path, workspace_root: Path) -> None:
        self._notes_path = notes_path
        self._workspace_root = workspace_root.resolve()

    def execute(self, tool: str, action: str, payload: dict[str, Any]) -> ToolExecution:
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

    def _notes(self, action: str, payload: dict[str, Any]) -> ToolExecution:
        if action == "create":
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ToolError("Missing note text.")
            notes = self._load_notes()
            note = Note(ts=now_local_iso(), text=text, tags=[])
            notes.append(note.__dict__)
            write_json(self._notes_path, notes)
            return ToolExecution(
                "Saved note.",
                {
                    "kind": "notes_create",
                    "title": "Note saved",
                    "text": note.text,
                    "ts": note.ts,
                },
            )
        if action == "search":
            query = str(payload.get("query", "")).strip()
            if not query:
                raise ToolError("Missing note query.")
            notes = self._load_notes()
            tokens = _tokenize(query)
            matches = [item for item in reversed(notes) if all(token in str(item.get("text", "")).lower() for token in tokens)]
            if not matches:
                return ToolExecution(
                    "No matching notes found.",
                    {"kind": "notes_search", "title": "Note search", "query": query, "items": []},
                )
            lines = ["Matching notes:"]
            items: list[dict[str, str]] = []
            for item in matches[:5]:
                note_text = str(item.get("text", "")).strip()
                note_ts = str(item.get("ts", "")).strip()
                lines.append(f"- {note_text}")
                items.append({"text": note_text, "ts": note_ts})
            return ToolExecution(
                "\n".join(lines),
                {"kind": "notes_search", "title": "Matching notes", "query": query, "items": items},
            )
        if action == "list":
            notes = self._load_notes()
            if not notes:
                return ToolExecution("No notes saved yet.", {"kind": "notes_list", "title": "Recent notes", "items": []})
            lines = ["Recent notes:"]
            items: list[dict[str, str]] = []
            for item in reversed(notes[-5:]):
                note_text = str(item.get("text", "")).strip()
                note_ts = str(item.get("ts", "")).strip()
                lines.append(f"- {note_text}")
                items.append({"text": note_text, "ts": note_ts})
            return ToolExecution("\n".join(lines), {"kind": "notes_list", "title": "Recent notes", "items": items})
        raise ToolError("Unsupported notes action.")

    def _filesystem(self, action: str, payload: dict[str, Any]) -> ToolExecution:
        path = self._resolve_payload_path(payload)
        if action == "list":
            if not path.exists():
                raise ToolError("That path does not exist.")
            if path.is_file():
                return ToolExecution(
                    f"`{self._display_path(path)}` is a file.",
                    {"kind": "filesystem_list", "title": "Path info", "path": self._display_path(path), "items": []},
                )
            items = sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
            if not items:
                return ToolExecution(
                    f"`{self._display_path(path)}` is empty.",
                    {"kind": "filesystem_list", "title": "Directory contents", "path": self._display_path(path), "items": []},
                )
            lines = [f"Contents of `{self._display_path(path)}`:"]
            entries: list[dict[str, str]] = []
            for item in items[:20]:
                label = "file" if item.is_file() else "dir"
                lines.append(f"- [{label}] {item.name}")
                entries.append({"name": item.name, "entry_type": label, "path": self._display_path(item)})
            if len(items) > 20:
                lines.append(f"- ... and {len(items) - 20} more")
            return ToolExecution(
                "\n".join(lines),
                {
                    "kind": "filesystem_list",
                    "title": "Directory contents",
                    "path": self._display_path(path),
                    "items": entries,
                    "truncated": len(items) > 20,
                },
            )
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
            return ToolExecution(
                header if not body else f"{header}\n{body}",
                {
                    "kind": "filesystem_read",
                    "title": path.name,
                    "path": self._display_path(path),
                    "content": body,
                    "truncated": len(lines) > 80,
                },
            )
        raise ToolError("Unsupported filesystem action.")

    def _shell(self, action: str, payload: dict[str, Any]) -> ToolExecution:
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
        return ToolExecution(
            "\n".join(parts),
            {
                "kind": "shell_run",
                "title": command,
                "command": command,
                "cwd": self._display_path(workdir),
                "exit_code": completed.returncode,
                "stdout": output[:4000] if output else "",
                "stderr": error[:2000] if error else "",
            },
        )

    def _load_notes(self) -> list[dict[str, Any]]:
        raw = read_json(self._notes_path, default=[])
        return raw if isinstance(raw, list) else []

    def _resolve_payload_path(self, payload: dict[str, Any]) -> Path:
        raw = _extract_path_reference(str(payload.get("path", "")).strip())
        base_path = self._resolve_base_path(str(payload.get("base_path", "")).strip())
        if not raw:
            return base_path or self._workspace_root
        path = Path(raw).expanduser()
        if not path.is_absolute():
            root_candidate = (self._workspace_root / path).resolve()
            base_candidate = ((base_path or self._workspace_root) / path).resolve()
            if base_path is not None and base_candidate.exists():
                path = base_candidate
            else:
                path = root_candidate
        else:
            path = path.resolve()
        if not path.exists():
            matched = self._find_best_path_match(raw, base_path=base_path)
            if matched is not None:
                path = matched
        try:
            path.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ToolError(f"Path must stay inside `{self._display_path(self._workspace_root)}`.") from exc
        return path

    def _resolve_base_path(self, raw: str) -> Path | None:
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (self._workspace_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            candidate.relative_to(self._workspace_root)
        except ValueError:
            return None
        if candidate.is_file():
            return candidate.parent
        return candidate if candidate.exists() else None

    def _display_path(self, path: Path) -> str:
        try:
            rel = path.relative_to(self._workspace_root)
        except ValueError:
            return str(path)
        return "." if str(rel) == "." else f"./{rel}"

    def _find_best_path_match(self, raw: str, base_path: Path | None = None) -> Path | None:
        target = _normalize_candidate_name(raw)
        if not target:
            return None
        candidates: list[Path] = []
        search_roots = [base_path] if base_path is not None else []
        search_roots.append(self._workspace_root)
        seen_roots: set[Path] = set()
        unique_roots: list[Path] = []
        for root in search_roots:
            if root is None or root in seen_roots:
                continue
            seen_roots.add(root)
            unique_roots.append(root)
        for root in unique_roots:
            for candidate in root.rglob("*"):
                if not candidate.is_file():
                    continue
                name = _normalize_candidate_name(candidate.name)
                stem = _normalize_candidate_name(candidate.stem)
                if target in {name, stem} or name.startswith(target):
                    candidates.append(candidate.resolve())
                if len(candidates) >= 50:
                    break
            if candidates:
                break
        if not candidates:
            return None
        candidates.sort(key=lambda item: (len(item.relative_to(self._workspace_root).parts), len(item.name)))
        return candidates[0]


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


def _extract_path_reference(text: str) -> str:
    raw = text.strip().strip("`'\"")
    if not raw:
        return ""
    lowered = raw.lower()
    patterns = (
        r"^(?:show|open|read|view)\s+(?:the\s+)?(?:content\s+of\s+)?(.+)$",
        r"^(?:show me|open up)\s+(.+)$",
        r"^(?:content of|file)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if match:
            start = match.start(1)
            raw = raw[start:].strip()
            break
    raw = re.sub(r"\s+(?:file|please)$", "", raw, flags=re.IGNORECASE).strip()
    return raw.strip("`'\"")


def _normalize_candidate_name(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())
