from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import uvicorn
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI, Header, HTTPException
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel

from repoagent.adk_validation.agent import ADKValidationRunner
from repoagent.config import get_settings
from repoagent.models import ValidationRequest

app = FastAPI(title="RepoAgent ADK Validation A2A Service", version="0.1.0")
_tasks: dict[str, dict[str, Any]] = {}


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int
    method: str
    params: dict[str, Any] = {}


def agent_card() -> dict[str, Any]:
    card = AgentCard(
        name="RepoAgent ADK Validation Agent",
        description="Runs repository validation through a restricted Sandbox MCP server.",
        supported_interfaces=[
            AgentInterface(
                url=f"{get_settings().adk_a2a_url.rstrip('/')}/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        version="0.1.0",
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="validate_repository",
                name="Validate repository",
                description="Runs allow-listed tests, linters, type checks, and builds.",
                tags=["testing", "validation", "repository"],
            )
        ],
    )
    return MessageToDict(card, preserving_proto_field_name=False)


@app.get("/.well-known/agent-card.json")
@app.get("/.well-known/agent.json")
async def get_agent_card() -> dict[str, Any]:
    return agent_card()


def _validation_request_from_message(message: dict[str, Any]) -> ValidationRequest:
    for part in message.get("parts", []):
        payload = part.get("data")
        if payload is None and "text" in part:
            payload = part["text"]
        if payload is None:
            continue
        if isinstance(payload, str):
            return ValidationRequest.model_validate_json(payload)
        return ValidationRequest.model_validate(payload)
    raise ValueError("A2A message contains no ValidationRequest data part")


async def _execute_message(message: dict[str, Any]) -> dict[str, Any]:
    request = _validation_request_from_message(message)
    task_id = str(uuid4())
    context_id = message.get("contextId") or str(request.run_id)
    now = datetime.now(UTC).isoformat()
    task: dict[str, Any] = {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": "TASK_STATE_WORKING", "timestamp": now},
        "history": [message],
    }
    _tasks[task_id] = task
    try:
        report = await ADKValidationRunner(get_settings()).run(request)
        task["status"] = {
            "state": "TASK_STATE_COMPLETED",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        task["artifacts"] = [
            {
                "artifactId": str(uuid4()),
                "name": "validation-report",
                "parts": [{"data": report.model_dump(mode="json")}],
            }
        ]
    except Exception as error:
        task["status"] = {
            "state": "TASK_STATE_FAILED",
            "timestamp": datetime.now(UTC).isoformat(),
            "message": {
                "role": "ROLE_AGENT",
                "messageId": str(uuid4()),
                "parts": [{"text": str(error)}],
            },
        }
    return task


@app.post("/a2a")
async def a2a_jsonrpc(
    request: JSONRPCRequest,
    a2a_version: str | None = Header(default=None, alias="A2A-Version"),
) -> dict[str, Any]:
    if a2a_version not in {None, "0.3", "1.0"}:
        raise HTTPException(status_code=400, detail="Unsupported A2A protocol version")
    try:
        if request.method in {"message/send", "message/stream", "SendMessage"}:
            result = await _execute_message(request.params["message"])
        elif request.method in {"tasks/get", "GetTask"}:
            result = _tasks[request.params["id"]]
        else:
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {"code": -32601, "message": "Method not found"},
            }
    except (KeyError, ValueError) as error:
        return {
            "jsonrpc": "2.0",
            "id": request.id,
            "error": {"code": -32602, "message": str(error)},
        }
    return {"jsonrpc": "2.0", "id": request.id, "result": result}


@app.post("/message:send")
async def a2a_rest_send(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message", payload)
    return await _execute_message(message)


@app.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return _tasks[task_id]


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok", "framework": "google-adk", "protocol": "a2a-1.0"}


def main() -> None:
    uvicorn.run("repoagent.adk_validation.service:app", host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
