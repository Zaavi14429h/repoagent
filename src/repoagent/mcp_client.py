from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from uuid import UUID

from repoagent.config import Settings
from repoagent.models import ChangeSet, PullRequestResult, RepositorySummary
from repoagent.services.github import GitHubService
from repoagent.services.repository import RepositoryService


class ToolCallError(RuntimeError):
    pass


class MCPProcessClient:
    """Calls a stdio MCP server and validates its structured response."""

    async def call(self, module: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as error:
            raise RuntimeError("Install the 'mcp' package to use MCP tool mode") from error

        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", module],
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)

        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            raise ToolCallError(self._text_content(result) or f"MCP tool failed: {tool_name}")
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        text = self._text_content(result)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ToolCallError(f"MCP tool returned non-JSON output: {text[:200]}") from error
        if not isinstance(parsed, dict):
            raise ToolCallError("MCP tool structured output must be an object")
        return parsed

    @staticmethod
    def _text_content(result: Any) -> str:
        values: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                values.append(text)
        return "\n".join(values)


class ToolGateway(ABC):
    @abstractmethod
    async def prepare_workspace(
        self, source: str, base_branch: str, run_id: UUID
    ) -> tuple[Path, str]: ...

    @abstractmethod
    async def inspect_repository(self, root: Path, base_branch: str) -> RepositorySummary: ...

    @abstractmethod
    async def read_context(self, root: Path, files: list[str]) -> dict[str, str]: ...

    @abstractmethod
    async def apply_change_set(self, root: Path, change_set: ChangeSet) -> list[str]: ...

    @abstractmethod
    async def get_diff(self, root: Path) -> tuple[str, list[str]]: ...

    @abstractmethod
    async def commit_and_push(self, root: Path, branch: str, message: str) -> dict[str, str]: ...

    @abstractmethod
    async def create_pull_request(
        self,
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
    ) -> PullRequestResult: ...


class MCPToolGateway(ToolGateway):
    def __init__(self, client: MCPProcessClient | None = None) -> None:
        self.client = client or MCPProcessClient()

    async def prepare_workspace(
        self, source: str, base_branch: str, run_id: UUID
    ) -> tuple[Path, str]:
        result = await self.client.call(
            "repoagent.mcp_servers.repository_server",
            "prepare_workspace",
            {"source": source, "base_branch": base_branch, "run_id": str(run_id)},
        )
        return Path(result["workspace_path"]), result["working_branch"]

    async def inspect_repository(self, root: Path, base_branch: str) -> RepositorySummary:
        result = await self.client.call(
            "repoagent.mcp_servers.repository_server",
            "inspect_repository",
            {"root": str(root), "base_branch": base_branch},
        )
        return RepositorySummary.model_validate(result)

    async def read_context(self, root: Path, files: list[str]) -> dict[str, str]:
        result = await self.client.call(
            "repoagent.mcp_servers.repository_server",
            "read_repository_context",
            {"root": str(root), "files": files},
        )
        return {str(key): str(value) for key, value in result.items()}

    async def apply_change_set(self, root: Path, change_set: ChangeSet) -> list[str]:
        result = await self.client.call(
            "repoagent.mcp_servers.repository_server",
            "apply_change_set",
            {"root": str(root), "change_set": change_set.model_dump(mode="json")},
        )
        return list(result["changed_files"])

    async def get_diff(self, root: Path) -> tuple[str, list[str]]:
        result = await self.client.call(
            "repoagent.mcp_servers.repository_server",
            "get_diff",
            {"root": str(root)},
        )
        return str(result["diff"]), list(result["changed_files"])

    async def commit_and_push(self, root: Path, branch: str, message: str) -> dict[str, str]:
        result = await self.client.call(
            "repoagent.mcp_servers.repository_server",
            "commit_and_push",
            {"root": str(root), "branch": branch, "message": message},
        )
        return {str(key): str(value) for key, value in result.items()}

    async def create_pull_request(
        self,
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
    ) -> PullRequestResult:
        result = await self.client.call(
            "repoagent.mcp_servers.github_server",
            "create_pull_request",
            {
                "repository": repository,
                "head": head,
                "base": base,
                "title": title,
                "body": body,
                "draft": draft,
            },
        )
        return PullRequestResult.model_validate(result)


class LocalToolGateway(ToolGateway):
    """Test-only/direct adapter; production workflow defaults to MCPToolGateway."""

    def __init__(self, settings: Settings) -> None:
        self.repository = RepositoryService(settings.workspace_root)
        self.github = GitHubService(settings.github_token)

    async def prepare_workspace(
        self, source: str, base_branch: str, run_id: UUID
    ) -> tuple[Path, str]:
        return self.repository.prepare_workspace(source, base_branch, run_id)

    async def inspect_repository(self, root: Path, base_branch: str) -> RepositorySummary:
        return self.repository.inspect(root, base_branch)

    async def read_context(self, root: Path, files: list[str]) -> dict[str, str]:
        return self.repository.read_context(root, files)

    async def apply_change_set(self, root: Path, change_set: ChangeSet) -> list[str]:
        return self.repository.apply_change_set(root, change_set)

    async def get_diff(self, root: Path) -> tuple[str, list[str]]:
        return self.repository.diff(root), self.repository.status_files(root)

    async def commit_and_push(self, root: Path, branch: str, message: str) -> dict[str, str]:
        revision = self.repository.commit(root, message)
        self.repository.push(root, branch)
        return {
            "revision": revision,
            "repository": self.repository.remote_slug(root),
            "branch": branch,
        }

    async def create_pull_request(
        self,
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
    ) -> PullRequestResult:
        return await self.github.create_pull_request(repository, head, base, title, body, draft)
