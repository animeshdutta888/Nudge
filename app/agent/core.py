from __future__ import annotations

import json
from typing import Any

from app.agent import prompts
from app.agent.graph import AgentWorkflow
from app.agent.memory import Memory
from app.agent.planner import Planner, Plan
from app.agent.state import PendingPlan, PendingSave, StateStore
from app.config import Config
from app.persona.builder import build_persona_from_logs
from app.services.llm import LlmConfig, LlmError, ask_llm
from app.services.conversations import append_conversation
from app.services.retrieval import Retriever
from app.services.storage import ensure_json_file, read_json, write_json
from app.tools.insights import weekly_insights, weekly_review
from app.tools.questions import pick_persona_question
from app.tools.reminders import add_reminder, list_reminders, mark_done, parse_remind_command
from app.tools.activities import recommend_activities_from_persona
from app.tools.projects import add_goal, add_project, load_projects, mark_goal, projects_summary
from app.tools.repair import delete_recent, edit_recent, pin_recent, recent_items
from app.utils.logger import warn
from app.utils.time import now_local_iso


class NudgeAgent:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._llm = LlmConfig(
            base_url=cfg.ollama_base_url,
            model=cfg.model,
            timeout_s=cfg.timeout_s,
        )

        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        ensure_json_file(cfg.logs_path, [])
        ensure_json_file(cfg.notes_path, [])
        ensure_json_file(cfg.persona_path, {})
        ensure_json_file(cfg.embeddings_path, {"next_id": 1, "items": {}})
        ensure_json_file(cfg.state_path, {"pending_save": None, "autosave_enabled": True})
        ensure_json_file(cfg.reminders_path, [])
        ensure_json_file(cfg.conversations_path, [])
        ensure_json_file(cfg.projects_path, [])

        self._retriever = Retriever(
            index_path=cfg.faiss_index_path,
            map_path=cfg.embeddings_path,
            ollama=self._llm,
            embed_model=cfg.embed_model,
        )
        self._memory = Memory(cfg.logs_path, cfg.notes_path, self._retriever)
        self._planner = Planner(self._llm)
        self._state = StateStore(cfg.state_path)
        self._workflow = AgentWorkflow(self)

    def run_agent(self, user_text: str) -> str:
        t = user_text.strip()
        if not t:
            return "Say `log: ...`, `note: ...`, or ask a question."

        low = t.lower()
        if _is_smalltalk_input(t):
            # Greetings should not trigger "second brain recall" or leak stored context.
            return (
                "Hey. What do you want to do?\n"
                "- `checkin` (daily)\n"
                "- `note: ...` or `log: ...`\n"
                "- `remind: ...`\n"
                "- ask a question"
            )
        # If we're mid "persona question" flow, allow skipping without saving or invoking other skip behavior.
        if low in {"skip", "pass", "idk", "i don't know", "dont know", "not sure"} and self._state.last_question() is not None:
            self._state.clear_last_question()
            return "Skipped. Type `ask` for another question."

        if low.startswith("remind:"):
            return self._handle_remind(t)
        if low in {"reminders", "show reminders"}:
            return self._render_reminders()
        if low.startswith("done "):
            if "::" in low:
                return self._handle_goal_done(t)
            return self._handle_done(t)
        if low.startswith("project add "):
            return self._handle_project_add(t)
        if low.startswith("goal add "):
            return self._handle_goal_add(t)
        if low in {"projects", "goals"}:
            return projects_summary(self._cfg.projects_path)
        if low in {"timeline", "recent timeline"}:
            return self._timeline_text()
        if low in {"story week", "week story", "narrative"}:
            return self._story_week()
        if low in {"recent", "recent memories"}:
            return self._recent_memory_summary()
        if low.startswith("delete "):
            return self._handle_delete(t)
        if low.startswith("edit "):
            return self._handle_edit(t)
        if low.startswith("pin "):
            return self._handle_pin(t, True)
        if low.startswith("unpin "):
            return self._handle_pin(t, False)
        if low in {"ask", "question", "ask me", "qotd"} or low.startswith("ask "):
            return self._handle_ask_cmd(t)
        if low.startswith("autosave"):
            return self._handle_autosave_cmd(t)
        if low in {"approve", "yes save", "save it"}:
            return self._approve_pending()
        if low in {"skip", "discard", "no", "don't save", "dont save"}:
            return self._skip_pending()
        if low in {"persona", "show persona"}:
            return self._render_json(read_json(self._cfg.persona_path, default={}))
        if low in {"insights", "weekly"}:
            return weekly_insights(self._llm, self._memory)
        if low in {"review week", "weekly review", "review"}:
            persona = read_json(self._cfg.persona_path, default={})
            upcoming = list_reminders(self._cfg.reminders_path, upcoming_days=7)
            return weekly_review(self._llm, self._memory, persona=persona, reminders=upcoming, days=7)
        if _is_activity_request(t):
            return self._recommend_activities(t)
        if self._should_offer_plan(t):
            proposal = self._build_task_plan(t)
            if proposal is not None:
                self._state.set_pending_plan(proposal)
                self._state.set_pending_save(None)
                return self._render_pending_plan(proposal)

        return self._workflow.run(t)

    def _handle_ask_cmd(self, raw: str) -> str:
        low = raw.strip().lower()
        if low in {"ask reset", "ask restart", "ask clear"}:
            self._state.reset_questions()
            return "Reset persona questions history."
        asked = self._state.asked_question_ids()
        q = pick_persona_question(asked)
        self._state.mark_question_asked(q.qid, q.text)
        return q.text

    def _handle_remind(self, raw: str) -> str:
        after = raw.split(":", 1)[1].strip()
        due, text = parse_remind_command(after)
        if not text:
            return "Usage: `remind: <when> <text>` e.g. `remind: tomorrow 09:00 call mom`"
        r = add_reminder(self._cfg.reminders_path, text=text, due_ts=due)
        # Best-effort: index reminders so you can ask about them later.
        try:
            self._retriever.add(kind="reminder", ts=r.created_ts, text=f"{r.text}" + (f" (due {r.due_ts})" if r.due_ts else ""))
        except Exception:
            pass
        return "Saved reminder."

    def _render_reminders(self) -> str:
        items = list_reminders(self._cfg.reminders_path, upcoming_days=30)
        if not items:
            return "No reminders."
        lines: list[str] = ["Reminders:"]
        for r in items[:12]:
            when = f" due {r.due_ts}" if r.due_ts else ""
            lines.append(f"- {r.id}:{when} {r.text}")
        return "\n".join(lines)

    def _handle_done(self, raw: str) -> str:
        parts = raw.strip().split(maxsplit=1)
        if len(parts) != 2:
            return "Usage: `done <id>`"
        try:
            rid = int(parts[1])
        except ValueError:
            return "Usage: `done <id>`"
        return "Marked done." if mark_done(self._cfg.reminders_path, rid) else "Reminder not found."

    def _handle_project_add(self, raw: str) -> str:
        name = raw.split("project add", 1)[1].strip()
        return "Project added." if add_project(self._cfg.projects_path, name) else "Could not add project."

    def _handle_goal_add(self, raw: str) -> str:
        rest = raw.split("goal add", 1)[1].strip()
        if "::" not in rest:
            return "Usage: `goal add <project> :: <goal>`"
        project, text = [x.strip() for x in rest.split("::", 1)]
        return "Goal added." if add_goal(self._cfg.projects_path, project, text) else "Could not add goal."

    def _handle_goal_done(self, raw: str) -> str:
        # done <project> :: <goal_number>
        rest = raw.split("done", 1)[1].strip()
        if "::" not in rest:
            return "Usage: `done <project> :: <goal_number>`"
        project, index_text = [x.strip() for x in rest.split("::", 1)]
        try:
            idx = int(index_text)
        except ValueError:
            return "Usage: `done <project> :: <goal_number>`"
        return "Goal marked done." if mark_goal(self._cfg.projects_path, project, idx, True) else "Goal not found."

    def _timeline_text(self) -> str:
        items = _timeline_items(self._cfg)
        if not items:
            return "No timeline items yet."
        lines = ["Recent timeline:"]
        for item in items[:12]:
            lines.append(f"- {item['kind']}: {item['text']}")
        return "\n".join(lines)

    def _story_week(self) -> str:
        items = _timeline_items(self._cfg, days=7)
        if not items:
            return "I don't have enough activity for a weekly story yet."
        focus = []
        wins = []
        for item in items:
            fields = _parse_checkin_fields(item["text"])
            if fields:
                fv = str(fields.get("focus", "")).strip()
                wv = str(fields.get("win", "")).strip()
                if fv and fv.lower() != "n/a":
                    focus.append(fv)
                if wv and wv.lower() != "n/a":
                    wins.append(wv)
        parts = []
        if focus:
            parts.append("This week centered on " + _join_human(_dedupe_keep_order(focus)[:2]) + ".")
        if wins:
            parts.append("Your visible wins included " + _join_human(_dedupe_keep_order(wins)[:3]) + ".")
        projects = load_projects(self._cfg.projects_path)
        active = [str(p.get("name", "")).strip() for p in projects if isinstance(p, dict) and str(p.get("status", "active")) == "active"]
        if active:
            parts.append("Active projects in your system are " + _join_human(active[:3]) + ".")
        return " ".join(parts) if parts else "I have some activity saved, but not enough structure for a weekly story yet."

    def _recent_memory_summary(self) -> str:
        logs = recent_items(self._cfg.logs_path, limit=5)
        notes = recent_items(self._cfg.notes_path, limit=5)
        lines = ["Recent logs:"]
        for item in logs:
            lines.append(f"- log {item['recent_index']}: {item.get('text', '')}")
        lines.append("")
        lines.append("Recent notes:")
        for item in notes:
            lines.append(f"- note {item['recent_index']}: {item.get('text', '')}")
        return "\n".join(lines)

    def _handle_delete(self, raw: str) -> str:
        parsed = _parse_repair_command(raw, "delete")
        if parsed is None:
            return "Usage: `delete note <n>` or `delete log <n>`"
        kind, idx, _ = parsed
        path = self._cfg.notes_path if kind == "note" else self._cfg.logs_path
        ok = delete_recent(path, idx)
        if ok and kind in {"note", "log"}:
            self._update_persona()
        return "Deleted." if ok else "Item not found."

    def _handle_edit(self, raw: str) -> str:
        parsed = _parse_repair_command(raw, "edit")
        if parsed is None:
            return "Usage: `edit note <n> :: <text>` or `edit log <n> :: <text>`"
        kind, idx, text = parsed
        path = self._cfg.notes_path if kind == "note" else self._cfg.logs_path
        ok = edit_recent(path, idx, text)
        if ok and kind in {"note", "log"}:
            self._update_persona()
        return "Updated." if ok else "Item not found."

    def _handle_pin(self, raw: str, pinned: bool) -> str:
        verb = "pin" if pinned else "unpin"
        parsed = _parse_repair_command(raw, verb)
        if parsed is None:
            return f"Usage: `{verb} note <n>` or `{verb} log <n>`"
        kind, idx, _ = parsed
        path = self._cfg.notes_path if kind == "note" else self._cfg.logs_path
        ok = pin_recent(path, idx, pinned)
        return ("Pinned." if pinned else "Unpinned.") if ok else "Item not found."

    def _handle_autosave_cmd(self, raw: str) -> str:
        parts = raw.strip().split()
        if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() in {"status", "?"}):
            return f"Autosave is {'ON' if self._state.autosave_enabled() else 'OFF'}."
        if len(parts) >= 2:
            val = parts[1].lower()
            if val in {"on", "true", "1", "yes"}:
                self._state.set_autosave_enabled(True)
                # When autosave is on, pending approvals aren't useful.
                self._state.set_pending_save(None)
                return "Autosave ON."
            if val in {"off", "false", "0", "no"}:
                self._state.set_autosave_enabled(False)
                return "Autosave OFF. I’ll ask for `approve` before saving."
        return "Usage: `autosave on`, `autosave off`, or `autosave status`."

    def _handle_plan(self, plan: Plan) -> str:
        if plan.kind == "log" and plan.explicit:
            self._memory.append_log(plan.clean_text)
            self._update_persona()
            return "Saved log."
        if plan.kind == "note" and plan.explicit:
            self._memory.add_note(plan.clean_text)
            self._update_persona()
            return "Saved note."
        # Anything else is treated as normal conversation. We may *suggest* saving with approval.
        return self._answer_and_maybe_offer_save(plan.clean_text)

    def _update_persona(self) -> None:
        existing = read_json(self._cfg.persona_path, default={})
        logs = self._memory.recent_logs(self._cfg.recent_logs_n)
        notes = self._memory.recent_notes(self._cfg.recent_logs_n)
        updated = build_persona_from_logs(self._llm, logs, notes, existing)
        updated["updated_at"] = now_local_iso()
        write_json(self._cfg.persona_path, updated)

    def _approve_pending(self) -> str:
        pending_plan = self._state.get_pending_plan()
        if pending_plan is not None:
            created = add_project(self._cfg.projects_path, pending_plan.project)
            if not created and not any(
                str(p.get("name", "")).strip().lower() == pending_plan.project.strip().lower()
                for p in load_projects(self._cfg.projects_path)
                if isinstance(p, dict)
            ):
                return "I couldn't create that project."
            added = 0
            for goal in pending_plan.goals:
                if add_goal(self._cfg.projects_path, pending_plan.project, goal):
                    added += 1
            self._state.set_pending_plan(None)
            return (
                f"Saved plan to project `{pending_plan.project}` with {added} goal"
                + ("" if added == 1 else "s")
                + "."
            )
        pending = self._state.get_pending_save()
        if pending is None:
            return "Nothing to save. If you want me to remember something, use `note: ...` or `log: ...`."
        if pending.kind == "log":
            self._memory.append_log(pending.text)
            self._update_persona()
            self._state.set_pending_save(None)
            return "Saved as log."
        self._memory.add_note(pending.text)
        self._update_persona()
        self._state.set_pending_save(None)
        return "Saved as note."

    def _skip_pending(self) -> str:
        if self._state.get_pending_plan() is not None:
            self._state.set_pending_plan(None)
            return "Skipped plan."
        if self._state.get_pending_save() is None:
            return "Nothing pending."
        self._state.set_pending_save(None)
        return "Skipped saving."

    def _build_task_plan(self, user_text: str) -> PendingPlan | None:
        persona = read_json(self._cfg.persona_path, default={})
        reminders = list_reminders(self._cfg.reminders_path, upcoming_days=14)
        context = _build_context_signals(self._memory, user_text, reminders)
        payload = prompts.TASK_PLAN.format(
            persona=json.dumps(persona, ensure_ascii=True),
            context=context,
            user=user_text,
        )
        try:
            raw = ask_llm(self._llm, payload)
            data = _parse_json_obj(raw)
        except LlmError:
            data = {}

        should_plan = bool(data.get("should_plan", False))
        project = str(data.get("project", "")).strip()
        summary = str(data.get("summary", "")).strip()
        reason = str(data.get("reason", "")).strip()
        goals = [str(x).strip() for x in data.get("goals", []) if str(x).strip()] if isinstance(data.get("goals"), list) else []

        if should_plan and project and goals:
            return PendingPlan(project=project, summary=summary, goals=goals[:4], reason=reason)
        return _fallback_task_plan(user_text)

    def _should_offer_plan(self, user_text: str) -> bool:
        # Strong deterministic planning requests should not be blocked by model misclassification.
        if _looks_like_planning_request(user_text):
            return True

        persona = read_json(self._cfg.persona_path, default={})
        reminders = list_reminders(self._cfg.reminders_path, upcoming_days=14)
        context = _build_context_signals(self._memory, user_text, reminders)
        payload = prompts.PLAN_INTENT.format(
            persona=json.dumps(persona, ensure_ascii=True),
            context=context,
            user=user_text,
        )
        try:
            raw = ask_llm(self._llm, payload)
            data = _parse_json_obj(raw)
            if isinstance(data, dict) and "should_plan" in data:
                return bool(data.get("should_plan", False))
        except LlmError:
            pass
        return False

    def _render_pending_plan(self, pending: PendingPlan) -> str:
        lines = [pending.summary or f"I sketched a small plan for `{pending.project}`."]
        lines.append("")
        lines.append(f"Project: {pending.project}")
        lines.append("Starter goals:")
        for goal in pending.goals[:4]:
            lines.append(f"- {goal}")
        lines.append("")
        lines.append("Click the thumbs up icon to add this to projects, or the thumbs down icon to skip.")
        return "\n".join(lines)

    def _answer_and_maybe_offer_save(self, user_text: str) -> str:
        answer, persona, retrieved = self._answer_core(user_text)
        return self._apply_memory_policy(answer, user_text, persona, retrieved)

    def _answer_core(self, user_text: str) -> tuple[str, Any, list[Any]]:
        if _is_recall_request(user_text):
            return self._recall_answer(user_text), {}, []
        if _is_about_me_question(user_text):
            persona = read_json(self._cfg.persona_path, default={})
            return self._about_me_answer(user_text), persona, []

        persona = read_json(self._cfg.persona_path, default={})
        retrieved = _select_retrieved(self._retriever.retrieve(user_text, k=12))
        reminders = list_reminders(self._cfg.reminders_path, upcoming_days=30)
        context = _build_context_signals(self._memory, user_text, reminders)

        payload = prompts.RESPOND.format(
            persona=json.dumps(persona, ensure_ascii=True),
            context=context,
            reminders=_format_reminders(reminders),
            memories=_format_retrieved(retrieved),
            logs=_format_logs(self._memory.logs_in_last_days(7)[-12:]),
            notes=_format_notes(self._memory.notes_in_last_days(7)[-12:]),
            user=user_text,
        )

        try:
            raw = ask_llm(self._llm, payload)
            data = _parse_json_obj(raw) or {"answer": raw.strip(), "clarifying_question": "", "used_memory_ids": []}
        except LlmError as e:
            warn(f"LLM failed; responding without it. ({e})")
            data = {
                "answer": _fallback_context_answer(self._memory, user_text, retrieved, reminders),
                "clarifying_question": "",
                "used_memory_ids": [],
            }

        answer = str(data.get("answer", "")).strip() or _fallback_context_answer(
            self._memory, user_text, retrieved, reminders
        )
        clar = str(data.get("clarifying_question", "")).strip()

        out = answer
        if _looks_generic(out) and user_text.strip().endswith("?"):
            clar = clar or _default_clarifier(user_text)
        if clar:
            out = out + "\n\n" + clar

        return out, persona, retrieved

    def _about_me_answer(self, user_text: str) -> str:
        topic = _extract_about_me_topic(user_text)
        if topic is None:
            topic = ""
        query = topic or user_text

        candidate_texts: list[str] = []
        persona = read_json(self._cfg.persona_path, default={})
        reminders = list_reminders(self._cfg.reminders_path, upcoming_days=30)

        # Deterministic fallback that works even without embeddings.
        if topic:
            hits = self._memory.search(topic, limit=10)
        else:
            hits = self._memory.search(user_text, limit=10)
        candidate_texts.extend([h["text"] for h in hits])

        try:
            retrieved = [r for r in self._retriever.retrieve(query, k=8) if not _is_noise_memory(r.item.text)]
        except Exception:
            retrieved = []
        candidate_texts.extend([r.item.text for r in retrieved])

        if _is_specific_preference_question(user_text):
            return _fallback_about_me_answer(topic, persona, candidate_texts)

        context = _build_context_signals(self._memory, user_text, reminders)
        if candidate_texts or persona:
            payload = prompts.ABOUT_ME.format(
                persona=json.dumps(persona, ensure_ascii=True),
                context=context,
                reminders=_format_reminders(reminders),
                memories=_format_retrieved(retrieved),
                user=user_text,
            )
            try:
                raw = ask_llm(self._llm, payload)
                data = _parse_json_obj(raw)
                answer = str(data.get("answer", "")).strip()
                clar = str(data.get("clarifying_question", "")).strip()
                if answer:
                    return answer if not clar else answer + "\n\n" + clar
            except LlmError:
                pass

        if not candidate_texts:
            if topic:
                return _fallback_about_me_answer(topic, persona, [])
            return self._about_me_summary()

        return _fallback_about_me_answer(topic, persona, candidate_texts)

    def _about_me_summary(self) -> str:
        persona = read_json(self._cfg.persona_path, default={})
        interests = _safe_str_list(persona.get("interests"))[:4]
        focus = _safe_str_list(persona.get("current_focus"))[:2]
        habits = _safe_str_list(persona.get("habits"))[:2]

        parts: list[str] = []
        if interests:
            parts.append("You seem to enjoy " + _join_human(interests) + ".")
        if focus:
            parts.append("Right now your focus looks like " + _join_human(focus) + ".")
        if habits:
            parts.append("A habit/theme I have saved is " + _join_human(habits) + ".")

        if parts:
            return " ".join(parts)

        # Fallback to a small deterministic summary from notes/logs.
        recent = [n.text for n in self._memory.recent_notes(5) if not _is_noise_memory(n.text)]
        if recent:
            return "I know a few things from your saved notes, but the persona is still sparse."
        return "I don't know much about you yet. A `checkin`, `note: ...`, or answering `ask` questions will help."


    def _recall_answer(self, user_text: str) -> str:
        """
        "Where was my earlier entry?" style questions should prefer deterministic recall.
        If the user asks to show/quote, we can display the exact stored line.
        """
        show_raw = _wants_raw_memory(user_text)

        # First try embeddings retrieval (fast), then deterministic scan fallback.
        try:
            retrieved = [r for r in self._retriever.retrieve(user_text, k=8) if not _is_noise_memory(r.item.text)]
        except Exception:
            retrieved = []

        if retrieved:
            top = retrieved[0].item
            if show_raw:
                return f"[{top.kind}] {top.ts}: {top.text}"
            return _summarize_memory_line(top.text)

        hits = self._memory.search(user_text, limit=3)
        if not hits:
            return "I couldn't find a saved entry for that yet."
        if show_raw:
            h = hits[0]
            return f"[{h['kind']}] {h['ts']}: {h['text']}"
        return _summarize_memory_line(hits[0]["text"])

    def _recommend_activities(self, user_text: str) -> str:
        persona = read_json(self._cfg.persona_path, default={})
        retrieved = _select_retrieved(self._retriever.retrieve(user_text, k=10))

        payload = prompts.ACTIVITIES_RECOMMEND.format(
            persona=json.dumps(persona, ensure_ascii=True),
            memories=_format_retrieved(retrieved),
            user=user_text,
        )
        try:
            raw = ask_llm(self._llm, payload)
            data = _parse_json_obj(raw)
            intro = str(data.get("answer", "")).strip()
            activities = data.get("activities", [])
            if isinstance(activities, list) and activities:
                acts = [str(x).strip() for x in activities if str(x).strip()]
                if acts:
                    header = intro or "Here are a few activity ideas you can do offline:"
                    return header + "\n" + "\n".join(f"- {a}" for a in acts[:10])
        except LlmError:
            pass

        acts = recommend_activities_from_persona(persona)
        if not acts:
            return "Tell me what you enjoy (sports, music, learning), and I’ll recommend activities."
        return "Here are a few activity ideas you can do offline:\n" + "\n".join(f"- {a}" for a in acts[:10])

    def _apply_memory_policy(self, out: str, user_text: str, persona: Any, retrieved) -> str:
        if _looks_like_question_input(user_text):
            return out
        if self._state.get_pending_save() is not None:
            return out + "\n\n(Pending save: type `approve` to save it, or `skip`.)"

        pending, ask_first = self._decide_pending_save(user_text, persona, retrieved)
        if pending is None:
            return out
        if self._state.autosave_enabled() and not ask_first:
            self._save_now(pending)
            # Keep UX minimal; don't echo the stored content.
            return out + f"\n\n(Saved as {pending.kind}.)"
        self._state.set_pending_save(pending)
        reason = pending.reason.strip()
        suffix = f" {reason}" if reason else ""
        return out + f"\n\n(I noticed something that may be worth remembering.{suffix} Type `approve` to save, or `skip`.)"

    def _save_now(self, pending: PendingSave) -> None:
        if pending.kind == "log":
            self._memory.append_log(pending.text)
        else:
            self._memory.add_note(pending.text)
        self._update_persona()

    def _decide_pending_save(self, user_text: str, persona: Any, retrieved) -> tuple[PendingSave | None, bool]:
        raw = user_text.strip()
        low = raw.lower()
        if _is_smalltalk_input(raw) or low in {"clear", "help", "approve", "skip", "quit", "exit"}:
            return None, False
        if len(raw) < 12:
            return None, False

        payload = prompts.SAVE_DECIDE.format(
            persona=json.dumps(persona, ensure_ascii=True),
            memories=_format_retrieved(retrieved),
            user=user_text,
        )

        try:
            llm_out = ask_llm(self._llm, payload)
            data = _parse_json_obj(llm_out)
            if not isinstance(data, dict):
                return None, False
            decision = str(data.get("decision", "")).strip().lower()
            if decision not in {"autosave", "ask"}:
                return None, False
            kind = str(data.get("kind", "")).strip().lower()
            text = str(data.get("text", "")).strip()
            reason = str(data.get("reason", "")).strip()
            if kind not in {"log", "note"}:
                return None, False
            if not text or len(text) < 8:
                return None, False
            if text.strip().lower() in {"hi", "hello", "hey", "clear"}:
                return None, False
            if _already_in_memory(self._memory, text, retrieved):
                return None, False
            return PendingSave(kind=kind, text=text, reason=reason), decision == "ask"
        except LlmError:
            fallback = _heuristic_pending_save(raw)
            if fallback is None or _already_in_memory(self._memory, fallback.text, retrieved):
                return None, False
            return fallback, True

    @staticmethod
    def _render_json(obj: Any) -> str:
        try:
            return json.dumps(obj, ensure_ascii=True, indent=2)
        except TypeError:
            return str(obj)


