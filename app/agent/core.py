from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Config
from runtime.service import NudgeRuntime


class NudgeAgent:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._runtime = NudgeRuntime(cfg)

    def run_agent(self, user_text: str) -> str:
        return self._runtime.run_sync(user_text, source="cli")


def run_agent(user_text: str, cfg: Config | None = None, source: str = "cli") -> str:
    runtime = _get_runtime(cfg or Config.load())
    return runtime.run_sync(user_text, source=source)


def pending_action(action: str, cfg: Config | None = None) -> str:
    runtime = _get_runtime(cfg or Config.load())
    if action not in {"approve", "skip"}:
        return runtime.run_sync(action, source="dashboard")
    return asyncio.run(runtime.pending_action(action))


def pending_action_result(action: str, cfg: Config | None = None):
    runtime = _get_runtime(cfg or Config.load())
    return asyncio.run(runtime.pending_action_response(action, source="dashboard", persist=True))


def pending_save_action(action: str, cfg: Config | None = None) -> str:
    return pending_action(action, cfg=cfg)


_RUNTIMES: dict[str, NudgeRuntime] = {}


def _get_runtime(cfg: Config) -> NudgeRuntime:
    key = str(Path(cfg.data_dir).resolve())
    runtime = _RUNTIMES.get(key)
    if runtime is None:
        runtime = NudgeRuntime(cfg)
        _RUNTIMES[key] = runtime
    return runtime
