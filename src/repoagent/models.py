from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    GUARDING = "guarding"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    CODING = "coding"
    VALIDATING = "validating"
    CORRECTING = "correcting"
    REVIEWING = "reviewing"
    PR_CREATING = "pr_creating"
    COMPLETED = "completed"
    NEEDS_HUMAN = "needs_human"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            self.COMPLETED,
            self.NEEDS_HUMAN,
            self.BLOCKED,
            self.FAILED,
            self.CANCELLED,
        }


class RunRequest(BaseModel):
    requirement: str = Field(min_length=5, max_length=20_000)
    repository: str = Field(description="Local Git repository path or an HTTPS Git repository URL")
    base_branch: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]+$")
    create_pull_request: bool = True
    draft_pull_request: bool = True
    validation_commands: list[list[str]] | None = None

    @model_validator(mode="after")
    def validate_repository(self) -> RunRequest:
        value = self.repository.strip()
        if not value:
            raise ValueError("repository cannot be empty")
        if value.startswith(("http://", "git://", "ssh://", "git@")):
            raise ValueError("Only local paths and HTTPS repository URLs are accepted")
        return self


class GuardrailResult(BaseModel):
    allowed: bool
    normalized_requirement: str
    flags: list[str] = []
    reason: str | None = None


class RepositorySummary(BaseModel):
    root: Path
    name: str
    default_branch: str
    languages: list[str] = []
    manifests: list[str] = []
    test_commands: list[list[str]] = []
    relevant_files: list[str] = []
    tree: list[str] = []


class PlanStep(BaseModel):
    order: int = Field(ge=1)
    description: str
    files: list[str] = []
    validation: str | None = None


class ImplementationPlan(BaseModel):
    summary: str
    assumptions: list[str] = []
    steps: list[PlanStep] = []
    acceptance_criteria: list[str] = []
    risks: list[str] = []


class FileChange(BaseModel):
    path: str
    operation: Literal["create", "update", "delete"]
    content: str | None = None
    rationale: str

    @model_validator(mode="after")
    def content_required(self) -> FileChange:
        if self.operation != "delete" and self.content is None:
            raise ValueError("content is required for create and update operations")
        return self


class ChangeSet(BaseModel):
    summary: str
    changes: list[FileChange] = []
    notes: list[str] = []


class CommandResult(BaseModel):
    argv: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class ValidationRequest(BaseModel):
    run_id: UUID
    repository_path: Path
    commands: list[list[str]]
    attempt: int = Field(ge=1)


class ValidationReport(BaseModel):
    passed: bool
    attempt: int = Field(ge=1)
    command_results: list[CommandResult] = []
    summary: str
    recommended_actions: list[str] = []


class ReviewFinding(BaseModel):
    severity: Literal["info", "warning", "critical"]
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    message: str
    recommendation: str | None = None


class ReviewReport(BaseModel):
    approved: bool
    summary: str
    findings: list[ReviewFinding] = []

    @property
    def has_critical_findings(self) -> bool:
        return any(item.severity == "critical" for item in self.findings)


class PullRequestResult(BaseModel):
    created: bool
    number: int | None = None
    url: HttpUrl | None = None
    title: str | None = None
    error: str | None = None


class PullRequestDraft(BaseModel):
    title: str = Field(min_length=5, max_length=256)
    body: str = Field(min_length=20, max_length=60_000)


class RunMetrics(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0
    agent_calls: int = 0
    tool_calls: int = 0
    correction_iterations: int = 0


class RepoRunState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    run_id: UUID = Field(default_factory=uuid4)
    status: RunStatus = RunStatus.QUEUED
    request: RunRequest
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    workspace_path: Path | None = None
    working_branch: str | None = None
    guardrail: GuardrailResult | None = None
    repository_summary: RepositorySummary | None = None
    plan: ImplementationPlan | None = None
    change_sets: list[ChangeSet] = []
    validation_reports: list[ValidationReport] = []
    review_reports: list[ReviewReport] = []
    pull_request: PullRequestResult | None = None

    correction_count: int = 0
    max_corrections: int = 3
    trace_id: str | None = None
    failure_reason: str | None = None
    metrics: RunMetrics = Field(default_factory=lambda: RunMetrics(started_at=utcnow()))

    def transition(self, status: RunStatus) -> None:
        self.status = status
        self.updated_at = utcnow()

    def finish(self, status: RunStatus, reason: str | None = None) -> None:
        self.transition(status)
        self.failure_reason = reason
        self.metrics.finished_at = utcnow()
        self.metrics.duration_seconds = (
            self.metrics.finished_at - self.metrics.started_at
        ).total_seconds()


class RunEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    timestamp: datetime = Field(default_factory=utcnow)
    event_type: str
    component: str
    message: str
    data: dict[str, Any] = {}
    error: str | None = None


class RepoAgentFinalReport(BaseModel):
    run_id: UUID
    status: RunStatus
    requirement: str
    working_branch: str | None
    plan: ImplementationPlan | None
    changed_files: list[str]
    validation: ValidationReport | None
    review: ReviewReport | None
    pull_request: PullRequestResult | None
    metrics: RunMetrics
    failure_reason: str | None

    @classmethod
    def from_state(cls, state: RepoRunState) -> RepoAgentFinalReport:
        changed = sorted({change.path for cs in state.change_sets for change in cs.changes})
        return cls(
            run_id=state.run_id,
            status=state.status,
            requirement=state.request.requirement,
            working_branch=state.working_branch,
            plan=state.plan,
            changed_files=changed,
            validation=state.validation_reports[-1] if state.validation_reports else None,
            review=state.review_reports[-1] if state.review_reports else None,
            pull_request=state.pull_request,
            metrics=state.metrics,
            failure_reason=state.failure_reason,
        )

    def to_markdown(self) -> str:
        validation = "not run"
        if self.validation:
            validation = "passed" if self.validation.passed else "failed"
        review = "not run"
        if self.review:
            review = "approved" if self.review.approved else "changes requested"
        pr = (
            str(self.pull_request.url)
            if self.pull_request and self.pull_request.url
            else "not created"
        )
        files = "\n".join(f"- `{path}`" for path in self.changed_files) or "- None"
        return (
            f"# RepoAgent run {self.run_id}\n\n"
            f"| Field | Result |\n|---|---|\n"
            f"| Status | `{self.status}` |\n"
            f"| Branch | `{self.working_branch or 'n/a'}` |\n"
            f"| Validation | {validation} |\n"
            f"| Review | {review} |\n"
            f"| Pull request | {pr} |\n\n"
            f"## Changed files\n\n{files}\n"
        )