_AGENT: NudgeAgent | None = None


def run_agent(user_text: str, cfg: Config | None = None, source: str = "cli") -> str:
    global _AGENT
    if _AGENT is None:
        try:
            _AGENT = NudgeAgent(cfg or Config.load())
        except RuntimeError as e:
            return str(e)
    out = _AGENT.run_agent(user_text)
    try:
        append_conversation(_AGENT._cfg.conversations_path, user_text, out, source=source)
    except Exception:
        pass
    return out


def pending_save_action(action: str, cfg: Config | None = None) -> str:
    global _AGENT
    if _AGENT is None:
        try:
            _AGENT = NudgeAgent(cfg or Config.load())
        except RuntimeError as e:
            return str(e)
    low = (action or "").strip().lower()
    if low in {"approve", "yes", "save"}:
        return _AGENT._approve_pending()
    if low in {"skip", "discard", "no"}:
        return _AGENT._skip_pending()
    return "Unknown pending-save action."


def _format_retrieved(results) -> str:
    if not results:
        return "- (none)"
    lines: list[str] = []
    for r in results:
        item = r.item
        if _is_noise_memory(item.text):
            continue
        # Include id + score for the model, but we never print this to the user.
        lines.append(f"- id={item.id} kind={item.kind} score={r.score:.4f} text={item.text}")
    return "\n".join(lines)


