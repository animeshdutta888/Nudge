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
from app.config import Config
from app.services.llm import LlmConfig
from app.services.retrieval import Retriever
from app.services.semantic_cache import SemanticCache
from app.tools.projects import add_goal, add_project, load_projects, mark_goal, projects_summary
from app.tools.reminders import add_reminder, list_reminders, mark_done, parse_remind_command
from app.utils.presentation import assistant_display_text
from observability.logger import ExecutionLogger
from orchestration.engine import OrchestrationEngine
from schemas.shared import PendingPlan, PendingSave, RuntimeResponse, SharedState
from storage.local import LocalWorkspace


class NudgeRuntime:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._workspace = LocalWorkspace(cfg.data_dir, cfg.traces_db_path)
        llm = LlmConfig(base_url=cfg.ollama_base_url, model=cfg.model, timeout_s=cfg.timeout_s)
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
        if low in {"persona", "show persona"}:
            persona = self._workspace.load_persona()
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=str(persona), run_id=state.run_id, state=state)
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


class _InflightEntry:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.done = False
        self.result: Optional[str] = None
        self.error: Optional[Exception] = None
        self.waiters = 0


def _normalize_query(text: str) -> str:
    return " ".join((text or "").strip().lower().split())
