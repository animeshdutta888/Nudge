from __future__ import annotations

import asyncio
import re
import threading
import uuid
from typing import Any, Optional

from agents.critic import CriticAgent
from agents.governance import GovernanceAgent
from agents.manager import ManagerAgent
from agents.memory import MemoryAgent
from agents.retrieval import RetrievalAgent
from agents.synthesis import SynthesisAgent
from app.agent.router import IntentRouter
from app.agent.state import PendingPlan, PendingSave, PendingToolAction, StateStore
from app.config import Config
from app.services.llm import LlmConfig
from app.services.retrieval import Retriever
from app.services.semantic_cache import SemanticCache
from app.tools.daily_plan import add_priority_to_today_plan, close_today_plan, latest_daily_plan, previous_daily_plan, remove_priority_from_today_plan, save_daily_plan
from app.tools.reflection_planner import build_close_day_summary, infer_goal_completions, parse_close_day_reflection, render_close_day_log, render_close_day_response
from app.tools.local_tools import LocalToolExecutor, ToolError, extract_remind_text, normalize_shell_command
from app.tools.projects import add_goal, add_project, describe_project, find_project, load_projects, mark_goal, projects_summary
from app.tools.reminders import add_reminder, list_reminders, mark_done, resolve_reminder_request
from app.utils.presentation import assistant_display_text
from app.utils.time import parse_iso_to_local_date, today_local_date
from observability.logger import ExecutionLogger
from orchestration.engine import OrchestrationEngine
from schemas.shared import RuntimeResponse, SharedState, TraceEvent
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
            await self._workspace.append_conversation(text, smalltalk_response.text, source, smalltalk_response.tool_result)
            self._persist_runtime_state(smalltalk_response)
            return smalltalk_response

        command_response = await self._handle_command(text, source)
        if command_response is not None:
            await self._workspace.append_conversation(text, command_response.text, source, command_response.tool_result)
            self._persist_runtime_state(command_response)
            return command_response

        routed_response = await self._handle_routed_intent(text, source)
        if routed_response is not None:
            await self._workspace.append_conversation(text, routed_response.text, source, routed_response.tool_result)
            self._persist_runtime_state(routed_response)
            return routed_response

        recall_response = await self._handle_recall(text, source)
        if recall_response is not None:
            await self._workspace.append_conversation(text, recall_response.text, source, recall_response.tool_result)
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
        await self._workspace.append_conversation(text, answer, source, None)
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
        response = await self.pending_action_response(action, persist=False)
        return response.text

    async def pending_action_response(self, action: str, source: str = "dashboard", persist: bool = False) -> RuntimeResponse:
        state = self._workspace.load_state()
        response_text = "Unsupported action."
        tool_result: Optional[dict[str, object]] = None
        if action == "approve":
            pending_plan = state.get("pending_plan")
            if isinstance(pending_plan, dict):
                if str(pending_plan.get("plan_kind", "")).strip() == "daily_plan":
                    priorities_raw = pending_plan.get("priorities", pending_plan.get("goals", []))
                    priorities = [str(item).strip() for item in priorities_raw if str(item).strip()] if isinstance(priorities_raw, list) else []
                    carry_forward_raw = pending_plan.get("carry_forward", [])
                    carry_forward = [str(item).strip() for item in carry_forward_raw if str(item).strip()] if isinstance(carry_forward_raw, list) else []
                    summary = str(pending_plan.get("summary", "")).strip()
                    plan = save_daily_plan(
                        self._workspace.daily_plans_path,
                        priorities,
                        summary=summary,
                        source="start_day",
                        carry_forward=carry_forward,
                    )
                    state["pending_plan"] = None
                    self._workspace.save_state(state)
                    response_text = "Saved today's plan."
                    if plan.get("priorities"):
                        response_text += "\n" + "\n".join(f"- {item}" for item in plan.get("priorities", []))
                    runtime_state = self._new_state("approve", source)
                    runtime_state.execution_status = "COMPLETED"
                    if persist:
                        await self._workspace.append_conversation("approve", response_text, source, None)
                    return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
                if str(pending_plan.get("plan_kind", "")).strip() == "close_day_review":
                    payload = pending_plan.get("payload", {})
                    payload_dict = payload if isinstance(payload, dict) else {}
                    wins_raw = payload_dict.get("wins", pending_plan.get("wins", []))
                    blockers_raw = payload_dict.get("blockers", pending_plan.get("blockers", []))
                    carry_raw = payload_dict.get("carry_forward", pending_plan.get("carry_forward", []))
                    summary = str(payload_dict.get("summary", pending_plan.get("summary", ""))).strip()
                    wins = [str(item).strip() for item in wins_raw if str(item).strip()] if isinstance(wins_raw, list) else []
                    blockers = [str(item).strip() for item in blockers_raw if str(item).strip()] if isinstance(blockers_raw, list) else []
                    carry_forward = [str(item).strip() for item in carry_raw if str(item).strip()] if isinstance(carry_raw, list) else []
                    updates_raw = payload_dict.get("project_goal_updates", pending_plan.get("project_goal_updates", []))
                    updates = updates_raw if isinstance(updates_raw, list) else []
                    plan = close_today_plan(
                        self._workspace.daily_plans_path,
                        wins=wins,
                        blockers=blockers,
                        carry_forward=carry_forward,
                        summary=summary,
                        source="close_day",
                    )
                    for update in updates:
                        if not isinstance(update, dict):
                            continue
                        project = str(update.get("project", "")).strip()
                        goal_index = int(update.get("goal_index", 0) or 0)
                        if project and goal_index > 0:
                            mark_goal(self._workspace.projects_path, project, goal_index, True)
                    reflection_text = render_close_day_log(wins, blockers, carry_forward)
                    await self._memory.save_explicit("log", reflection_text)
                    self._workspace.refresh_persona_snapshot()
                    state["pending_plan"] = None
                    state["close_day_session"] = None
                    self._workspace.save_state(state)
                    response_text = "Saved today's reflection and closed today's plan."
                    if plan.get("carry_forward"):
                        response_text += "\nCarry forward:\n" + "\n".join(f"- {item}" for item in plan.get("carry_forward", []))
                    runtime_state = self._new_state("approve", source)
                    runtime_state.execution_status = "COMPLETED"
                    runtime_state.traces.extend(_close_day_trace({
                        "wins": wins,
                        "blockers": blockers,
                        "carry_forward": carry_forward,
                    }, approved=True))
                    if persist:
                        await self._workspace.append_conversation("approve", response_text, source, None)
                    return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
                project = str(pending_plan.get("project", "")).strip()
                goals = pending_plan.get("goals", [])
                created = add_project(self._workspace.projects_path, project)
                if not created and not any(str(item.get("name", "")).strip().lower() == project.lower() for item in load_projects(self._workspace.projects_path)):
                    response_text = "I couldn't create that project."
                    runtime_state = self._new_state("approve", source)
                    runtime_state.execution_status = "REJECTED"
                    return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
                added = 0
                for goal in goals if isinstance(goals, list) else []:
                    if add_goal(self._workspace.projects_path, project, str(goal)):
                        added += 1
                state["pending_plan"] = None
                self._workspace.save_state(state)
                response_text = f"Saved plan to project `{project}` with {added} goal" + ("" if added == 1 else "s") + "."
                runtime_state = self._new_state("approve", source)
                runtime_state.execution_status = "COMPLETED"
                if persist:
                    await self._workspace.append_conversation("approve", response_text, source, None)
                return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
            pending_save = state.get("pending_save")
            if isinstance(pending_save, dict):
                kind = str(pending_save.get("kind", "note"))
                text = str(pending_save.get("text", ""))
                await self._memory.save_explicit(kind, text)
                self._workspace.refresh_persona_snapshot()
                state["pending_save"] = None
                self._workspace.save_state(state)
                response_text = f"Saved as {kind}."
                runtime_state = self._new_state("approve", source)
                runtime_state.execution_status = "COMPLETED"
                if persist:
                    await self._workspace.append_conversation("approve", response_text, source, None)
                return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
            pending_tool_action = state.get("pending_tool_action")
            if isinstance(pending_tool_action, dict):
                payload_raw = pending_tool_action.get("payload")
                payload_dict = payload_raw if isinstance(payload_raw, dict) else {}
                tool = str(payload_dict.get("tool", pending_tool_action.get("tool", ""))).strip()
                action_name = str(payload_dict.get("action", pending_tool_action.get("action", ""))).strip()
                payload = payload_dict.get("payload", pending_tool_action.get("payload", {}))
                if tool and action_name and isinstance(payload, dict):
                    try:
                        execution = self._tools.execute(tool, action_name, payload)
                    except ToolError as exc:
                        state["pending_tool_action"] = None
                        self._workspace.save_state(state)
                        response_text = str(exc)
                        runtime_state = self._new_state("approve", source)
                        runtime_state.execution_status = "REJECTED"
                        if persist:
                            await self._workspace.append_conversation("approve", response_text, source, None)
                        return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
                    state["pending_tool_action"] = None
                    self._workspace.save_state(state)
                    response_text = execution.text
                    tool_result = execution.result
                    runtime_state = self._new_state("approve", source)
                    runtime_state.execution_status = "COMPLETED"
                    if persist:
                        await self._workspace.append_conversation("approve", response_text, source, tool_result)
                    return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, tool_result=tool_result, state=runtime_state)
            response_text = "Nothing pending."
            runtime_state = self._new_state("approve", source)
            runtime_state.execution_status = "COMPLETED"
            return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
        if action == "skip":
            if state.get("pending_plan") is not None:
                state["pending_plan"] = None
                self._workspace.save_state(state)
                response_text = "Skipped plan."
                runtime_state = self._new_state("skip", source)
                runtime_state.execution_status = "COMPLETED"
                if persist:
                    await self._workspace.append_conversation("skip", response_text, source, None)
                return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
            if state.get("pending_save") is not None:
                state["pending_save"] = None
                self._workspace.save_state(state)
                response_text = "Skipped saving."
                runtime_state = self._new_state("skip", source)
                runtime_state.execution_status = "COMPLETED"
                if persist:
                    await self._workspace.append_conversation("skip", response_text, source, None)
                return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
            if state.get("pending_tool_action") is not None:
                state["pending_tool_action"] = None
                self._workspace.save_state(state)
                response_text = "Skipped tool action."
                runtime_state = self._new_state("skip", source)
                runtime_state.execution_status = "COMPLETED"
                if persist:
                    await self._workspace.append_conversation("skip", response_text, source, None)
                return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
            response_text = "Nothing pending."
            runtime_state = self._new_state("skip", source)
            runtime_state.execution_status = "COMPLETED"
            return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)
        runtime_state = self._new_state(action, source)
        runtime_state.execution_status = "REJECTED"
        return RuntimeResponse(text=response_text, run_id=runtime_state.run_id, state=runtime_state)

    async def _handle_command(self, text: str, source: str) -> Optional[RuntimeResponse]:
        low = text.lower()
        state = self._new_state(text, source)
        if low in {"start my day", "nudge, start my day", "nudge, start my day.", "start-day", "start day"}:
            return await self._start_day(state)
        if low in {"close my day", "nudge, close my day", "nudge, close my day.", "close-day", "close day"}:
            return self._begin_close_day(state)
        close_day_response = self._handle_close_day_follow_up(text, source, require_session=True)
        if close_day_response is not None:
            return close_day_response
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
        daily_plan_edit = _extract_daily_plan_edit(text)
        if daily_plan_edit is not None:
            response = self._apply_daily_plan_update(state, operation=daily_plan_edit["operation"], item_text=daily_plan_edit["text"])
            if response is not None:
                return response
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
            resolution = resolve_reminder_request(text.split(":", 1)[1].strip())
            if resolution.error:
                state.execution_status = "REJECTED"
                return RuntimeResponse(text=resolution.error, run_id=state.run_id, state=state)
            reminder = add_reminder(self._workspace.reminders_path, resolution.text, resolution.due_ts)
            state.execution_status = "COMPLETED"
            if reminder.due_ts:
                return RuntimeResponse(text=f"Saved reminder for {reminder.due_ts}.", run_id=state.run_id, state=state)
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
            resolution = resolve_reminder_request(text, when_hint=route.when, text_hint=route.text)
            if resolution.error:
                state.execution_status = "REJECTED"
                return RuntimeResponse(text=resolution.error, run_id=state.run_id, state=state)
            reminder = add_reminder(self._workspace.reminders_path, resolution.text, resolution.due_ts)
            state.execution_status = "COMPLETED"
            if reminder.due_ts:
                return RuntimeResponse(text=f"Saved reminder for {reminder.due_ts}.", run_id=state.run_id, state=state)
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
        if route.intent == "close_day":
            state.execution_status = "COMPLETED"
            return self._begin_close_day(state)
        if route.intent == "close_day_reflection":
            return self._handle_close_day_follow_up(route.text or text, source, require_session=False)
        if route.intent == "show_daily_plan":
            state.execution_status = "COMPLETED"
            return RuntimeResponse(text=_describe_today_plan(self._workspace.daily_plans_path), run_id=state.run_id, state=state)
        if route.intent == "update_daily_plan":
            response = self._apply_daily_plan_update(state, operation=route.operation or "add", item_text=route.text)
            if response is not None:
                return response
            state.execution_status = "REJECTED"
            return RuntimeResponse(text="Tell me what to change in today's plan.", run_id=state.run_id, state=state)
        if route.intent == "notes_create":
            return self._run_tool_action(state, "notes", "create", {"text": route.text or extract_remind_text(text)}, route.reason)
        if route.intent == "notes_search":
            return self._run_tool_action(state, "notes", "search", {"query": route.query or route.text or text}, route.reason)
        if route.intent == "notes_list":
            return self._run_tool_action(state, "notes", "list", {}, route.reason)
        if route.intent == "filesystem_list":
            return self._run_tool_action(
                state,
                "filesystem",
                "list",
                {"path": route.path or route.text, "base_path": self._last_filesystem_base_path()},
                route.reason,
            )
        if route.intent == "filesystem_read":
            return self._run_tool_action(
                state,
                "filesystem",
                "read",
                {"path": route.path or route.text, "base_path": self._last_filesystem_base_path()},
                route.reason,
            )
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
            execution = self._tools.execute(tool, action, clean_payload)
        except ToolError as exc:
            state.execution_status = "REJECTED"
            return RuntimeResponse(text=str(exc), run_id=state.run_id, state=state)
        self._remember_tool_context(execution.result)
        state.execution_status = "COMPLETED"
        return RuntimeResponse(text=execution.text, run_id=state.run_id, tool_result=execution.result, state=state)

    def _last_filesystem_base_path(self) -> str:
        state = self._workspace.load_state()
        raw = state.get("last_filesystem_path")
        return str(raw).strip() if raw is not None else ""

    def _remember_tool_context(self, tool_result: Optional[dict[str, Any]]) -> None:
        if not isinstance(tool_result, dict):
            return
        if str(tool_result.get("kind", "")).strip() not in {"filesystem_list", "filesystem_read"}:
            return
        path = str(tool_result.get("path", "")).strip()
        if not path:
            return
        self._workspace.merge_state({"last_filesystem_path": path})

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
        if not state.traces:
            state.traces.append(
                TraceEvent(
                    agent="Runtime",
                    step="respond",
                    status="OK" if state.execution_status != "REJECTED" else "ERROR",
                    message="Completed a local Nudge run.",
                    payload={
                        "query": state.query,
                        "source": state.source,
                        "execution_status": state.execution_status,
                    },
                )
            )
        current_state = self._workspace.load_state()
        runtime_status = current_state.get("runtime_status") if isinstance(current_state, dict) else {}
        previous_latest_trace = runtime_status.get("latest_trace") if isinstance(runtime_status, dict) and isinstance(runtime_status.get("latest_trace"), dict) else None
        latest_trace = state.traces[-1].model_dump() if state.traces else previous_latest_trace
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

    async def _start_day(self, state: SharedState) -> RuntimeResponse:
        memories = await self._workspace.recent_memories(limit=20)
        projects = load_projects(self._workspace.projects_path)
        reminders = list_reminders(self._workspace.reminders_path, upcoming_days=2)
        today_plan = latest_daily_plan(self._workspace.daily_plans_path)
        previous_plan = previous_daily_plan(self._workspace.daily_plans_path)
        plan = _build_start_day_plan(memories, projects, reminders, today_plan, previous_plan)

        state.traces.extend(_start_day_trace(plan))
        self._state_store.set_pending_plan(
            PendingPlan(
                project="daily-plan",
                summary=str(plan.get("summary", "")).strip(),
                goals=list(plan.get("priorities", [])),
                priorities=list(plan.get("priorities", [])),
                carry_forward=list(plan.get("carry_forward", [])),
                previous_plan_date=str(plan.get("previous_plan_date", "")).strip(),
                plan_kind="daily_plan",
                reason="User asked to start the day and Nudge prepared a focused local plan.",
                source=state.source,
            )
        )
        state.execution_status = "COMPLETED"
        return RuntimeResponse(text=_render_start_day_response(plan), run_id=state.run_id, state=state)

    def _begin_close_day(self, state: SharedState) -> RuntimeResponse:
        current = self._workspace.load_state()
        current["close_day_session"] = {
            "status": "awaiting_reflection",
            "started_at": state.run_id,
            "source": state.source,
        }
        self._workspace.save_state(current)
        state.traces.extend(_close_day_trace({}, started=True))
        state.execution_status = "COMPLETED"
        return RuntimeResponse(
            text=(
                "Close My Day check-in:\n"
                "What did you finish today?\n"
                "What got stuck?\n"
                "What should carry forward tomorrow?\n\n"
                "Reply in one message, for example:\n"
                "finished: shipped dashboard refresh; stuck: reminder parsing edge case; carry: write demo script"
            ),
            run_id=state.run_id,
            state=state,
        )

    def _handle_close_day_follow_up(self, text: str, source: str, *, require_session: bool) -> Optional[RuntimeResponse]:
        current = self._workspace.load_state()
        session = current.get("close_day_session") if isinstance(current, dict) else None
        has_active_session = isinstance(session, dict) and str(session.get("status", "")).strip() == "awaiting_reflection"
        if require_session and not has_active_session:
            return None
        low = text.strip().lower()
        if low in {"approve", "skip"}:
            return None
        parsed = parse_close_day_reflection(text)
        state = self._new_state(text, source)
        if parsed is None:
            state.execution_status = "REJECTED"
            return None if not require_session else RuntimeResponse(
                text="I couldn't parse that reflection yet. Please reply like `finished: ...; stuck: ...; carry: ...`.",
                run_id=state.run_id,
                state=state,
            )
        today_plan = latest_daily_plan(self._workspace.daily_plans_path)
        project_updates = infer_goal_completions(load_projects(self._workspace.projects_path), parsed.get("wins", []))
        summary = build_close_day_summary(parsed)
        self._state_store.set_pending_plan(
            PendingPlan(
                project="daily-plan",
                summary=summary,
                goals=parsed.get("carry_forward", []),
                priorities=parsed.get("carry_forward", []),
                carry_forward=parsed.get("carry_forward", []),
                plan_kind="close_day_review",
                reason="User completed the Close My Day reflection and Nudge prepared the update for approval.",
                source=source,
            )
        )
        refreshed = self._workspace.load_state()
        pending_plan = refreshed.get("pending_plan") if isinstance(refreshed, dict) else {}
        if isinstance(pending_plan, dict):
            pending_plan["wins"] = parsed.get("wins", [])
            pending_plan["blockers"] = parsed.get("blockers", [])
            pending_plan["carry_forward"] = parsed.get("carry_forward", [])
            pending_plan["project_goal_updates"] = project_updates
            payload = pending_plan.get("payload")
            if isinstance(payload, dict):
                payload["wins"] = parsed.get("wins", [])
                payload["blockers"] = parsed.get("blockers", [])
                payload["carry_forward"] = parsed.get("carry_forward", [])
                payload["project_goal_updates"] = project_updates
                payload["current_plan_priorities"] = today_plan.get("priorities", []) if isinstance(today_plan, dict) else []
                pending_plan["payload"] = payload
            refreshed["pending_plan"] = pending_plan
            refreshed["close_day_session"] = {
                "status": "awaiting_approval",
                "source": source,
            }
            self._workspace.save_state(refreshed)
        state.traces.extend(_close_day_trace(parsed, approved=False))
        state.execution_status = "COMPLETED"
        return RuntimeResponse(text=render_close_day_response(parsed), run_id=state.run_id, state=state)

    def _apply_daily_plan_update(self, state: SharedState, *, operation: str, item_text: str) -> Optional[RuntimeResponse]:
        clean_item = str(item_text).strip()
        action = str(operation or "add").strip().lower()
        if not clean_item:
            return None
        raw_state = self._workspace.load_state()
        pending_plan = raw_state.get("pending_plan") if isinstance(raw_state, dict) else None
        if isinstance(pending_plan, dict) and str(pending_plan.get("plan_kind", "")).strip() == "daily_plan":
            priorities_raw = pending_plan.get("priorities", pending_plan.get("goals", []))
            priorities = [str(item).strip() for item in priorities_raw if str(item).strip()] if isinstance(priorities_raw, list) else []
            priorities = _update_priority_list(priorities, action, clean_item)
            priorities = priorities[:3]
            pending_plan["priorities"] = priorities
            pending_plan["goals"] = priorities
            raw_state["pending_plan"] = pending_plan
            self._workspace.save_state(raw_state)
            state.execution_status = "COMPLETED"
            return RuntimeResponse(
                text=_render_daily_plan_update_message("Updated today's draft plan.", priorities),
                run_id=state.run_id,
                state=state,
            )
        if action == "remove":
            plan = remove_priority_from_today_plan(self._workspace.daily_plans_path, clean_item)
        else:
            plan = add_priority_to_today_plan(self._workspace.daily_plans_path, clean_item)
        if plan is None:
            state.execution_status = "REJECTED"
            return RuntimeResponse(
                text="I couldn't find today's saved plan yet. Start your day first, then I can update it.",
                run_id=state.run_id,
                state=state,
            )
        priorities = [str(item).strip() for item in plan.get("priorities", []) if str(item).strip()]
        state.execution_status = "COMPLETED"
        return RuntimeResponse(
            text=_render_daily_plan_update_message("Updated today's plan.", priorities),
            run_id=state.run_id,
            state=state,
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


def _candidate_kind(text: str) -> str:
    low = text.lower()
    time_markers = ("today", "yesterday", "this week", "tonight", "energy", "mood", "focus", "daily check-in")
    return "log" if any(marker in low for marker in time_markers) else "note"


def _match_project_query(projects_path, text: str) -> Optional[str]:
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


def _build_start_day_plan(
    memories,
    projects,
    reminders,
    today_plan: Optional[dict[str, Any]],
    previous_plan: Optional[dict[str, Any]],
) -> dict[str, Any]:
    focus_signals: list[str] = []
    for item in memories:
        text = str(item.text).strip()
        low = text.lower()
        if "focus=" in low:
            focus_signals.append(text.split("focus=", 1)[1].split(";", 1)[0].strip())

    active_projects = [
        project for project in projects
        if isinstance(project, dict) and str(project.get("status", "active")).strip().lower() == "active"
    ]
    open_goals: list[tuple[str, str]] = []
    stale_projects: list[str] = []
    today = today_local_date()
    for project in active_projects:
        project_name = str(project.get("name", "")).strip() or "Unnamed project"
        created = parse_iso_to_local_date(str(project.get("created_ts", "")))
        goals = project.get("goals", [])
        if isinstance(goals, list):
            for goal in goals:
                if not isinstance(goal, dict) or bool(goal.get("done", False)):
                    continue
                text = str(goal.get("text", "")).strip()
                if text:
                    open_goals.append((project_name, text))
        if created is not None and created.toordinal() <= today.toordinal() - 2:
            stale_projects.append(project_name)

    carry_forward: list[str] = []
    previous_plan_date = ""
    if isinstance(today_plan, dict):
        today_priorities = today_plan.get("priorities", [])
        carry_forward = [str(item).strip() for item in today_priorities if str(item).strip()][:2] if isinstance(today_priorities, list) else []
    if not carry_forward and isinstance(previous_plan, dict):
        previous_plan_date = str(previous_plan.get("date", "")).strip()
        prior_items = previous_plan.get("priorities", [])
        carry_forward = [str(item).strip() for item in prior_items if str(item).strip()][:2] if isinstance(prior_items, list) else []

    priorities: list[str] = []
    for item in carry_forward:
        if item not in priorities and len(priorities) < 3:
            priorities.append(item)
    for project_name, goal_text in open_goals:
        if len(priorities) >= 3:
            break
        candidate = f"Finish: {goal_text} ({project_name})"
        if candidate not in priorities and goal_text not in priorities:
            priorities.append(candidate)
    for signal in focus_signals:
        if len(priorities) >= 3:
            break
        candidate = f"Protect focus: {signal}"
        if candidate not in priorities:
            priorities.append(candidate)
    if not priorities:
        priorities = [
            "Review active projects and pick one concrete next step.",
            "Clear one small blocker before noon.",
            "Capture a win or blocker in Nudge by the end of the day.",
        ]

    top_task = priorities[0]
    reminder_line = "No urgent reminders."
    if reminders:
        item = reminders[0]
        reminder_line = f"Pending reminder: {item.text}" + (f" ({item.due_ts})" if item.due_ts else "")
    stale_line = ", ".join(stale_projects[:2]) if stale_projects else "No stale projects detected."
    summary = f"Highest leverage task: {top_task}"
    return {
        "summary": summary,
        "priorities": priorities[:3],
        "carry_forward": carry_forward[:2],
        "previous_plan_date": previous_plan_date,
        "stale_line": stale_line,
        "reminder_line": reminder_line,
        "active_projects_count": len(active_projects),
        "open_goals_count": len(open_goals),
        "reminders_count": len(reminders),
        "stale_projects_count": len(stale_projects),
        "focus_signals_count": len(focus_signals),
    }


def _render_start_day_response(plan: dict[str, Any]) -> str:
    priorities = [f"- {item}" for item in plan.get("priorities", []) if str(item).strip()]
    carry_forward = [f"- {item}" for item in plan.get("carry_forward", []) if str(item).strip()]
    carry_block = ""
    if carry_forward:
        previous_plan_date = str(plan.get("previous_plan_date", "")).strip()
        carry_label = "Carry forward from today's earlier plan" if not previous_plan_date else f"Carry forward from {previous_plan_date}"
        carry_block = carry_label + ":\n" + "\n".join(carry_forward) + "\n\n"
    return (
        "Good morning. Here is what matters today:\n\n"
        f"1. {plan.get('summary', 'Highest leverage task not found.')}\n"
        f"2. Stale project check: {plan.get('stale_line', 'No stale projects detected.')}\n"
        f"3. {plan.get('reminder_line', 'No urgent reminders.')}\n\n"
        + carry_block
        + "Suggested plan:\n"
        + "\n".join(priorities)
        + "\n\nI can save this as today's plan. Approve?"
    )


def _start_day_trace(plan: dict[str, Any]) -> list[TraceEvent]:
    priorities = [str(item).strip() for item in plan.get("priorities", []) if str(item).strip()]
    carry_forward = [str(item).strip() for item in plan.get("carry_forward", []) if str(item).strip()]
    return [
        TraceEvent(
            agent="IntentRouter",
            step="start_day",
            status="OK",
            message="Recognized the dedicated Start My Day workflow.",
            payload={"intent": "start_day"},
        ),
        TraceEvent(
            agent="ContextRetrieval",
            step="collect_context",
            status="OK",
            message="Loaded local projects, reminders, recent memory, and prior daily plans.",
            payload={
                "active_projects": int(plan.get("active_projects_count", 0) or 0),
                "open_goals": int(plan.get("open_goals_count", 0) or 0),
                "upcoming_reminders": int(plan.get("reminders_count", 0) or 0),
                "carry_forward": len(carry_forward),
            },
        ),
        TraceEvent(
            agent="DailyPlanning",
            step="compose_plan",
            status="OK",
            message="Built a 3-priority plan with carry-forward and stale-project checks.",
            payload={
                "priorities": priorities[:3],
                "stale_projects": int(plan.get("stale_projects_count", 0) or 0),
                "focus_signals": int(plan.get("focus_signals_count", 0) or 0),
            },
        ),
        TraceEvent(
            agent="Approval",
            step="await_approval",
            status="OK",
            message="Prepared the plan for approval before saving durable state.",
            payload={"requires_approval": True},
        ),
    ]


def _clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_daily_plan_edit(text: str) -> Optional[dict[str, str]]:
    raw = str(text).strip()
    patterns = (
        (r"^edit\s+today(?:'s|s)?\s+plan\s+to\s+(?:add|include)\s+(.+)$", "add"),
        (r"^update\s+today(?:'s|s)?\s+plan\s+to\s+(?:add|include)\s+(.+)$", "add"),
        (r"^add\s+(.+?)\s+to\s+today(?:'s|s)?\s+plan$", "add"),
        (r"^include\s+(.+?)\s+in\s+today(?:'s|s)?\s+plan$", "add"),
        (r"^remove\s+(.+?)\s+from\s+today(?:'s|s)?\s+plan$", "remove"),
        (r"^edit\s+today(?:'s|s)?\s+plan\s+to\s+remove\s+(.+)$", "remove"),
    )
    for pattern, operation in patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).strip().rstrip(".")
        return {"operation": operation, "text": candidate}
    return None


