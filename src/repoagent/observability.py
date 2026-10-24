from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext, suppress
from typing import Any

from repoagent.config import Settings


class TraceManager:
    """Optional Langfuse/OpenTelemetry tracing that never breaks the workflow."""

    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.langfuse_enabled
        self._client: Any | None = None
        if self.enabled:
            try:
                from langfuse import get_client

                self._client = get_client()
            except Exception:
                self.enabled = False

    def span(
        self,
        name: str,
        *,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
        as_type: str = "span",
    ) -> AbstractContextManager:
        if not self.enabled or self._client is None:
            return nullcontext()
        return self._client.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=input,
            metadata=metadata,
        )

    def flush(self) -> None:
        if self.enabled and self._client is not None:
            with suppress(Exception):
                self._client.flush()