def _format_logs(logs) -> str:
    lines = [f"- {l.ts}: {l.text}" for l in logs]
    return "\n".join(lines) if lines else "- (none)"


def _format_notes(notes) -> str:
    lines = [f"- {n.ts}: {n.text}" for n in notes]
    return "\n".join(lines) if lines else "- (none)"


def _format_reminders(reminders) -> str:
    if not reminders:
        return "- (none)"
    lines = []
    for r in reminders[:12]:
        due = f" due={r.due_ts}" if getattr(r, "due_ts", None) else ""
        lines.append(f"- id={r.id}{due} text={r.text}")
    return "\n".join(lines)


def _parse_json_obj(text: str) -> dict[str, Any]:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _render_answer(answer: str, bullets: Any) -> str:
    # Kept for backward compatibility; core now directly renders answer + optional clarifier.
    return answer.strip()


def _fallback_answer(user_text: str, retrieved) -> str:
    if retrieved:
        top = retrieved[0].item
        return f"I found this relevant memory: [{top.kind}] {top.ts}: {top.text}"
    return "I don't have any relevant stored memory yet. If you want me to remember something, use `note: ...` or `log: ...`."


def _fallback_context_answer(memory: Memory, user_text: str, retrieved, reminders) -> str:
    low = (user_text or "").strip().lower()
    is_question = _looks_like_question_input(user_text)

    if "remind" in low or "reminder" in low:
        active = [r for r in (reminders or []) if str(getattr(r, "text", "")).strip()]
        if active:
            if len(active) == 1:
                return f"You asked me to remind you: {active[0].text}"
            texts = [str(r.text).strip() for r in active[:3]]
            return "Your reminders include " + _join_human(texts) + "."

    latest_checkin = None
    for e in reversed(memory.logs_in_last_days(7)):
        fields = _parse_checkin_fields(e.text)
        if fields:
            latest_checkin = fields
            break

    if latest_checkin:
        if "energy" in low:
            value = str(latest_checkin.get("energy", "")).strip()
            if value and value.lower() != "n/a":
                return f"Your most recent saved energy is {value}."
        if "focus" in low:
            value = str(latest_checkin.get("focus", "")).strip()
            if value and value.lower() != "n/a":
                return f"Your most recent saved focus is {value}."
        if "week" in low:
            parts: list[str] = []
            focus = str(latest_checkin.get("focus", "")).strip()
            win = str(latest_checkin.get("win", "")).strip()
            if focus and focus.lower() != "n/a":
                parts.append(f"Your latest recorded focus is {focus}.")
            if win and win.lower() != "n/a":
                parts.append(f"A recent win is {win}.")
            if parts:
                return " ".join(parts)

    matches = memory.search(user_text, limit=3)
    if matches:
        top = matches[0]
        return _summarize_memory_line(top["text"])

    if retrieved and not is_question:
        top = retrieved[0].item
        return _summarize_memory_line(top.text)

    if is_question:
        return "I don't have enough grounded context for that yet."
    return "I don't have any relevant stored memory yet. If you want me to remember something, use `note: ...` or `log: ...`."