def _update_priority_list(priorities: list[str], operation: str, item_text: str) -> list[str]:
    action = str(operation).strip().lower()
    clean_item = str(item_text).strip()
    existing = [str(item).strip() for item in priorities if str(item).strip()]
    if action == "remove":
        lowered = clean_item.lower()
        updated = [item for item in existing if item.lower() != lowered]
        if len(updated) == len(existing):
            updated = [item for item in existing if lowered not in item.lower()]
        return updated
    if clean_item not in existing:
        existing.append(clean_item)
    return existing


def _render_daily_plan_update_message(prefix: str, priorities: list[str]) -> str:
    if not priorities:
        return prefix + "\n- No priorities left in today's plan."
    return prefix + "\n" + "\n".join(f"- {item}" for item in priorities)


def _describe_today_plan(path) -> str:
    plan = latest_daily_plan(path)
    if not isinstance(plan, dict):
        return "I couldn't find a saved plan for today yet."
    priorities = [str(item).strip() for item in plan.get("priorities", []) if str(item).strip()]
    if not priorities:
        return "Today's plan is saved, but it does not have priorities yet."
    summary = str(plan.get("summary", "")).strip()
    lines = ["Today's plan:"]
    if summary:
        lines.append(summary)
    lines.extend(f"- {item}" for item in priorities)
    if plan.get("status") == "closed":
        close_summary = str(plan.get("close_day_summary", "")).strip()
        if close_summary:
            lines.append(close_summary)
    return "\n".join(lines)


def _close_day_trace(parsed: dict[str, list[str]], *, started: bool = False, approved: bool = False) -> list[TraceEvent]:
    if started:
        return [
            TraceEvent(
                agent="IntentRouter",
                step="close_day",
                status="OK",
                message="Recognized the Close My Day workflow and opened a reflection prompt.",
                payload={"intent": "close_day"},
            )
        ]
    return [
        TraceEvent(
            agent="Reflection",
            step="summarize_day",
            status="OK",
            message="Extracted wins, blockers, and carry-forward work from the user's reflection.",
            payload={
                "wins": len(parsed.get("wins", [])),
                "blockers": len(parsed.get("blockers", [])),
                "carry_forward": len(parsed.get("carry_forward", [])),
            },
        ),
        TraceEvent(
            agent="Approval",
            step="await_approval" if not approved else "approved",
            status="OK",
            message="Prepared the close-day update for approval." if not approved else "Saved the close-day reflection and updated today's plan.",
            payload={"requires_approval": not approved},
        ),
    ]
