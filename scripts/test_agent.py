from __future__ import annotations

import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.core import NudgeAgent  # noqa: E402
from app.config import Config  # noqa: E402


def main() -> int:
    agent = NudgeAgent(Config.load())
    inputs = [
        "note: learned about FAISS indexing and IndexIDMap2",
        "log: skipped gym today",
        "what did I learn about FAISS?",
        "insights",
    ]
    for t in inputs:
        print(f"\n> {t}")
        print(agent.run_agent(t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
