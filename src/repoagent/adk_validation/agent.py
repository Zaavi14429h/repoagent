from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from repoagent.config import Settings
from repoagent.mcp_client import MCPProcessClient
from repoagent.models import ValidationReport, ValidationRequest


class ADKValidationRunner:
    """Runs a Google ADK agent whose only action is the Sandbox MCP tool."""

    def __init__(self, settings: Settings, mcp_client: MCPProcessClient | None = None) -> None:
        self.settings = settings
        self.mcp_client = mcp_client or MCPProcessClient()

    async def run(self, request: ValidationRequest) -> ValidationReport:
        try:
            from google.adk.agents import Agent
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.genai import types
        except ImportError as error:
            raise RuntimeError("Install google-adk to run the validation agent") from error

        captured: dict[str, Any] = {}

        async def execute_validation(
            repository_path: str, commands: list[list[str]], attempt: int
        ) -> dict:
            """Execute approved repository validation commands in the sandbox MCP server."""
            result = await self.mcp_client.call(
                "repoagent.mcp_servers.sandbox_server",
                "run_validation",
                {
                    "root": repository_path,
                    "commands": commands,
                    "attempt": attempt,
                },
            )
            captured["report"] = result
            return result

        root_agent = Agent(
            name="repoagent_validation_agent",
            model=self.settings.adk_model,
            description="Validates repository changes using the restricted sandbox MCP tool.",
            instruction=(
                "You are the RepoAgent Validation Agent. Call execute_validation exactly once "
                "with the repository_path, commands, and attempt supplied by the user. Never "
                "invent command output and never call any other tool. After the tool completes, "
                "briefly state whether validation passed."
            ),
            tools=[execute_validation],
        )
        app_name = "repoagent_validation"
        user_id = "repoagent-orchestrator"
        session_id = f"validation-{uuid4()}"
        sessions = InMemorySessionService()
        await sessions.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        runner = Runner(agent=root_agent, app_name=app_name, session_service=sessions)
        content = types.Content(
            role="user",
            parts=[types.Part(text=request.model_dump_json())],
        )
        async for _event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            pass

        if "report" not in captured:
            raise RuntimeError("ADK Validation Agent did not invoke the sandbox MCP tool")
        return ValidationReport.model_validate(captured["report"])


def build_root_agent(settings: Settings):
    """Discovery-friendly ADK agent definition for `adk web` and inspection.

    The A2A service builds a request-scoped agent so it can securely capture and
    validate the exact MCP result. This root agent exposes the framework identity.
    """

    try:
        from google.adk.agents import Agent
    except ImportError as error:
        raise RuntimeError("Install google-adk to build the validation agent") from error

    def validation_contract(request_json: str) -> str:
        """Validate that an incoming payload matches ValidationRequest."""
        request = ValidationRequest.model_validate_json(request_json)
        return json.dumps({"valid": True, "run_id": str(request.run_id)})

    return Agent(
        name="repoagent_validation_contract_agent",
        model=settings.adk_model,
        description="ADK agent for RepoAgent validation tasks.",
        instruction="Validate RepoAgent validation requests with the provided contract tool.",
        tools=[validation_contract],
    )
