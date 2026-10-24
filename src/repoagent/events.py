from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from repoagent.models import RepoRunState, RunEvent
from repoagent.store import StateStore

logger = logging.getLogger("repoagent")

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
]


def redact(value: Any) -> Any:
    if isinstance(value, str):
        result = value
        for pattern in _SECRET_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class EventBus:
    """Callback target for durable events and live dashboard subscribers."""

    def __init__(self, store: StateStore) -> None:
        self.store = store
        self._subscribers: list[Callable[[RunEvent], None]] = []

    def subscribe(self, callback: Callable[[RunEvent], None]) -> None:
        self._subscribers.append(callback)

    def emit(
        self,
        run_id: UUID,
        event_type: str,
        component: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            event_type=event_type,
            component=component,
            message=message,
            data=redact(data or {}),
            error=redact(error) if error else None,
        )
        self.store.append_event(event)
        logger.info("%s %s: %s", event_type, component, message)
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:
                logger.exception("Event subscriber failed")
        return event

    def state_updated(self, state: RepoRunState, message: str) -> None:
        self.store.save_run(state)
        self.emit(
            state.run_id,
            "state_updated",
            "orchestrator",
            message,
            data={"status": state.status.value},
        )


class AgentCallback:
    """Framework-neutral callback adapter used by CrewAI and ADK wrappers."""

    def __init__(self, events: EventBus, run_id: UUID, component: str) -> None:
        self.events = events
        self.run_id = run_id
        self.component = component

    def started(self, data: dict[str, Any] | None = None) -> None:
        self.events.emit(
            self.run_id,
            "agent_started",
            self.component,
            f"{self.component} started",
            data=data,
        )

    def completed(self, data: dict[str, Any] | None = None) -> None:
        self.events.emit(
            self.run_id,
            "agent_completed",
            self.component,
            f"{self.component} completed",
            data=data,
        )

    def progress(self, step_type: str, tool: str | None = None) -> None:
        # Do not persist hidden reasoning or raw prompts. The event records only
        # execution metadata needed for monitoring and auditability.
        self.events.emit(
            self.run_id,
            "agent_step",
            self.component,
            f"{self.component} produced an intermediate step",
            data={"step_type": step_type, "tool": tool},
        )

    def failed(self, error: Exception) -> None:
        self.events.emit(
            self.run_id,
            "agent_failed",
            self.component,
            f"{self.component} failed",
            error=str(error),
        )