def _fallback_about_me_answer(topic: str, persona: Any, texts: list[str]) -> str:
    interests = _safe_str_list(persona.get("interests") if isinstance(persona, dict) else [])
    topic_low = (topic or "").strip().lower()
    corpus = "\n".join(texts).lower()

    if topic_low in {"", "about"}:
        if interests:
            return "You seem to enjoy " + _join_human(interests[:4]) + "."
        return "I don't know much about you yet."

    if any(x in topic_low for x in ("sport", "playing", "play")):
        sports = _extract_sports(interests + texts)
        if sports:
            return "Yes, the sport I have saved for you is " + _join_human(sports[:2]) + "."

    if any(x in topic_low for x in ("footballer", "player", "artist", "band", "actor", "team")):
        matches = _extract_named_preferences(texts, topic_low)
        if matches:
            return "The saved answer I have for that is " + _join_human(matches[:2]) + "."
        return f"I don't have your favorite {topic} saved yet." if topic else "I don't have that favorite saved yet."

    if topic_low and topic_low in corpus:
        return _yes_no_from_mentions(topic, texts)

    # Also try topic against persona interests.
    for item in interests:
        if topic_low and topic_low in item.lower():
            return f"Yes, you’ve mentioned you like {topic}."

    return f"I don't have a saved note/log about {topic} yet." if topic else "I don't know much about you yet."


