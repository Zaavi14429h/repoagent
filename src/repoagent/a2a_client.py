from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

import httpx

from repoagent.models import ValidationReport, ValidationRequest


class ValidationClient(ABC):
    @abstractmethod
    async def validate(self, request: ValidationRequest) -> ValidationReport: ...


class A2AValidationClient(ValidationClient):
    def __init__(self, base_url: str, timeout_seconds: int = 600) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def validate(self, request: ValidationRequest) -> ValidationReport:
        message_id = str(uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "messageId": message_id,
                    "contextId": str(request.run_id),
                    "parts": [{"data": request.model_dump(mode="json")}],
                }
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/a2a",
                headers={"A2A-Version": "1.0"},
                json=payload,
            )
        response.raise_for_status()
        envelope = response.json()
        if "error" in envelope:
            raise RuntimeError(f"A2A error: {envelope['error']}")
        return self._extract_report(envelope["result"])

    @staticmethod
    def _extract_report(task: dict[str, Any]) -> ValidationReport:
        state = task.get("status", {}).get("state")
        if state not in {"completed", "TASK_STATE_COMPLETED"}:
            message = task.get("status", {}).get("message", {})
            raise RuntimeError(f"ADK validation task ended in {state}: {message}")
        for artifact in task.get("artifacts", []):
            if artifact.get("name") != "validation-report":
                continue
            for part in artifact.get("parts", []):
                if "data" in part:
                    return ValidationReport.model_validate(part["data"])
        raise RuntimeError("A2A task completed without a validation-report artifact")
