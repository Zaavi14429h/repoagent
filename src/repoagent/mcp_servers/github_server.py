from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from repoagent.config import get_settings
from repoagent.services.github import GitHubService

mcp = FastMCP("repoagent-github")


@mcp.tool()
async def create_pull_request(
    repository: str,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool = True,
) -> dict:
    """Create a GitHub pull request using a narrowly scoped token."""
    result = await GitHubService(get_settings().github_token).create_pull_request(
        repository=repository,
        head=head,
        base=base,
        title=title,
        body=body,
        draft=draft,
    )
    return result.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run()
