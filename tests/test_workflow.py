import subprocess
from pathlib import Path

import pytest

from repoagent.a2a_client import ValidationClient
from repoagent.agents.base import AgentBackend
from repoagent.config import Settings
from repoagent.events import EventBus
from repoagent.mcp_client import LocalToolGateway
from repoagent.models import (
    ChangeSet,
    CommandResult,
    FileChange,
    ImplementationPlan,
    PlanStep,
    PullRequestDraft,
    RepoRunState,
    ReviewReport,
    RunRequest,
    RunStatus,
    ValidationReport,
    ValidationRequest,
)
from repoagent.observability import TraceManager
from repoagent.store import StateStore
from repoagent.workflow import WorkflowEngine


class FakeAgents(AgentBackend):
    def __init__(self) -> None:
        self.code_calls = 0

    async def plan(self, state, repository_context):
        return ImplementationPlan(
            summary="Update value",
            steps=[PlanStep(order=1, description="Update app.py", files=["app.py"])],
            acceptance_criteria=["Validation passes"],
        )

    async def code(self, state, repository_context, feedback):
        self.code_calls += 1
        return ChangeSet(
            summary="Update value",
            changes=[
                FileChange(
                    path="app.py",
                    operation="update",
                    content=f"VALUE = {self.code_calls + 1}\n",
                    rationale="Test workflow",
                )
            ],
        )

    async def review(self, state, diff):
        return ReviewReport(approved=True, summary="Looks good")

    async def draft_pull_request(self, state, diff):
        return PullRequestDraft(
            title="Update application value", body="## Summary\n\nUpdated value."
        )


class FailThenPassValidation(ValidationClient):
    def __init__(self) -> None:
        self.calls = 0

    async def validate(self, request: ValidationRequest) -> ValidationReport:
        self.calls += 1
        passed = self.calls > 1
        return ValidationReport(
            passed=passed,
            attempt=self.calls,
            command_results=[
                CommandResult(
                    argv=["python", "-m", "pytest"],
                    exit_code=0 if passed else 1,
                    stderr="first attempt failure" if not passed else "",
                    duration_seconds=0.1,
                )
            ],
            summary="passed" if passed else "failed",
        )


def init_repository(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True)


@pytest.mark.asyncio
async def test_failure_is_routed_back_to_coding_agent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    init_repository(source)
    settings = Settings(
        database_path=tmp_path / "state.db",
        workspace_root=tmp_path / "workspaces",
        agent_backend="deterministic",
        sandbox_mode="local",
        max_corrections=2,
    )
    store = StateStore(settings.database_path)
    events = EventBus(store)
    agents = FakeAgents()
    validation = FailThenPassValidation()
    engine = WorkflowEngine(
        store=store,
        events=events,
        tools=LocalToolGateway(settings),
        agents_factory=lambda _: agents,
        validation_client=validation,
        trace=TraceManager(settings),
        max_corrections=2,
    )
    request = RunRequest(
        repository=str(source),
        requirement="Update the application value safely",
        create_pull_request=False,
        validation_commands=[["python", "-m", "pytest"]],
    )
    report = await engine.run(request)

    assert report.status == RunStatus.COMPLETED
    assert agents.code_calls == 2
    assert validation.calls == 2
    assert report.metrics.correction_iterations == 1
    persisted = store.get_run(report.run_id)
    assert isinstance(persisted, RepoRunState)
    assert len(persisted.validation_reports) == 2
