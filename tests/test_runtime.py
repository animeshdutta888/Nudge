from __future__ import annotations

import os
import tempfile
import unittest

from app.config import Config
from runtime.service import NudgeRuntime


class RuntimeTest(unittest.TestCase):
    def test_note_and_recall_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NUDGE_DATA_DIR"] = tmp
            cfg = Config.load()
            runtime = NudgeRuntime(cfg)

            saved = runtime.run_sync("note: learned about FAISS indexing", source="test")
            recalled = runtime.run_sync("what did I learn about FAISS?", source="test")

            self.assertEqual(saved, "Saved note.")
            self.assertIn("FAISS", recalled)
            self.assertIn("[note]", recalled)


if __name__ == "__main__":
    unittest.main()
