from uuid import uuid4

from a2a.types import AgentCard, Task
from fastapi.testclient import TestClient
from google.protobuf.json_format import ParseDict

from repoagent.adk_validation import service
from repoagent.models import ValidationReport


class FakeADKRunner:
    async def run(self, request):
        return ValidationReport(
            passed=True,
            attempt=request.attempt,
            summary="All checks passed",
        )


def test_a2a_message_send_returns_validation_artifact(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "ADKValidationRunner", lambda _settings: FakeADKRunner())
    run_id = uuid4()
    message_id = str(uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "messageId": message_id,
                "contextId": str(run_id),
                "parts": [
                    {
                        "data": {
                            "run_id": str(run_id),
                            "repository_path": str(tmp_path),
                            "commands": [["python", "-m", "pytest"]],
                            "attempt": 1,
                        }
                    }
                ],
            }
        },
    }
    response = TestClient(service.app).post("/a2a", headers={"A2A-Version": "1.0"}, json=payload)
    assert response.status_code == 200
    task = response.json()["result"]
    ParseDict(task, Task())
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["contextId"] == str(run_id)
    report = task["artifacts"][0]["parts"][0]["data"]
    assert report["passed"] is True


def test_agent_card_matches_official_a2a_schema() -> None:
    card = service.agent_card()
    parsed = ParseDict(card, AgentCard())
    assert parsed.supported_interfaces[0].protocol_version == "1.0"