def _heuristic_pending_save(user_text: str) -> PendingSave | None:
    low = user_text.lower()
    if any(x in low for x in ("today", "yesterday", "this week", "tonight")) and any(
        x in low for x in ("i ", "i'm", "im ", "i feel", "i did", "i went", "i was")
    ):
        return PendingSave(kind="log", text=user_text.strip(), reason="Time-bound personal update.")

    durable_markers = (
        "i like",
        "i love",
        "i prefer",
        "my goal",
        "i want to",
        "i'm trying to",
        "im trying to",
        "i learned",
        "i realised",
        "i realized",
        "i usually",
        "i always",
    )
    if any(m in low for m in durable_markers):
        return PendingSave(kind="note", text=user_text.strip(), reason="Looks like a durable preference/goal/learning.")
    return None


def _looks_like_planning_request(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low or len(low) < 12:
        return False
    starters = (
        "help me",
        "plan",
        "how should i",
        "i want to improve",
        "i want to get",
        "i want to build",
        "i want to start",
        "i want help",
        "can you help me",
    )
    areas = (
        "fit",
        "fitter",
        "slim",
        "lean",
        "weight",
        "fat loss",
        "lose weight",
        "health",
        "sleep",
        "study",
        "career",
        "work",
        "read",
        "routine",
        "habit",
        "stronger",
        "focus",
        "startup",
        "agentic",
        "ai",
        "product",
        "business",
        "company",
        "build",
        "launch",
        "app",
    )
    if not any(s in low for s in starters):
        return False
    if any(a in low for a in areas):
        return True

    topic = _extract_plan_topic(text)
    if not topic:
        return False
    topic_tokens = [t for t in topic.split() if t]
    growth_markers = (
        "better",
        "improve",
        "stronger",
        "build",
        "start",
        "learn",
        "grow",
        "become",
        "develop",
    )
    return len(topic_tokens) >= 2 and any(marker in topic for marker in growth_markers)


def _looks_like_question_input(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    if raw.endswith("?"):
        return True
    starters = ("who ", "what ", "when ", "where ", "why ", "how ", "do i ", "am i ", "did i ", "can you ")
    return any(raw.startswith(s) for s in starters)


def _is_specific_preference_question(text: str) -> bool:
    low = (text or "").strip().lower()
    markers = ("favorite", "favourite", "prefer", "best", "who is my", "what is my")
    domains = ("footballer", "player", "team", "band", "artist", "actor", "sport", "food", "music")
    return any(m in low for m in markers) and any(d in low for d in domains)


def _fallback_task_plan(user_text: str) -> PendingPlan | None:
    low = (user_text or "").strip().lower()
    if any(x in low for x in ("fit", "fitter", "health", "slim", "lean", "weight", "fat loss", "lose weight")):
        return PendingPlan(
            project="Fitness",
            summary="I can turn that into a small fitness project so you can track it instead of just talking about it.",
            goals=[
                "Do 3 movement sessions this week",
                "Log energy and recovery in one daily check-in",
                "Sleep before your target time on 4 nights this week",
            ],
            reason="User asked for help with fitness improvement.",
        )
    if "sleep" in low:
        return PendingPlan(
            project="Sleep",
            summary="I can make this into a small sleep-improvement project with a few starter goals.",
            goals=[
                "Set a target bedtime for this week",
                "Reduce screens 30 minutes before bed on 4 nights",
                "Track sleep quality in daily check-ins",
            ],
            reason="User asked for help improving sleep.",
        )
    if any(x in low for x in ("writer", "writing", "write", "reader", "reading", "read better")):
        return PendingPlan(
            project="Writing Practice",
            summary="I can turn that into a small writing-improvement project with simple weekly practice goals.",
            goals=[
                "Write for 15 minutes on 4 days this week",
                "Read one strong piece of writing and note what made it effective",
                "Revise one short paragraph each day for clarity and simplicity",
            ],
            reason="User asked for help improving writing.",
        )
    if any(x in low for x in ("startup", "agentic ai", "agentic", "business", "company")):
        return PendingPlan(
            project="Agentic AI Startup",
            summary="I can turn that into a startup-building project with a few focused first steps instead of leaving it as a broad idea.",
            goals=[
                "Define the user problem, target user, and why this should be agentic",
                "Choose one MVP workflow and write a simple end-to-end product scope",
                "List the first 5 customer or user conversations to validate the idea",
            ],
            reason="User asked for help building a startup.",
        )
    generic_topic = _extract_plan_topic(user_text)
    if generic_topic:
        project = _project_name_from_topic(generic_topic)
        return PendingPlan(
            project=project,
            summary=f"I can turn that into a small project for `{project}` with a few starter goals.",
            goals=[
                f"Define what success looks like for {generic_topic}",
                f"Break {generic_topic} into one MVP or first milestone",
                "Create 3 concrete actions to do this week",
            ],
            reason="User asked for help planning an ongoing goal.",
        )
    return None


def _extract_plan_topic(user_text: str) -> str:
    low = (user_text or "").strip().lower()
    starters = (
        "help me ",
        "can you help me ",
        "i want to build ",
        "i want to start ",
        "i want to improve ",
        "i want help with ",
        "plan ",
        "how should i ",
    )
    topic = low
    for starter in starters:
        if low.startswith(starter):
            topic = low[len(starter) :].strip()
            break
    for prefix in ("build ", "start ", "create ", "launch "):
        if topic.startswith(prefix):
            topic = topic[len(prefix) :].strip()
    return topic[:80].strip(" .!?")


def _project_name_from_topic(topic: str) -> str:
    words = [w for w in "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in topic).split() if w]
    if not words:
        return "New Project"
    kept: list[str] = []
    skip = {"a", "an", "the", "on", "for", "of", "to", "my"}
    for word in words:
        if word.lower() in skip:
            continue
        kept.append(word.capitalize())
        if len(kept) >= 4:
            break
    return " ".join(kept) if kept else "New Project"


def _extract_named_preferences(texts: list[str], topic_low: str) -> list[str]:
    matches: list[str] = []
    for text in texts:
        low = text.lower()
        if topic_low not in low and not any(x in low for x in ("favorite", "favourite", "love", "like")):
            continue
        cleaned = text.strip()
        if cleaned and cleaned not in matches:
            matches.append(cleaned)
    return matches


def _already_in_memory(memory: Memory, text: str, retrieved) -> bool:
    candidate = _normalize_memory_text(text)
    if not candidate:
        return True

    for note in memory.recent_notes(20):
        if _memory_text_match(candidate, note.text):
            return True
    for log in memory.recent_logs(20):
        if _memory_text_match(candidate, log.text):
            return True

    for item in retrieved or []:
        try:
            existing = item.item.text
        except AttributeError:
            existing = ""
        if _memory_text_match(candidate, existing):
            return True
    return False


def _memory_text_match(candidate: str, existing: str) -> bool:
    other = _normalize_memory_text(existing)
    if not other:
        return False
    if candidate == other:
        return True
    if candidate in other or other in candidate:
        shorter = min(len(candidate), len(other))
        if shorter >= 18:
            return True
    candidate_tokens = set(candidate.split())
    other_tokens = set(other.split())
    if not candidate_tokens or not other_tokens:
        return False
    overlap = len(candidate_tokens & other_tokens) / max(1, min(len(candidate_tokens), len(other_tokens)))
    return overlap >= 0.8 and min(len(candidate_tokens), len(other_tokens)) >= 4


def _normalize_memory_text(text: str) -> str:
    chars: list[str] = []
    last_space = False
    for ch in (text or "").strip().lower():
        if ch.isalnum():
            chars.append(ch)
            last_space = False
        elif not last_space:
            chars.append(" ")
            last_space = True
    return "".join(chars).strip()


def _is_noise_memory(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    if len(low) < 12:
        return True
    if low in {"hi", "hello", "hey", "clear", "help"}:
        return True
    if low in {"what do you know about me", "what do you remember about me"}:
        return True
    return False


def _is_smalltalk_input(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    # Common greetings/smalltalk that should not trigger memory recall.
    small = {
        "hi",
        "hello",
        "hey",
        "hey there",
        "yo",
        "sup",
        "what's up",
        "whats up",
        "hey what's up",
        "hey, what's up",
        "hey whats up",
        "good morning",
        "good afternoon",
        "good evening",
    }
    if low in small:
        return True
    if low.startswith("hey") and len(low) <= 24:
        return True
    return False


def _is_activity_request(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if "recommend" in low and "activit" in low:
        return True
    if low.startswith("activities") or low.startswith("activity"):
        return True
    if "what should i do" in low:
        return True
    return False


def _parse_checkin_fields(text: str) -> dict[str, str]:
    s = (text or "").strip()
    if not s.lower().startswith("daily check-in:"):
        return {}
    after = s.split(":", 1)[1].strip()
    parts = [p.strip() for p in after.split(";") if p.strip()]
    out: dict[str, str] = {}
    for p in parts:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        key = k.strip().lower()
        val = v.strip()
        if key:
            out[key] = val
    return out


def _build_context_signals(memory: Memory, user_text: str, reminders) -> str:
    lines: list[str] = []

    today_logs = memory.logs_today()
    recent_logs = memory.logs_in_last_days(7)
    recent_notes = memory.notes_in_last_days(7)
    matches = memory.search(user_text, limit=6)

    checkins = []
    for e in reversed(today_logs or recent_logs):
        fields = _parse_checkin_fields(e.text)
        if fields:
            checkins.append({"ts": e.ts, **fields})
        if len(checkins) >= 4:
            break

    if checkins:
        lines.append("Recent check-ins:")
        for item in checkins:
            parts = []
            for key in ("energy", "focus", "win"):
                value = str(item.get(key, "")).strip()
                if value and value.lower() != "n/a":
                    parts.append(f"{key}={value}")
            if parts:
                lines.append("- " + "; ".join(parts))

    if matches:
        lines.append("Deterministic matches:")
        for item in matches[:4]:
            text = str(item.get("text", "")).strip()
            if text and not _is_noise_memory(text):
                lines.append(f"- [{item.get('kind', '')}] {text}")

    if recent_logs:
        lines.append("Recent 7-day logs summary:")
        for e in recent_logs[-4:]:
            if not _is_noise_memory(e.text):
                lines.append(f"- {e.text}")

    if recent_notes:
        lines.append("Recent 7-day notes summary:")
        for n in recent_notes[-4:]:
            if not _is_noise_memory(n.text):
                lines.append(f"- {n.text}")

    if reminders:
        lines.append("Open reminders:")
        for r in reminders[:4]:
            text = str(getattr(r, "text", "")).strip()
            if text:
                lines.append(f"- {text}")

    return "\n".join(lines) if lines else "- (none)"


def _is_recall_request(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    triggers = (
        "earlier",
        "previous",
        "before",
        "what did i say",
        "what did i write",
        "what did i mention",
        "where was my",
        "do you remember",
        "remind me what",
        "last time",
        "my check-in",
        "checkin",
    )
    return any(t in low for t in triggers)


def _wants_raw_memory(text: str) -> bool:
    low = (text or "").lower()
    return any(x in low for x in ("show", "quote", "exact", "verbatim", "line", "timestamp"))


def _summarize_memory_line(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return "I found an entry, but it was empty."
    # Remove common boilerplate to make the answer feel direct.
    prefix = "Daily check-in:"
    if s.lower().startswith(prefix.lower()):
        s = s[len(prefix) :].strip()
    # Keep it short.
    if len(s) > 180:
        s = s[:177].rstrip() + "..."
    return s


def _is_about_me_question(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low or not low.endswith("?"):
        return False
    if _is_smalltalk_input(low):
        return False
    # Identity/preferences/habits questions.
    if low.startswith(("do i ", "am i ", "have i ", "did i ")):
        return True
    if "what do you know about me" in low or "what do you remember about me" in low:
        return True
    return False


def _extract_about_me_topic(text: str) -> str | None:
    low = (text or "").strip().lower().strip("?!. ")
    if not low:
        return None
    if "what do you know about me" in low or "what do you remember about me" in low:
        return None
    for p in ("do i like ", "do i love ", "do i enjoy ", "do i ", "am i ", "have i ", "did i "):
        if low.startswith(p):
            low = low[len(p) :].strip()
            break

    # Tokenize and take the last meaningful token as the topic.
    tokens: list[str] = []
    cur: list[str] = []
    for ch in low:
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                tokens.append("".join(cur))
                cur = []
    if cur:
        tokens.append("".join(cur))
    tokens = [t for t in tokens if t and t not in {"a", "an", "the", "to", "in", "on", "at", "me", "my"}]
    if not tokens:
        return None
    return tokens[-1]


def _yes_no_from_mentions(topic: str, texts: list[str]) -> str:
    t = (topic or "").strip().lower()
    joined = "\n".join(texts).lower()
    if not t:
        # If we can't infer the topic, be conservative.
        return "I have some saved context, but I need a more specific question."
    if f"don't like {t}" in joined or f"do not like {t}" in joined or f"hate {t}" in joined:
        return f"No, you’ve mentioned you don’t like {topic}."
    if f"like {t}" in joined or f"love {t}" in joined or f"enjoy {t}" in joined:
        return f"Yes, you’ve mentioned you like {topic}."
    if t in joined:
        return f"I've seen {topic} mentioned, but not clearly as a like/dislike."
    return f"I don't have a saved note/log about {topic} yet."


def _safe_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _join_human(items: list[str]) -> str:
    clean = [x.strip() for x in items if x.strip()]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return f"{', '.join(clean[:-1])}, and {clean[-1]}"


def _extract_sports(items: list[str]) -> list[str]:
    corpus = " \n".join([str(x).lower() for x in items if str(x).strip()])
    sports = []
    for sport in ("badminton", "football", "cricket", "tennis", "running", "swimming", "basketball"):
        if sport in corpus:
            sports.append(sport)
    return sports


def _timeline_items(cfg: Config, days: int = 30) -> list[dict[str, str]]:
    logs = read_json(cfg.logs_path, default=[])
    notes = read_json(cfg.notes_path, default=[])
    reminders = read_json(cfg.reminders_path, default=[])
    convs = read_json(cfg.conversations_path, default=[])
    items: list[dict[str, str]] = []

    for item in logs if isinstance(logs, list) else []:
        if not isinstance(item, dict):
            continue
        if not _within_days(str(item.get("ts", "")), days):
            continue
        items.append({"ts": str(item.get("ts", "")), "kind": "log", "text": str(item.get("text", ""))})
    for item in notes if isinstance(notes, list) else []:
        if not isinstance(item, dict):
            continue
        if not _within_days(str(item.get("ts", "")), days):
            continue
        items.append({"ts": str(item.get("ts", "")), "kind": "note", "text": str(item.get("text", ""))})
    for item in reminders if isinstance(reminders, list) else []:
        if not isinstance(item, dict):
            continue
        if not _within_days(str(item.get("created_ts", "")), days):
            continue
        items.append({"ts": str(item.get("created_ts", "")), "kind": "reminder", "text": str(item.get("text", ""))})
    for item in convs if isinstance(convs, list) else []:
        if not isinstance(item, dict):
            continue
        if not _within_days(str(item.get("ts", "")), days):
            continue
        items.append({"ts": str(item.get("ts", "")), "kind": "chat", "text": str(item.get("user", ""))})

    return sorted(items, key=lambda x: x.get("ts", ""), reverse=True)


def _within_days(ts: str, days: int) -> bool:
    from app.utils.time import parse_iso_to_local_date, today_local_date

    d = parse_iso_to_local_date(ts)
    if d is None:
        return False
    return d.toordinal() >= today_local_date().toordinal() - max(0, int(days)) + 1


def _parse_repair_command(raw: str, verb: str) -> tuple[str, int, str] | None:
    rest = raw.split(verb, 1)[1].strip()
    text = ""
    if "::" in rest:
        left, text = [x.strip() for x in rest.split("::", 1)]
    else:
        left = rest
    parts = left.split()
    if len(parts) != 2:
        return None
    kind = parts[0].strip().lower()
    if kind not in {"note", "log"}:
        return None
    try:
        idx = int(parts[1])
    except ValueError:
        return None
    return (kind, idx, text)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _select_retrieved(results, min_score: float = 0.18, max_items: int = 6):
    """
    Retriever scores are cosine-like (higher is more similar). We filter low-signal items so
    the LLM doesn't "grab" unrelated context and answer generically.
    """
    out = []
    for r in results or []:
        try:
            score = float(getattr(r, "score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        if score < float(min_score):
            continue
        if _is_noise_memory(getattr(r.item, "text", "")):
            continue
        out.append(r)
        if len(out) >= int(max_items):
            break
    # If everything got filtered, keep the single best item (if any) so recall still works a bit.
    if not out and results:
        best = results[0]
        if not _is_noise_memory(getattr(best.item, "text", "")):
            out = [best]
    return out


def _looks_generic(answer: str) -> bool:
    low = (answer or "").strip().lower()
    if not low:
        return True
    generic_phrases = (
        "i don't have specific",
        "as an ai",
        "i can assist you",
        "how can i assist",
        "based on my current focus",
        "i don't seem to",
        "however, you enjoy",
    )
    return any(p in low for p in generic_phrases)


def _default_clarifier(user_text: str) -> str:
    # One targeted question, not a menu.
    return "Quick question: are you asking based on what I've saved about you, or are you asking generally?"
