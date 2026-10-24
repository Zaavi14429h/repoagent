from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from threading import Lock
from uuid import UUID

from repoagent.models import RepoRunState, RunEvent


class StateStore:
    """Small SQLite event/state store suitable for local runs and demos."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    component TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_run_time
                ON events(run_id, timestamp);
                """
            )

    def save_run(self, state: RepoRunState) -> None:
        payload = state.model_dump_json()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(run_id, status, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    str(state.run_id),
                    state.status.value,
                    payload,
                    state.created_at.isoformat(),
                    state.updated_at.isoformat(),
                ),
            )

    def get_run(self, run_id: UUID | str) -> RepoRunState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return RepoRunState.model_validate_json(row["state_json"]) if row else None

    def list_runs(self, limit: int = 100) -> list[RepoRunState]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state_json FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [RepoRunState.model_validate_json(row["state_json"]) for row in rows]

    def append_event(self, event: RunEvent) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events(
                    event_id, run_id, timestamp, event_type, component, event_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.event_id),
                    str(event.run_id),
                    event.timestamp.isoformat(),
                    event.event_type,
                    event.component,
                    event.model_dump_json(),
                ),
            )

    def get_events(self, run_id: UUID | str) -> list[RunEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_json FROM events
                WHERE run_id = ? ORDER BY timestamp, rowid
                """,
                (str(run_id),),
            ).fetchall()
        return [RunEvent.model_validate_json(row["event_json"]) for row in rows]

    def save_events(self, events: Iterable[RunEvent]) -> None:
        for event in events:
            self.append_event(event)
