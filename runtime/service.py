from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Optional

from agents.critic import CriticAgent
from agents.governance import GovernanceAgent
from agents.manager import ManagerAgent
from agents.memory import MemoryAgent
from agents.retrieval import RetrievalAgent
from agents.synthesis import SynthesisAgent
from app.agent.router import IntentRouter
from app.agent.state import PendingSave, PendingToolAction, StateStore
from app.config import Config
from app.services.llm import LlmConfig
from app.services.retrieval import Retriever
from app.services.semantic_cache import SemanticCache
from app.tools.local_tools import LocalToolExecutor, ToolError, extract_remind_text, normalize_shell_command
from app.tools.projects import add_goal, add_project, describe_project, find_project, load_projects, mark_goal, projects_summary
from app.tools.reminders import add_reminder, list_reminders, mark_done, parse_remind_command
from app.utils.presentation import assistant_display_text
from observability.logger import ExecutionLogger
from orchestration.engine import OrchestrationEngine
from schemas.shared import RuntimeResponse, SharedState
from storage.local import LocalWorkspace


class NudgeRuntime:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._workspace = LocalWorkspace(cfg.data_dir, cfg.traces_db_path)
        llm = LlmConfig(base_url=cfg.ollama_base_url, model=cfg.model, timeout_s=cfg.timeout_s)
        self._state_store = StateStore(cfg.state_path)
        self._router = IntentRouter(llm)
        self._tools = LocalToolExecutor(notes_path=cfg.notes_path, workspace_root=cfg.workspace_root)
        retriever = Retriever(
            index_path=cfg.faiss_index_path,
            map_path=cfg.embeddings_path,
            ollama=llm,
            embed_model=cfg.embed_model,
        )
        self._logger = ExecutionLogger(cfg.traces_db_path)
        self._memory = MemoryAgent(self._workspace)
        semantic_cache = SemanticCache(
            cfg.semantic_cache_path,
            llm,
            cfg.embed_model,
            cfg.semantic_cache_threshold,
            model_id=f"{cfg.model}|{cfg.embed_model}",
            data_version_provider=self._workspace.data_version_hash,
        )
        manager = ManagerAgent(
            logger=self._logger,
            governance=GovernanceAgent(cfg),
            retrieval=RetrievalAgent(retriever),
            memory=self._memory,
            semantic_cache=semantic_cache,
            synthesis=SynthesisAgent(llm, self._workspace),
            critic=CriticAgent(),
            timeout_s=cfg.agent_timeout_s,
            max_retries=cfg.max_retries,
            global_budget_s=cfg.global_budget_s,
        )
        self._engine = OrchestrationEngine(manager)
        self._inflight_lock = threading.Lock()
        self._inflight: dict[tuple[str, str], _InflightEntry] = {}

    def run_sync(self, user_text: str, source: str = "cli") -> str:
        key = (_normalize_query(user_text), source)
        is_owner = False
        with self._inflight_lock:
            entry = self._inflight.get(key)
            if entry is None:
                entry = _InflightEntry()
                self._inflight[key] = entry
                is_owner = True
            else:
                entry.waiters += 1

        if not is_owner:
            with entry.condition:
                while not entry.done:
                    entry.condition.wait()
                if entry.error is not None:
                    raise entry.error
                return entry.result or ""

        try:
            result = asyncio.run(self.run(user_text, source=source)).text
            with entry.condition:
                entry.result = result
                entry.done = True
                entry.condition.notify_all()
            return result
        except Exception as exc:  # noqa: BLE001
            with entry.condition:
                entry.error = exc
                entry.done = True
                entry.condition.notify_all()
            raise
        finally:
            with self._inflight_lock:
                self._inflight.pop(key, None)

    async def run(self, user_text: str, source: str = "cli") -> RuntimeResponse:
        text = user_text.strip()
        if not text:
            state = self._new_state(text, source)
            state.execution_status = "REJECTED"
            response = RuntimeResponse(text="Say `log: ...`, `note: ...`, or ask a question.", run_id=state.run_id, state=state)
            self._persist_runtime_state(response)
            return response

        smalltalk_response = await self._handle_smalltalk(text, source)
        if smalltalk_response is not None:
            await self._workspace.append_conversation(text, smalltalk_response.text, source)
            self._persist_runtime_state(smalltalk_response)
            return smalltalk_response

        command_response = await self._handle_command(text, source)
        if command_response is not None:
            await self._workspace.append_conversation(text, command_response.text, source)
            self._persist_runtime_state(command_response)
            return command_response

        routed_response = await self._handle_routed_intent(text, source)
        if routed_response is not None:
            await self._workspace.append_conversation(text, routed_response.text, source)
            self._persist_runtime_state(routed_response)
            return routed_response

        recall_response = await self._handle_recall(text, source)
        if recall_response is not None:
            await self._workspace.append_conversation(text, recall_response.text, source)
            self._persist_runtime_state(recall_response)
            return recall_response

        state = self._new_state(text, source)
        state = await self._engine.execute(state)
        answer = assistant_display_text(state.synthesis_output or "I couldn't produce a response.")
        if state.critic_feedback:
            errors = [item.message for item in state.critic_feedback if item.severity == "error"]
            if errors:
                answer = answer + "\n\nValidation notes: " + "; ".join(errors[:2])
        state.synthesis_output = answer
        await self._workspace.append_conversation(text, answer, source)
        response = RuntimeResponse(text=answer, run_id=state.run_id, degraded=state.degraded_mode, state=state)
        self._persist_runtime_state(response)
        return response

    async def _handle_smalltalk(self, text: str, source: str) -> Optional[RuntimeResponse]:
        low = (text or "").strip().lower()
        if not low:
            return None

        greetings = {
            "hi",
            "hello",
            "hey",
            "yo",
            "hiya",
            "good morning",
            "good afternoon",
            "good evening",
        }
        thanks = {"thanks", "thank you", "thx"}

        if low in greetings:
            state = self._new_state(text, source)
            state.execution_status = "COMPLETED"
            reply = (
                "Hi. What do you want to do?\n"
                "- Ask a question\n"
                "- `note: ...` or `log: ...`\n"
                "- `remind: ...`\n"
                "- `projects`"
            )
            return RuntimeResponse(text=reply, run_id=state.run_id, state=state)

        if low in thanks:
            state = self._new_state(text, source)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="Anytime.", run_id=state.run_id, state=state)

        return None

    async def pending_action(self, action: str) -> str:
        state = self._workspace.load_state()
        if action == "approve":
            pending_plan = state.get("pending_plan")
            if isinstance(pending_plan, dict):
                project = str(pending_plan.get("project", "")).strip()
                goals = pending_plan.get("goals", [])
                created = add_project(self._workspace.projects_path, project)
                if not created and not any(str(item.get("name", "")).strip().lower() == project.lower() for item in load_projects(self._workspace.projects_path)):
                    return "I couldn't create that project."
                added = 0
                for goal in goals if isinstance(goals, list) else []:
                    if add_goal(self._workspace.projects_path, project, str(goal)):
                        added += 1
                state["pending_plan"] = None
                self._workspace.save_state(state)
                return f"Saved plan to project `{project}` with {added} goal" + ("" if added == 1 else "s") + "."
            pending_save = state.get("pending_save")
            if isinstance(pending_save, dict):
                kind = str(pending_save.get("kind", "note"))
                text = str(pending_save.get("text", ""))
                await self._memory.save_explicit(kind, text)
                self._workspace.refresh_persona_snapshot()
                state["pending_save"] = None
                self._workspace.save_state(state)
                return f"Saved as {kind}."
            pending_tool_action = state.get("pending_tool_action")
            if isinstance(pending_tool_action, dict):
                tool = str(pending_tool_action.get("tool", "")).strip()
                action_name = str(pending_tool_action.get("action", "")).strip()
                payload = pending_tool_action.get("payload", {})
                if tool and action_name and isinstance(payload, dict):
                    try:
                        result = self._tools.execute(tool, action_name, payload)
                    except ToolError as exc:
                        state["pending_tool_action"] = None
                        self._workspace.save_state(state)
                        return str(exc)
                    state["pending_tool_action"] = None
                    self._workspace.save_state(state)
                    return result
            return "Nothing pending."
        if action == "skip":
            if state.get("pending_plan") is not None:
                state["pending_plan"] = None
                self._workspace.save_state(state)
                return "Skipped plan."
            if state.get("pending_save") is not None:
                state["pending_save"] = None
                self._workspace.save_state(state)
                return "Skipped saving."
            if state.get("pending_tool_action") is not None:
                state["pending_tool_action"] = None
                self._workspace.save_state(state)
                return "Skipped tool action."
            return "Nothing pending."
        return "Unsupported action."

    async def _handle_command(self, text: str, source: str) -> Optional[RuntimeResponse]:
        low = text.lower()
        state = self._new_state(text, source)
        if low.startswith("log:"):
            record = await self._memory.save_explicit("log", text.split(":", 1)[1].strip())
            self._workspace.refresh_persona_snapshot()
            state.execution_status = "COMPLETED"
            state.memory_context = [record]
            return RuntimeResponse(text="Saved log.", run_id=state.run_id, state=state)
        if low.startswith("note:") or low.startswith("save:") or low.startswith("remember:"):
            body = text.split(":", 1)[1].strip()
            record = await self._memory.save_explicit("note", body)
            self._workspace.refresh_persona_snapshot()
            state.execution_status = "COMPLETED"
            state.memory_context = [record]
            return RuntimeResponse(text="Saved note.", run_id=state.run_id, state=state)
        # Fast-path for explicit shell requests so they don't get misrouted as filesystem reads.
        if low.startswith("run ") or low.startswith("shell ") or low.startswith("command "):
            command = normalize_shell_command(text)
            return self._run_tool_action(
                state,
                "shell",
                "run",
                {"command": command, "timeout_s": 20},
                "Explicit shell request.",
                requires_approval=True,
            )
        if low.startswith("remind:"):
            due, body = parse_remind_command(text.split(":", 1)[1].strip())
            if not body:
                return RuntimeResponse(text="Usage: `remind: <when> <text>`", run_id=state.run_id, state=state)
            add_reminder(self._workspace.reminders_path, body, due)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="Saved reminder.", run_id=state.run_id, state=state)
        if low in {"reminders", "show reminders"}:
            items = list_reminders(self._workspace.reminders_path, upcoming_days=30)
            lines = ["Reminders:"] + [f"- {item.id}:{' due ' + item.due_ts if item.due_ts else ''} {item.text}" for item in items[:12]]
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="\n".join(lines) if len(lines) > 1 else "No reminders.", run_id=state.run_id, state=state)
        if low.startswith("done "):
            if "::" in text:
                rest = text.split("done", 1)[1].strip()
                if "::" not in rest:
                    return RuntimeResponse(text="Usage: `done <project> :: <goal_number>`", run_id=state.run_id, state=state)
                project, goal_text = [part.strip() for part in rest.split("::", 1)]
                try:
                    goal_index = int(goal_text)
                except ValueError:
                    return RuntimeResponse(text="Usage: `done <project> :: <goal_number>`", run_id=state.run_id, state=state)
                ok = mark_goal(self._workspace.projects_path, project, goal_index, True)
                return RuntimeResponse(text="Goal marked done." if ok else "Goal not found.", run_id=state.run_id, state=state)
            try:
                reminder_id = int(text.split(maxsplit=1)[1])
            except (IndexError, ValueError):
                return RuntimeResponse(text="Usage: `done <id>`", run_id=state.run_id, state=state)
            ok = mark_done(self._workspace.reminders_path, reminder_id)
            return RuntimeResponse(text="Marked done." if ok else "Reminder not found.", run_id=state.run_id, state=state)
        if low.startswith("project add "):
            ok = add_project(self._workspace.projects_path, text.split("project add", 1)[1].strip())
            return RuntimeResponse(text="Project added." if ok else "Could not add project.", run_id=state.run_id, state=state)
        if low.startswith("goal add "):
            rest = text.split("goal add", 1)[1].strip()
            if "::" not in rest:
                return RuntimeResponse(text="Usage: `goal add <project> :: <goal>`", run_id=state.run_id, state=state)
            project, goal = [part.strip() for part in rest.split("::", 1)]
            ok = add_goal(self._workspace.projects_path, project, goal)
            return RuntimeResponse(text="Goal added." if ok else "Could not add goal.", run_id=state.run_id, state=state)
        if low in {"projects", "goals"}:
            return RuntimeResponse(text=projects_summary(self._workspace.projects_path), run_id=state.run_id, state=state)
        # Help the dashboard UX: common natural-language ways of asking to list projects.
        if "project" in low and any(
            phrase in low
            for phrase in (
                "do i have any project",
                "do i have any projects",
                "any projects",
                "list projects",
                "show projects",
                "what are my projects",
                "my projects",
            )
        ):
            return RuntimeResponse(text=projects_summary(self._workspace.projects_path), run_id=state.run_id, state=state)
        if low in {"persona", "show persona"}:
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=_describe_persona(self._workspace), run_id=state.run_id, state=state)
        if low in {"insights", "weekly", "review week", "weekly review", "review"}:
            memories = await self._workspace.recent_memories(limit=20)
            logs = [item for item in memories if item.kind == "log"]
            notes = [item for item in memories if item.kind == "note"]
            persona = self._workspace.load_persona()
            lines = [
                "Weekly insights:",
                f"- logs captured: {len(logs)}",
                f"- notes captured: {len(notes)}",
            ]
            focus = persona.get("current_focus", [])
            if isinstance(focus, list) and focus:
                lines.append(f"- current focus signals: {len(focus)}")
            if notes:
                lines.append(f"- recent note theme: {notes[0].text}")
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="\n".join(lines), run_id=state.run_id, state=state)
        if low in {"approve", "yes save", "save it"}:
            return RuntimeResponse(text=await self.pending_action("approve"), run_id=state.run_id, state=state)
        if low in {"skip", "discard", "no", "don't save", "dont save"}:
            return RuntimeResponse(text=await self.pending_action("skip"), run_id=state.run_id, state=state)
        return None

    async def _handle_routed_intent(self, text: str, source: str) -> Optional[RuntimeResponse]:
        state = self._new_state(text, source)
        route = await asyncio.to_thread(
            self._router.route,
            text,
            persona=self._workspace.load_persona(),
            context=self._routing_context(),
        )

        if route.intent == "none":
            matched_project = _match_project_query(self._workspace.projects_path, text)
            if matched_project is None:
                return None
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=describe_project(self._workspace.projects_path, matched_project), run_id=state.run_id, state=state)
        if route.intent == "save_note" and route.text:
            record = await self._memory.save_explicit("note", route.text)
            self._workspace.refresh_persona_snapshot()
            state.execution_status = "COMPLETED"
            state.memory_context = [record]
            return RuntimeResponse(text="Saved note.", run_id=state.run_id, state=state)
        if route.intent == "save_log" and route.text:
            record = await self._memory.save_explicit("log", route.text)
            self._workspace.refresh_persona_snapshot()
            state.execution_status = "COMPLETED"
            state.memory_context = [record]
            return RuntimeResponse(text="Saved log.", run_id=state.run_id, state=state)
        if route.intent == "save_candidate" and route.text:
            pending = PendingSave(kind=_candidate_kind(route.text), text=route.text, reason=route.reason or "Useful personal context.")
            if self._state_store.autosave_enabled():
                record = await self._memory.save_explicit(pending.kind, pending.text)
                self._workspace.refresh_persona_snapshot()
                state.execution_status = "COMPLETED"
                state.memory_context = [record]
                return RuntimeResponse(text=f"Saved {pending.kind}.", run_id=state.run_id, state=state)
            self._state_store.set_pending_save(pending)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(
                text=f"I can save that as a {pending.kind}. Use `approve` to save it or `skip` to discard it.",
                run_id=state.run_id,
                state=state,
            )
        if route.intent == "add_reminder":
            due, body = _resolve_reminder_route(route)
            if not body:
                return RuntimeResponse(text="Tell me what you want to be reminded about.", run_id=state.run_id, state=state)
            add_reminder(self._workspace.reminders_path, body, due)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="Saved reminder.", run_id=state.run_id, state=state)
        if route.intent == "list_reminders":
            items = list_reminders(self._workspace.reminders_path, upcoming_days=30)
            lines = ["Reminders:"] + [f"- {item.id}:{' due ' + item.due_ts if item.due_ts else ''} {item.text}" for item in items[:12]]
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="\n".join(lines) if len(lines) > 1 else "No reminders.", run_id=state.run_id, state=state)
        if route.intent == "complete_reminder" and route.reminder_id > 0:
            ok = mark_done(self._workspace.reminders_path, route.reminder_id)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="Marked done." if ok else "Reminder not found.", run_id=state.run_id, state=state)
        if route.intent == "add_project" and route.project:
            ok = add_project(self._workspace.projects_path, route.project)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="Project added." if ok else "Could not add project.", run_id=state.run_id, state=state)
        if route.intent == "add_goal" and route.project and route.goal:
            ok = add_goal(self._workspace.projects_path, route.project, route.goal)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="Goal added." if ok else "Could not add goal.", run_id=state.run_id, state=state)
        if route.intent == "list_projects":
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=projects_summary(self._workspace.projects_path), run_id=state.run_id, state=state)
        if route.intent == "show_project":
            project_query = route.project or route.text or text
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=describe_project(self._workspace.projects_path, project_query), run_id=state.run_id, state=state)
        if route.intent == "complete_goal" and route.project and route.goal_index > 0:
            ok = mark_goal(self._workspace.projects_path, route.project, route.goal_index, True)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="Goal marked done." if ok else "Goal not found.", run_id=state.run_id, state=state)
        if route.intent == "show_persona":
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=_describe_persona(self._workspace), run_id=state.run_id, state=state)
        if route.intent == "show_priorities":
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=_summarize_priorities(self._workspace), run_id=state.run_id, state=state)
        if route.intent == "notes_create":
            return self._run_tool_action(state, "notes", "create", {"text": route.text or extract_remind_text(text)}, route.reason)
        if route.intent == "notes_search":
            return self._run_tool_action(state, "notes", "search", {"query": route.query or route.text or text}, route.reason)
        if route.intent == "notes_list":
            return self._run_tool_action(state, "notes", "list", {}, route.reason)
        if route.intent == "filesystem_list":
            return self._run_tool_action(state, "filesystem", "list", {"path": route.path or route.text}, route.reason)
        if route.intent == "filesystem_read":
            return self._run_tool_action(state, "filesystem", "read", {"path": route.path or route.text}, route.reason)
        if route.intent == "shell_run":
            command = normalize_shell_command(route.command or route.text or text)
            return self._run_tool_action(state, "shell", "run", {"command": command, "timeout_s": 20}, route.reason, requires_approval=True)
        if route.intent == "show_insights":
            memories = await self._workspace.recent_memories(limit=20)
            logs = [item for item in memories if item.kind == "log"]
            notes = [item for item in memories if item.kind == "note"]
            persona = self._workspace.load_persona()
            lines = [
                "Weekly insights:",
                f"- logs captured: {len(logs)}",
                f"- notes captured: {len(notes)}",
            ]
            focus = persona.get("current_focus", [])
            if isinstance(focus, list) and focus:
                lines.append(f"- current focus signals: {len(focus)}")
            if notes:
                lines.append(f"- recent note theme: {notes[0].text}")
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text="\n".join(lines), run_id=state.run_id, state=state)
        if route.intent == "approve":
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=await self.pending_action("approve"), run_id=state.run_id, state=state)
        if route.intent == "skip":
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=await self.pending_action("skip"), run_id=state.run_id, state=state)
        return None

    def _run_tool_action(
        self,
        state: SharedState,
        tool: str,
        action: str,
        payload: dict[str, object],
        reason: str,
        *,
        requires_approval: bool = False,
    ) -> RuntimeResponse:
        clean_payload = {key: value for key, value in payload.items() if value not in {None, ""}}
        if requires_approval:
            pending = PendingToolAction(tool=tool, action=action, payload=clean_payload, reason=reason or self._tools.explain(tool, action, clean_payload))
            self._state_store.set_pending_tool_action(pending)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(
                text=f"{self._tools.explain(tool, action, clean_payload)} Use `approve` to continue or `skip` to cancel.",
                run_id=state.run_id,
                state=state,
            )
        try:
            result = self._tools.execute(tool, action, clean_payload)
        except ToolError as exc:
            state.execution_status = "REJECTED"
            return RuntimeResponse(text=str(exc), run_id=state.run_id, state=state)
        state.execution_status = "COMPLETED"
        return RuntimeResponse(text=result, run_id=state.run_id, state=state)

    async def _handle_recall(self, text: str, source: str) -> Optional[RuntimeResponse]:
        low = text.lower()
        if not any(marker in low for marker in ("what did i", "remember", "recall", "earlier", "saved")):
            return None
        state = self._new_state(text, source)
        hits = await self._workspace.search_memories(text, limit=3)
        state.execution_status = "COMPLETED"
        state.memory_context = hits
        if not hits:
            return RuntimeResponse(text="I couldn't find a saved entry for that yet.", run_id=state.run_id, state=state)
        lines = [f"I found {len(hits)} matching local memory item(s)."]
        for item in hits:
            lines.append(f"- [{item.kind}] {item.text}")
        return RuntimeResponse(text="\n".join(lines), run_id=state.run_id, state=state)

    def _new_state(self, query: str, source: str) -> SharedState:
        return SharedState(run_id=uuid.uuid4().hex, query=query, source=source)

    def _persist_runtime_state(self, response: RuntimeResponse) -> None:
        state = response.state
        latest_trace = state.traces[-1].model_dump() if state.traces else None
        self._workspace.merge_state(
            {
                "runtime_status": {
                    "run_id": response.run_id,
                    "query": state.query,
                    "source": state.source,
                    "execution_status": state.execution_status,
                    "degraded_mode": bool(response.degraded or state.degraded_mode),
                    "governance_reason": state.governance_reason,
                    "retrieved_chunks": len(state.retrieved_chunks),
                    "memory_records": len(state.memory_context),
                    "critic_feedback": [item.model_dump() for item in state.critic_feedback[-5:]],
                    "failure_count": len(state.failures),
                    "latest_trace": latest_trace,
                }
            }
        )

    def _routing_context(self) -> str:
        state = self._workspace.load_state()
        parts: list[str] = []
        pending_save = state.get("pending_save")
        if isinstance(pending_save, dict):
            parts.append(f"pending_save={pending_save.get('kind', '')}:{pending_save.get('text', '')}")
        pending_plan = state.get("pending_plan")
        if isinstance(pending_plan, dict):
            parts.append(f"pending_plan={pending_plan.get('project', '')}")
        project_names = [
            str(project.get("name", "")).strip()
            for project in load_projects(self._workspace.projects_path)
            if isinstance(project, dict) and str(project.get("name", "")).strip()
        ]
        if project_names:
            parts.append("projects=" + ", ".join(project_names[:20]))
        parts.append(f"workspace_root={self._cfg.workspace_root}")
        parts.append(f"autosave_enabled={self._state_store.autosave_enabled()}")
        return "\n".join(parts)


