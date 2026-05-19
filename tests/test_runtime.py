from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from app.config import Config
from app.agent.router import RoutedIntent
from app.services.storage import read_json
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

            with patch(
                "runtime.service.IntentRouter.route",
                return_value=RoutedIntent(
                    intent="add_reminder",
                    text="drink water",
                    when="2026-05-21T01:48:30+05:30",
                ),
            ):
                saved = runtime.run_sync("please create a reminder for drinking water", source="test")

            reminders = list_reminders(cfg.reminders_path, upcoming_days=30)
            self.assertIn("Saved reminder for", saved)
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "drink water")
            self.assertEqual(reminders[0].due_ts, "2026-05-21T01:48:30+05:30")

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


if __name__ == "__main__":
    unittest.main()
