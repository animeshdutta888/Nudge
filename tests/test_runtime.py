from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Config
from app.main import main as cli_main
from app.agent.router import RoutedIntent
from app.agent.state import PendingPlan, PendingSave, PendingToolAction, StateStore
from app.services.storage import read_json
from app.tools.daily_plan import save_daily_plan
from app.tools.dashboard_data import build_dashboard_payload
from app.tools.projects import add_project
from app.tools.reminders import add_reminder, list_reminders, next_due_reminder, snooze_reminder
from app.utils.time import now_local_iso
from runtime.service import NudgeRuntime


class RuntimeTest(unittest.TestCase):
    def test_note_and_recall_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            saved = runtime.run_sync("note: learned about FAISS indexing", source="test")
            recalled = runtime.run_sync("what did I learn about FAISS?", source="test")

            self.assertEqual(saved, "Saved note.")
            self.assertIn("FAISS", recalled)
            self.assertIn("[note]", recalled)

    def test_natural_language_reminder_routes_to_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="add_reminder", text="read a book", when="tomorrow 09:00")):
                saved = runtime.run_sync("Remind me tomorrow at 9 to read a book", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "read a book")
            self.assertIsNotNone(reminders[0].due_ts)

    def test_natural_language_reminder_route_accepts_resolved_iso_when(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            due_ts = (datetime.fromisoformat(now_local_iso()) + timedelta(minutes=20)).isoformat(timespec="seconds")

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(
                    intent="add_reminder",
                    text="drink water",
                    when=due_ts,
                ),
            ):
                saved = runtime.run_sync("please create a reminder for drinking water", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "drink water")
            self.assertEqual(reminders[0].due_ts, due_ts)

    def test_natural_language_reminder_falls_back_when_router_does_not_split_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="add_reminder", text="remind me in 20 seconds to drink water"),
            ):
                saved = runtime.run_sync("remind me in 20 seconds to drink water", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "drink water")
            self.assertIsNotNone(reminders[0].due_ts)

    def test_remind_command_parses_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            saved = runtime.run_sync("remind: in 30 seconds drink water", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "drink water")
            self.assertIsNotNone(reminders[0].due_ts)

    def test_natural_language_reminder_resolves_relative_seconds_from_raw_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="add_reminder", text=""),
            ):
                saved = runtime.run_sync("remind me in 5 seconds to drink water", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "drink water")
            self.assertIsNotNone(reminders[0].due_ts)

    def test_natural_language_reminder_handles_suffix_style_relative_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="add_reminder", text=""),
            ):
                saved = runtime.run_sync("remind me to drink water in 5 seconds", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "drink water")
            self.assertIsNotNone(reminders[0].due_ts)

    def test_routed_reminder_rejects_past_due_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="add_reminder", text="drink water", when="2023-11-29T14:38:15+05:30"),
            ):
                saved = runtime.run_sync("please create a reminder for drinking water", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("already in the past", saved)
            self.assertEqual(reminders, [])

    def test_routed_reminder_ignores_stale_hint_when_raw_text_has_valid_future_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="add_reminder", text="drink water", when="2023-11-29T14:38:15+05:30"),
            ):
                saved = runtime.run_sync("remind me to drink water in 5 seconds", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "drink water")
            self.assertIsNotNone(reminders[0].due_ts)

    def test_natural_language_project_question_routes_to_projects_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            add_project(cfg.projects_path, "chronicle")

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="list_projects")):
                reply = runtime.run_sync("Do I have any projects?", source="test")

            self.assertIn("chronicle", reply.lower())
            self.assertIn("active", reply.lower())

    def test_named_project_query_returns_project_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            add_project(cfg.projects_path, "chronicle")
            runtime.run_sync("goal add chronicle :: Open-source AI orchestration layer for coding agents", source="test")

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="show_project", project="chronicle")):
                reply = runtime.run_sync("what is chronicle?", source="test")

            self.assertIn("Project: chronicle", reply)
            self.assertIn("Status: active", reply)
            self.assertIn("Open-source AI orchestration layer for coding agents", reply)

    def test_named_project_query_has_runtime_fallback_when_router_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            add_project(cfg.projects_path, "chronicle")

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="none")):
                reply = runtime.run_sync("tell me about chronicle", source="test")

            self.assertIn("Project: chronicle", reply)
            self.assertIn("Status: active", reply)

    def test_priority_question_returns_focus_and_open_goals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            runtime.run_sync("log: Daily check-in: energy=8; focus=Finish chronicle SDK routing; win=Shipped dashboard fixes", source="test")
            add_project(cfg.projects_path, "chronicle")
            runtime.run_sync("goal add chronicle :: Finish SDK routing", source="test")

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="show_priorities")):
                reply = runtime.run_sync("What should I focus on next?", source="test")

            self.assertIn("Current focus signals:", reply)
            self.assertIn("Active projects:", reply)
            self.assertIn("Next open goals:", reply)
            self.assertIn("chronicle", reply)

    def test_persona_command_returns_human_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            runtime.run_sync("log: Daily check-in: energy=7; focus=Prepare launch checklist; win=Finished architecture doc", source="test")

            reply = runtime.run_sync("persona", source="test")

            self.assertIn("Current focus:", reply)
            self.assertNotIn("{'updated_at'", reply)

    def test_personal_statement_can_autosave_without_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="save_candidate", text="My favourite cricketer is MS Dhoni", reason="Durable preference."),
            ):
                reply = runtime.run_sync("My favourite cricketer is MS Dhoni", source="test")

            notes = read_json(cfg.notes_path, default=[])
            self.assertEqual(reply, "Saved note.")
            self.assertTrue(isinstance(notes, list) and notes)
            self.assertIn("MS Dhoni", str(notes[0].get("text", "")))

    def test_notes_tool_creates_note_from_routed_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="notes_create", text="Capture launch checklist")):
                reply = runtime.run_sync("Create a note to capture launch checklist", source="test")

            notes = read_json(cfg.notes_path, default=[])
            self.assertEqual(reply, "Saved note.")
            self.assertTrue(isinstance(notes, list) and notes)
            self.assertIn("Capture launch checklist", str(notes[0].get("text", "")))

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="notes_list")):
                response = asyncio.run(runtime.run("list notes", source="test"))

            self.assertIsInstance(response.tool_result, dict)
            self.assertEqual(response.tool_result.get("kind"), "notes_list")

    def test_filesystem_tool_reads_file_inside_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            target = os.path.join(tmp, "README.txt")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("hello from workspace\nsecond line\n")
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="filesystem_read", path="README.txt")):
                reply = runtime.run_sync("Open README.txt", source="test")

            self.assertIn("File: `./README.txt`", reply)
            self.assertIn("hello from workspace", reply)

    def test_filesystem_tool_extracts_readme_from_natural_language_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            target = os.path.join(tmp, "README.md")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("# Demo readme\nlocal content\n")
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="filesystem_read", text="show content of README")):
                reply = runtime.run_sync("show content of README", source="test")

            self.assertIn("File: `./README.md`", reply)
            self.assertIn("Demo readme", reply)

    def test_filesystem_follow_up_read_uses_last_listed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            chronicle_dir = os.path.join(tmp, "chronicle")
            os.makedirs(chronicle_dir, exist_ok=True)
            target = os.path.join(chronicle_dir, "render.yaml")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("services:\n  - type: web\n")
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="filesystem_list", path="chronicle")):
                runtime.run_sync("list files in chronicle", source="test")
            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="filesystem_read", text="open render.yaml")):
                reply = runtime.run_sync("open render.yaml", source="test")

            self.assertIn("File: `./chronicle/render.yaml`", reply)
            self.assertIn("type: web", reply)

    def test_shell_tool_requires_approval_and_runs_after_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="shell_run", command="pwd")):
                reply = runtime.run_sync("Run pwd", source="test")

            approved = runtime.run_sync("approve", source="test")
            self.assertIn("Use `approve` to continue", reply)
            self.assertIn("Shell command: `pwd`", approved)
            self.assertIn(tmp, approved)

    def test_run_prefix_routes_to_shell_fast_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            reply = runtime.run_sync("run pwd", source="test")
            approved = runtime.run_sync("approve", source="test")

            self.assertIn("Use `approve` to continue", reply)
            self.assertIn("Shell command: `pwd`", approved)
            self.assertIn(tmp, approved)

    def test_persisted_tool_card_survives_dashboard_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            runtime.run_sync("run pwd", source="dashboard")
            response = asyncio.run(runtime.pending_action_response("approve", source="dashboard", persist=True))
            conversations = read_json(cfg.conversations_path, default=[])

            self.assertIsInstance(response.tool_result, dict)
            self.assertEqual(response.tool_result.get("kind"), "shell_run")
            self.assertTrue(isinstance(conversations, list) and conversations)
            self.assertEqual(conversations[-1].get("tool_result", {}).get("kind"), "shell_run")

    def test_dashboard_payload_includes_due_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()

            reminder = add_reminder(cfg.reminders_path, "Review chronicle launch", now_local_iso())
            payload = build_dashboard_payload(cfg.data_dir)

            self.assertEqual(payload.get("due_reminder", {}).get("id"), reminder.id)
            self.assertEqual(payload.get("due_reminder", {}).get("text"), "Review chronicle launch")

    def test_snoozed_reminder_stops_being_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()

            reminder = add_reminder(cfg.reminders_path, "Read a chapter", now_local_iso())
            self.assertIsNotNone(next_due_reminder(cfg.reminders_path))

            ok = snooze_reminder(cfg.reminders_path, reminder.id, minutes=10)

            self.assertTrue(ok)
            self.assertIsNone(next_due_reminder(cfg.reminders_path))

    def test_snooze_defaults_to_one_minute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()

            reminder = add_reminder(cfg.reminders_path, "Stand up", now_local_iso())
            before = read_json(cfg.reminders_path, default=[])
            ok = snooze_reminder(cfg.reminders_path, reminder.id)
            after = read_json(cfg.reminders_path, default=[])

            self.assertTrue(ok)
            self.assertTrue(isinstance(before, list) and isinstance(after, list))
            self.assertNotEqual(before[0].get("due_ts"), after[0].get("due_ts"))

    def test_start_day_creates_pending_daily_plan_with_carry_forward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            save_daily_plan(cfg.daily_plans_path, ["Finish dashboard card", "Record demo walkthrough"], summary="Yesterday's plan")
            runtime = NudgeRuntime(cfg)
            add_project(cfg.projects_path, "chronicle")
            runtime.run_sync("goal add chronicle :: Tighten planner carry-forward", source="test")

            reply = runtime.run_sync("start my day", source="test")
            state = read_json(cfg.state_path, default={})
            pending_plan = state.get("pending_plan") if isinstance(state, dict) else {}

            self.assertIn("Good morning. Here is what matters today:", reply)
            self.assertIn("Carry forward", reply)
            self.assertIsInstance(pending_plan, dict)
            self.assertEqual(pending_plan.get("plan_kind"), "daily_plan")
            self.assertLessEqual(len(pending_plan.get("priorities", [])), 3)
            self.assertIn("Finish dashboard card", pending_plan.get("carry_forward", []))

    def test_approve_start_day_persists_daily_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            add_project(cfg.projects_path, "chronicle")
            runtime.run_sync("goal add chronicle :: Ship Start My Day demo", source="test")

            runtime.run_sync("start my day", source="test")
            approved = runtime.run_sync("approve", source="test")
            plans = read_json(cfg.daily_plans_path, default=[])

            self.assertIn("Saved today's plan.", approved)
            self.assertTrue(isinstance(plans, list) and plans)
            self.assertTrue(plans[-1].get("priorities"))
            self.assertEqual(plans[-1].get("source"), "start_day")

    def test_dashboard_payload_includes_latest_daily_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            save_daily_plan(cfg.daily_plans_path, ["Ship dashboard card"], summary="Highest leverage task: Ship dashboard card", source="start_day")

            payload = build_dashboard_payload(cfg.data_dir)

            self.assertEqual(payload.get("daily_plan", {}).get("summary"), "Highest leverage task: Ship dashboard card")
            self.assertEqual(payload.get("daily_plan", {}).get("priorities"), ["Ship dashboard card"])

    def test_edit_today_plan_updates_saved_daily_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            save_daily_plan(cfg.daily_plans_path, ["Finish SDK routing"], summary="Today's focus", source="start_day")
            reply = runtime.run_sync("edit today's plan to add improve nudge voice integration", source="test")
            payload = build_dashboard_payload(cfg.data_dir)

            self.assertIn("Updated today's plan.", reply)
            self.assertIn("improve nudge voice integration", reply)
            self.assertIn("improve nudge voice integration", payload.get("daily_plan", {}).get("priorities", []))

    def test_edit_today_plan_accepts_include_phrasing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            save_daily_plan(cfg.daily_plans_path, ["Finish SDK routing"], summary="Today's focus", source="start_day")
            reply = runtime.run_sync("Edit today's plan to include improving nudge", source="test")
            payload = build_dashboard_payload(cfg.data_dir)

            self.assertIn("Updated today's plan.", reply)
            self.assertIn("improving nudge", payload.get("daily_plan", {}).get("priorities", []))

    def test_routed_daily_plan_update_adds_priority_without_regex_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            save_daily_plan(cfg.daily_plans_path, ["Finish SDK routing"], summary="Today's focus", source="start_day")
            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="update_daily_plan", operation="add", target="today_plan", text="Improve Nudge voice integration"),
            ):
                reply = runtime.run_sync("make sure today's plan includes improving nudge voice integration", source="test")

            payload = build_dashboard_payload(cfg.data_dir)
            self.assertIn("Updated today's plan.", reply)
            self.assertIn("Improve Nudge voice integration", payload.get("daily_plan", {}).get("priorities", []))

    def test_routed_show_daily_plan_returns_saved_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            save_daily_plan(cfg.daily_plans_path, ["Finish SDK routing"], summary="Today's focus", source="start_day")
            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(intent="show_daily_plan"),
            ):
                reply = runtime.run_sync("what is on today's plan?", source="test")

            self.assertIn("Today's plan:", reply)
            self.assertIn("Finish SDK routing", reply)

    def test_edit_today_plan_updates_pending_daily_plan_before_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            add_project(cfg.projects_path, "chronicle")
            runtime.run_sync("goal add chronicle :: Ship agent orchestration docs", source="test")

            runtime.run_sync("start my day", source="test")
            reply = runtime.run_sync("edit today's plan to add improve nudge voice integration", source="test")
            state = read_json(cfg.state_path, default={})
            pending_plan = state.get("pending_plan") if isinstance(state, dict) else {}

            self.assertIn("Updated today's draft plan.", reply)
            self.assertIn("improve nudge voice integration", pending_plan.get("priorities", []))

    def test_each_run_persists_a_visible_runtime_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            runtime.run_sync("hello", source="test")
            payload = build_dashboard_payload(cfg.data_dir)
            latest_trace = payload.get("runtime", {}).get("latest_trace", {})

            self.assertEqual(latest_trace.get("agent"), "Runtime")
            self.assertEqual(latest_trace.get("step"), "respond")

    def test_cli_start_day_alias_runs_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            add_project(cfg.projects_path, "chronicle")
            original_argv = list(sys.argv)
            stdout = io.StringIO()
            try:
                sys.argv = ["python", "start-day"]
                with patch("sys.stdout", stdout):
                    code = cli_main()
            finally:
                sys.argv = original_argv

            self.assertEqual(code, 0)
            self.assertIn("Good morning. Here is what matters today:", stdout.getvalue())

    def test_close_day_starts_reflection_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            reply = runtime.run_sync("close my day", source="test")
            state = read_json(cfg.state_path, default={})

            self.assertIn("Close My Day check-in:", reply)
            self.assertEqual(state.get("close_day_session", {}).get("status"), "awaiting_reflection")

    def test_start_day_bypasses_stale_close_day_reflection_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            runtime.run_sync("close my day", source="test")
            reply = runtime.run_sync("start my day", source="test")

            self.assertIn("Good morning. Here is what matters today:", reply)
            self.assertNotIn("I couldn't parse that reflection yet", reply)

    def test_close_day_follow_up_creates_pending_review_for_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            save_daily_plan(cfg.daily_plans_path, ["Ship voice MVP"], summary="Today's focus", source="start_day")

            runtime.run_sync("close my day", source="test")
            reply = runtime.run_sync(
                "finished: Ship voice MVP; stuck: reminder edge cases; carry: write demo script",
                source="test",
            )
            state = read_json(cfg.state_path, default={})
            pending_plan = state.get("pending_plan", {})

            self.assertIn("close-day reflection draft", reply.lower())
            self.assertEqual(pending_plan.get("plan_kind"), "close_day_review")
            self.assertEqual(pending_plan.get("wins"), ["Ship voice MVP"])
            self.assertEqual(pending_plan.get("carry_forward"), ["write demo script"])

    def test_approve_close_day_persists_reflection_and_closes_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)
            save_daily_plan(cfg.daily_plans_path, ["Ship voice MVP"], summary="Today's focus", source="start_day")

            runtime.run_sync("close my day", source="test")
            runtime.run_sync(
                "finished: Ship voice MVP; stuck: reminder edge cases; carry: write demo script",
                source="test",
            )
            approved = runtime.run_sync("approve", source="test")
            plans = read_json(cfg.daily_plans_path, default=[])
            logs = read_json(cfg.logs_path, default=[])

            self.assertIn("Saved today's reflection", approved)
            self.assertEqual(plans[-1].get("status"), "closed")
            self.assertEqual(plans[-1].get("carry_forward"), ["write demo script"])
            self.assertEqual(plans[-1].get("wins"), ["Ship voice MVP"])
            self.assertTrue(any("Daily reflection:" in str(item.get("text", "")) for item in logs if isinstance(item, dict)))

    def test_close_day_accepts_common_finished_typo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            runtime.run_sync("close my day", source="test")
            reply = runtime.run_sync("finshed: nudge voice integration", source="test")
            state = read_json(cfg.state_path, default={})

            self.assertIn("close-day reflection draft", reply.lower())
            self.assertEqual(state.get("pending_plan", {}).get("wins"), ["nudge voice integration"])

    def test_close_day_accepts_looser_natural_language_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            runtime.run_sync("close my day", source="test")
            reply = runtime.run_sync(
                "finished nudge voice integration; blocked reminder parsing; tomorrow write demo script",
                source="test",
            )
            state = read_json(cfg.state_path, default={})
            pending = state.get("pending_plan", {})

            self.assertIn("close-day reflection draft", reply.lower())
            self.assertEqual(pending.get("wins"), ["nudge voice integration"])
            self.assertEqual(pending.get("blockers"), ["reminder parsing"])
            self.assertEqual(pending.get("carry_forward"), ["write demo script"])

    def test_close_day_accepts_multiline_reflection_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            runtime.run_sync("close my day", source="test")
            reply = runtime.run_sync(
                "finished: shipped dashboard refresh\nstuck: reminder parsing edge case\ncarry: write demo script",
                source="test",
            )
            pending = read_json(cfg.state_path, default={}).get("pending_plan", {})

            self.assertIn("close-day reflection draft", reply.lower())
            self.assertEqual(pending.get("wins"), ["shipped dashboard refresh"])
            self.assertEqual(pending.get("blockers"), ["reminder parsing edge case"])
            self.assertEqual(pending.get("carry_forward"), ["write demo script"])

    def test_close_day_accepts_dash_separated_reflection_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            runtime.run_sync("close my day", source="test")
            reply = runtime.run_sync(
                "finished - shipped dashboard refresh; stuck - reminder parsing edge case; carry forward - write demo script",
                source="test",
            )
            pending = read_json(cfg.state_path, default={}).get("pending_plan", {})

            self.assertIn("close-day reflection draft", reply.lower())
            self.assertEqual(pending.get("wins"), ["shipped dashboard refresh"])
            self.assertEqual(pending.get("blockers"), ["reminder parsing edge case"])
            self.assertEqual(pending.get("carry_forward"), ["write demo script"])

    def test_routed_close_day_intent_starts_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch("runtime.service.IntentRouter.route", return_value=RoutedIntent(intent="close_day")):
                reply = runtime.run_sync("wrap up my day", source="test")

            self.assertIn("Close My Day check-in:", reply)

    def test_routed_close_day_reflection_works_without_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            os.environ["NUDGE_WORKSPACE_ROOT"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(
                    intent="close_day_reflection",
                    text="finished nudge voice integration; blocked reminder parsing; tomorrow write demo script",
                ),
            ):
                reply = runtime.run_sync("wrapped up a lot today", source="test")

            state = read_json(cfg.state_path, default={})
            pending = state.get("pending_plan", {})
            self.assertIn("close-day reflection draft", reply.lower())
            self.assertEqual(pending.get("plan_kind"), "close_day_review")
            self.assertEqual(pending.get("carry_forward"), ["write demo script"])

    def test_pending_save_uses_normalized_action_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            store = StateStore(state_path)
            store.set_pending_save(PendingSave(kind="note", text="remember this", reason="Useful context."))
            state = read_json(state_path, default={})
            pending = state.get("pending_save", {})

            self.assertEqual(pending.get("type"), "save_memory")
            self.assertEqual(pending.get("status"), "pending")
            self.assertTrue(pending.get("requires_approval"))
            self.assertEqual(pending.get("payload", {}).get("text"), "remember this")
            self.assertIn("action_id", pending)
            self.assertIn("created_at", pending)
            self.assertEqual(pending.get("version"), 1)

    def test_pending_plan_uses_normalized_action_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            store = StateStore(state_path)
            store.set_pending_plan(
                PendingPlan(
                    project="daily-plan",
                    summary="Highest leverage task: Ship voice MVP",
                    goals=["Ship voice MVP"],
                    priorities=["Ship voice MVP"],
                    plan_kind="daily_plan",
                    reason="Start day flow.",
                )
            )
            state = read_json(state_path, default={})
            pending = state.get("pending_plan", {})

            self.assertEqual(pending.get("type"), "create_daily_plan")
            self.assertTrue(pending.get("requires_approval"))
            self.assertEqual(pending.get("payload", {}).get("plan_kind"), "daily_plan")
            self.assertEqual(pending.get("payload", {}).get("priorities"), ["Ship voice MVP"])
            self.assertEqual(pending.get("status"), "pending")

    def test_pending_tool_action_uses_normalized_action_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            store = StateStore(state_path)
            store.set_pending_tool_action(
                PendingToolAction(
                    tool="shell",
                    action="run",
                    payload={"command": "pwd"},
                    reason="Explicit shell request.",
                )
            )
            state = read_json(state_path, default={})
            pending = state.get("pending_tool_action", {})

            self.assertEqual(pending.get("type"), "shell_run")
            self.assertEqual(pending.get("payload", {}).get("tool"), "shell")
            self.assertEqual(pending.get("payload", {}).get("action"), "run")
            self.assertEqual(pending.get("payload", {}).get("payload", {}).get("command"), "pwd")


if __name__ == "__main__":
    unittest.main()
