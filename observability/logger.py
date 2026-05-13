from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from schemas.shared import SharedState, TraceEvent
from app.utils.time import now_local_iso


class ExecutionLogger:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_traces (
                    run_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    step TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def add(
        self,
        state: SharedState,
        *,
        agent: str,
        step: str,
        status: str,
        message: str,
        duration_ms: int = 0,
        retry_count: int = 0,
        payload: Optional[dict[str, object]] = None,
    ) -> None:
        event = TraceEvent(
            agent=agent,
            step=step,
            status=status,  # type: ignore[arg-type]
            message=message,
            duration_ms=max(0, int(duration_ms)),
            retry_count=max(0, int(retry_count)),
            payload=payload or {},
        )
        state.traces.append(event)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO execution_traces
                (run_id, ts, agent, step, status, duration_ms, retry_count, message, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.run_id,
                    now_local_iso(),
                    event.agent,
                    event.step,
                    event.status,
                    event.duration_ms,
                    event.retry_count,
                    event.message,
                    json.dumps(event.payload, ensure_ascii=True),
                ),
            )
            conn.commit()

    def timed(self, state: SharedState, *, agent: str, step: str):
        start = time.perf_counter()

        def done(status: str, message: str, retry_count: int = 0, payload: Optional[dict[str, object]] = None) -> None:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self.add(
                state,
                agent=agent,
                step=step,
                status=status,
                message=message,
                duration_ms=duration_ms,
                retry_count=retry_count,
                payload=payload,
            )

        return done