class _InflightEntry:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.done = False
        self.result: Optional[str] = None
        self.error: Optional[Exception] = None
        self.waiters = 0


def _normalize_query(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _resolve_reminder_route(route) -> tuple[str | None, str]:
    when = route.when.strip()
    text = route.text.strip()
    if when and text:
        return parse_remind_command(f"{when} {text}")
    return parse_remind_command(text)


def _candidate_kind(text: str) -> str:
    low = text.lower()
    time_markers = ("today", "yesterday", "this week", "tonight", "energy", "mood", "focus", "daily check-in")
    return "log" if any(marker in low for marker in time_markers) else "note"


def _match_project_query(projects_path, text: str) -> str | None:
    low = text.lower()
    if not any(marker in low for marker in ("project", "what is", "what's", "tell me", "show", "open", "status", "about")):
        return None
    project = find_project(projects_path, text)
    if not isinstance(project, dict):
        return None
    return str(project.get("name", "")).strip() or None


def _describe_persona(workspace: LocalWorkspace) -> str:
    persona = workspace.load_persona()
    if not isinstance(persona, dict):
        return "I don't have a persona summary yet."
    interests = _clean_list(persona.get("interests"))
    focus = _clean_list(persona.get("current_focus"))
    wins = _clean_list(persona.get("recent_wins"))
    lines: list[str] = []
    if interests:
        lines.append("Interests: " + ", ".join(interests[:5]))
    if focus:
        lines.append("Current focus: " + "; ".join(focus[:3]))
    if wins:
        lines.append("Recent wins: " + "; ".join(wins[:3]))
    if not lines:
        return "I don't have much saved persona context yet."
    return "\n".join(lines)


def _summarize_priorities(workspace: LocalWorkspace) -> str:
    persona = workspace.load_persona()
    focus_items = _clean_list(persona.get("current_focus")) if isinstance(persona, dict) else []
    projects = load_projects(workspace.projects_path)
    active_projects = [
        project for project in projects
        if isinstance(project, dict) and str(project.get("status", "active")).strip().lower() == "active"
    ]
    open_goals: list[tuple[str, str]] = []
    for project in active_projects:
        project_name = str(project.get("name", "")).strip() or "Unnamed project"
        goals = project.get("goals", [])
        if not isinstance(goals, list):
            continue
        for goal in goals:
            if not isinstance(goal, dict) or bool(goal.get("done", False)):
                continue
            text = str(goal.get("text", "")).strip()
            if text:
                open_goals.append((project_name, text))

    lines: list[str] = []
    if focus_items:
        lines.append("Current focus signals:")
        for item in focus_items[:3]:
            lines.append(f"- {item}")
    if active_projects:
        names = [str(project.get("name", "")).strip() for project in active_projects if str(project.get("name", "")).strip()]
        if names:
            lines.append("Active projects: " + ", ".join(names[:5]))
    if open_goals:
        lines.append("Next open goals:")
        for project_name, goal_text in open_goals[:3]:
            lines.append(f"- {project_name}: {goal_text}")
    if not lines:
        return "I don't have enough saved focus or active project context yet. Try a daily check-in, save a note, or add a project goal."
    return "\n".join(lines)


def _clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
