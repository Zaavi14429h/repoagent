from __future__ import annotations

from pathlib import Path
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from repoagent.config import get_settings
from repoagent.models import ChangeSet
from repoagent.services.repository import RepositoryService

mcp = FastMCP("repoagent-repository")


def service() -> RepositoryService:
    return RepositoryService(get_settings().workspace_root)


@mcp.tool()
def prepare_workspace(source: str, base_branch: str, run_id: str) -> dict:
    """Clone a repository into an isolated run workspace and create a working branch."""
    root, branch = service().prepare_workspace(source, base_branch, UUID(run_id))
    return {"workspace_path": str(root), "working_branch": branch}


@mcp.tool()
def inspect_repository(root: str, base_branch: str) -> dict:
    """Return a bounded repository map, languages, manifests, and validation commands."""
    return service().inspect(Path(root), base_branch).model_dump(mode="json")


@mcp.tool()
def read_repository_context(root: str, files: list[str], max_chars: int = 120000) -> dict:
    """Read selected repository files after containment checks."""
    return service().read_context(Path(root), files, max_chars)


@mcp.tool()
def apply_change_set(root: str, change_set: dict) -> dict:
    """Apply a Pydantic-validated set of file creates, updates, and deletes."""
    changes = ChangeSet.model_validate(change_set)
    changed_files = service().apply_change_set(Path(root), changes)
    return {"changed_files": changed_files}


@mcp.tool()
def get_diff(root: str) -> dict:
    """Return the current Git diff and changed file list."""
    path = Path(root)
    return {
        "diff": service().diff(path),
        "changed_files": service().status_files(path),
    }


@mcp.tool()
def commit_and_push(root: str, branch: str, message: str) -> dict:
    """Commit changes and push only the isolated working branch."""
    path = Path(root)
    if branch in {"main", "master"} or not branch.startswith("repoagent/"):
        raise ValueError("Only repoagent/* working branches may be pushed")
    revision = service().commit(path, message)
    service().push(path, branch)
    return {
        "revision": revision,
        "repository": service().remote_slug(path),
        "branch": branch,
    }


if __name__ == "__main__":
    mcp.run()
