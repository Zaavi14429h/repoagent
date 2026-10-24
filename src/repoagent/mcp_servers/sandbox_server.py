from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from repoagent.config import get_settings
from repoagent.services.sandbox import SandboxService

mcp = FastMCP("repoagent-sandbox")


def service() -> SandboxService:
    settings = get_settings()
    return SandboxService(
        mode=settings.sandbox_mode,
        timeout_seconds=settings.command_timeout_seconds,
        max_output_chars=settings.max_output_chars,
        python_image=settings.python_runner_image,
        node_image=settings.node_runner_image,
    )


@mcp.tool()
def run_validation(root: str, commands: list[list[str]], attempt: int) -> dict:
    """Run allow-listed validation commands with resource and timeout controls."""
    report = service().run_validation(Path(root), commands, attempt)
    return report.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run()
