from __future__ import annotations

from datetime import datetime


def info(msg: str) -> None:
    print(f"[{_ts()}] INFO  {msg}")


def warn(msg: str) -> None:
    print(f"[{_ts()}] WARN  {msg}")


def error(msg: str) -> None:
    print(f"[{_ts()}] ERROR {msg}")


def _ts() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
