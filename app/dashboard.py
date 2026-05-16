from __future__ import annotations

import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.agent.core import pending_action, run_agent
from app.config import Config
from app.tools.dashboard_data import build_dashboard_payload, search_dashboard_card
from app.agent.graph import LANGGRAPH_AVAILABLE
from app.tools.insights import weekly_review
from app.agent.memory import Memory
from app.persona.builder import build_persona_from_logs
from app.services.retrieval import Retriever
from app.services.llm import LlmConfig
from app.tools.projects import add_goal, add_project, delete_goal, delete_project, edit_goal, mark_goal, set_project_status
from app.tools.reminders import list_reminders
from app.services.storage import ensure_json_file, read_json, write_json
from app.utils.time import now_local_iso
from app.tools.repair import delete_recent, edit_recent, pin_recent
from app.agent.state import StateStore
from app.utils.presentation import assistant_display_text
from app.utils.time import today_local_date


def main() -> int:
    cfg = Config.load()
    assets_dir = Path(__file__).resolve().parents[1] / "dashboard"
    host = os.getenv("NUDGE_DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("NUDGE_DASHBOARD_PORT", "8765"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/overview":
                payload = build_dashboard_payload(cfg.data_dir)
                daily = payload.get("daily_checkin")
                if isinstance(daily, dict) and bool(daily.get("should_prompt")):
                    StateStore(cfg.state_path).mark_daily_checkin_prompted(today_local_date().isoformat())
                payload["graph_enabled"] = LANGGRAPH_AVAILABLE
                self._send_json(payload)
                return
            if parsed.path == "/api/review-week":
                self._send_json({"review": _weekly_review_text(cfg), "graph_enabled": LANGGRAPH_AVAILABLE})
                return
            self._serve_asset(parsed.path)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/chat":
                if parsed.path == "/api/repair":
                    self._handle_repair()
                    return
                if parsed.path == "/api/projects":
                    self._handle_projects()
                    return
                if parsed.path == "/api/search":
                    self._handle_search()
                    return
                if parsed.path in {"/api/pending-save", "/api/pending-action"}:
                    self._handle_pending_action()
                    return
                if parsed.path == "/api/daily-checkin":
                    self._handle_daily_checkin()
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return

            user_text = str(data.get("text", "")).strip()
            if not user_text:
                self.send_error(HTTPStatus.BAD_REQUEST, "Missing text")
                return

            reply = run_agent(user_text, cfg=cfg, source="dashboard")
            overview = build_dashboard_payload(cfg.data_dir)
            self._send_json(
                {
                    "reply": reply,
                    "reply_display": assistant_display_text(_strip_pending_save_hint(reply)),
                    "overview": overview,
                    "pending_action": overview.get("pending_action"),
                    "graph_enabled": LANGGRAPH_AVAILABLE,
                }
            )

        def _handle_repair(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return

            action = str(data.get("action", "")).strip().lower()
            kind = str(data.get("kind", "")).strip().lower()
            recent_index = int(data.get("recent_index", 0) or 0)
            text = str(data.get("text", "")).strip()

            if kind not in {"note", "log"} or recent_index < 1:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid repair target")
                return

            path = cfg.notes_path if kind == "note" else cfg.logs_path
            ok = False
            if action == "delete":
                ok = delete_recent(path, recent_index)
            elif action == "edit":
                ok = edit_recent(path, recent_index, text)
            elif action == "pin":
                ok = pin_recent(path, recent_index, True)
            elif action == "unpin":
                ok = pin_recent(path, recent_index, False)

            if ok and kind in {"note", "log"}:
                _refresh_persona(cfg, retriever=None)
            self._send_json({"ok": ok, "overview": build_dashboard_payload(cfg.data_dir)})

        def _handle_projects(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return

            action = str(data.get("action", "")).strip().lower()
            message = ""
            ok = False
            if action == "add_project":
                ok = add_project(cfg.projects_path, str(data.get("name", "")).strip())
                message = "Project created." if ok else "Could not create project. It may already exist."
            elif action == "add_goal":
                ok = add_goal(
                    cfg.projects_path,
                    str(data.get("project", "")).strip(),
                    str(data.get("text", "")).strip(),
                )
                message = "Goal added." if ok else "Could not add goal."
            elif action == "edit_goal":
                try:
                    goal_index = int(data.get("goal_index", 0) or 0)
                except ValueError:
                    goal_index = 0
                ok = edit_goal(
                    cfg.projects_path,
                    str(data.get("project", "")).strip(),
                    goal_index,
                    str(data.get("text", "")).strip(),
                )
                message = "Goal updated." if ok else "Could not update goal."
            elif action == "delete_goal":
                try:
                    goal_index = int(data.get("goal_index", 0) or 0)
                except ValueError:
                    goal_index = 0
                ok = delete_goal(
                    cfg.projects_path,
                    str(data.get("project", "")).strip(),
                    goal_index,
                )
                message = "Goal deleted." if ok else "Could not delete goal."
            elif action == "delete_project":
                ok = delete_project(
                    cfg.projects_path,
                    str(data.get("project", "")).strip(),
                )
                message = "Project removed." if ok else "Could not remove project."
            elif action == "done_goal":
                try:
                    goal_index = int(data.get("goal_index", 0) or 0)
                except ValueError:
                    goal_index = 0
                ok = mark_goal(
                    cfg.projects_path,
                    str(data.get("project", "")).strip(),
                    goal_index,
                    True,
                )
                message = "Goal marked done." if ok else "Could not update goal."
            elif action == "reopen_goal":
                try:
                    goal_index = int(data.get("goal_index", 0) or 0)
                except ValueError:
                    goal_index = 0
                ok = mark_goal(
                    cfg.projects_path,
                    str(data.get("project", "")).strip(),
                    goal_index,
                    False,
                )
                message = "Goal reopened." if ok else "Could not update goal."
            elif action == "archive_project":
                ok = set_project_status(cfg.projects_path, str(data.get("project", "")).strip(), "archived")
                message = "Project archived." if ok else "Could not archive project."
            elif action == "complete_project":
                ok = set_project_status(cfg.projects_path, str(data.get("project", "")).strip(), "done")
                message = "Project marked done." if ok else "Could not update project."
            elif action == "activate_project":
                ok = set_project_status(cfg.projects_path, str(data.get("project", "")).strip(), "active")
                message = "Project moved back to active." if ok else "Could not update project."

            self._send_json({"ok": ok, "message": message, "overview": build_dashboard_payload(cfg.data_dir)})

        def _handle_search(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return

            card = str(data.get("card", "")).strip().lower()
            query = str(data.get("query", "")).strip()
            if not card:
                self.send_error(HTTPStatus.BAD_REQUEST, "Missing card")
                return
            self._send_json(search_dashboard_card(cfg.data_dir, card, query))

        def _handle_pending_action(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return
            action = str(data.get("action", "")).strip().lower()
            if action not in {"approve", "skip"}:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid action")
                return
            reply = pending_action(action, cfg=cfg)
            overview = build_dashboard_payload(cfg.data_dir)
            self._send_json(
                {
                    "reply": reply,
                    "reply_display": assistant_display_text(reply),
                    "overview": overview,
                    "pending_action": overview.get("pending_action"),
                    "graph_enabled": LANGGRAPH_AVAILABLE,
                }
            )

        def _handle_daily_checkin(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return
            action = str(data.get("action", "")).strip().lower()
            state = StateStore(cfg.state_path)
            today_iso = today_local_date().isoformat()
            if action == "dismiss":
                state.dismiss_daily_checkin_for_day(today_iso)
                self._send_json({"ok": True, "overview": build_dashboard_payload(cfg.data_dir)})
                return
            if action != "submit":
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid action")
                return

            energy = str(data.get("energy", "")).strip() or "n/a"
            focus = str(data.get("focus", "")).strip() or "n/a"
            win = str(data.get("win", "")).strip() or "n/a"
            entry = f"Daily check-in: energy={energy}; focus={focus}; win={win}"
            reply = run_agent(f"log: {entry}", cfg=cfg, source="dashboard")
            state.clear_daily_checkin_dismissal(today_iso)
            overview = build_dashboard_payload(cfg.data_dir)
            self._send_json(
                {
                    "ok": True,
                    "reply": reply,
                    "reply_display": reply,
                    "overview": overview,
                }
            )

        def _serve_asset(self, raw_path: str) -> None:
            rel = "index.html" if raw_path in {"", "/"} else raw_path.lstrip("/")
            full = (assets_dir / rel).resolve()
            if not str(full).startswith(str(assets_dir.resolve())) or not full.exists() or not full.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            ctype, _ = mimetypes.guess_type(str(full))
            data = full.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Nudge dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _weekly_review_text(cfg: Config) -> str:
    ensure_json_file(cfg.logs_path, [])
    ensure_json_file(cfg.notes_path, [])
    ensure_json_file(cfg.persona_path, {})
    ensure_json_file(cfg.embeddings_path, {"next_id": 1, "items": {}})
    ensure_json_file(cfg.reminders_path, [])

    llm = LlmConfig(base_url=cfg.ollama_base_url, model=cfg.model, timeout_s=cfg.timeout_s)
    retriever = Retriever(
        index_path=cfg.faiss_index_path,
        map_path=cfg.embeddings_path,
        ollama=llm,
        embed_model=cfg.embed_model,
    )
    memory = Memory(cfg.logs_path, cfg.notes_path, retriever)
    persona = read_json(cfg.persona_path, default={})
    reminders = list_reminders(cfg.reminders_path, upcoming_days=7)
    return weekly_review(llm, memory, persona=persona, reminders=reminders, days=7)


def _refresh_persona(cfg: Config, retriever) -> None:
    llm = LlmConfig(base_url=cfg.ollama_base_url, model=cfg.model, timeout_s=cfg.timeout_s)
    if retriever is None:
        retriever = Retriever(
            index_path=cfg.faiss_index_path,
            map_path=cfg.embeddings_path,
            ollama=llm,
            embed_model=cfg.embed_model,
        )
    memory = Memory(cfg.logs_path, cfg.notes_path, retriever)
    existing = read_json(cfg.persona_path, default={})
    updated = build_persona_from_logs(llm, memory.recent_logs(cfg.recent_logs_n), memory.recent_notes(cfg.recent_logs_n), existing)
    updated["updated_at"] = now_local_iso()
    write_json(cfg.persona_path, updated)


def _strip_pending_save_hint(text: str) -> str:
    for marker in (
        "\n\n(I noticed something that may be worth remembering.",
        "\n\n(Pending save:",
        "\n\nApprove to create this project and goals, or skip to ignore.",
    ):
        idx = text.find(marker)
        if idx != -1:
            return text[:idx].strip()
    return text.strip()


if __name__ == "__main__":
    raise SystemExit(main())
