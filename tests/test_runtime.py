from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app.config import Config
from app.agent.router import RoutedIntent
from app.services.storage import read_json
from app.tools.projects import add_project
from app.tools.reminders import list_reminders
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
            self.assertEqual(saved, "Saved reminder.")
            self.assertEqual(len(reminders), 1)
            self.assertEqual(reminders[0].text, "read a book")

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


if __name__ == "__main__":
    unittest.main()
