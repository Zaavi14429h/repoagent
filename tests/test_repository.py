import subprocess
from pathlib import Path
from uuid import uuid4

from repoagent.models import ChangeSet, FileChange
from repoagent.services.repository import RepositoryService


def init_repository(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True)


def test_prepare_inspect_apply_and_diff(tmp_path: Path) -> None:
    source = tmp_path / "source"
    workspaces = tmp_path / "workspaces"
    init_repository(source)
    service = RepositoryService(workspaces)
    root, branch = service.prepare_workspace(str(source), "main", uuid4())

    assert branch.startswith("repoagent/run-")
    summary = service.inspect(root, "main")
    assert "Python" in summary.languages

    change_set = ChangeSet(
        summary="Update value",
        changes=[
            FileChange(
                path="app.py",
                operation="update",
                content="VALUE = 2\n",
                rationale="Requirement",
            )
        ],
    )
    service.apply_change_set(root, change_set)
    assert "+VALUE = 2" in service.diff(root)
