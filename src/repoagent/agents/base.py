from __future__ import annotations

from abc import ABC, abstractmethod

from repoagent.models import (
    ChangeSet,
    ImplementationPlan,
    PullRequestDraft,
    RepoRunState,
    ReviewReport,
)


class AgentBackend(ABC):
    """Provider-neutral contract for the reasoning agents in the workflow."""

    @abstractmethod
    async def plan(
        self, state: RepoRunState, repository_context: dict[str, str]
    ) -> ImplementationPlan: ...

    @abstractmethod
    async def code(
        self,
        state: RepoRunState,
        repository_context: dict[str, str],
        feedback: str | None,
    ) -> ChangeSet: ...

    @abstractmethod
    async def review(self, state: RepoRunState, diff: str) -> ReviewReport: ...

    @abstractmethod
    async def draft_pull_request(self, state: RepoRunState, diff: str) -> PullRequestDraft: ...

    def usage_snapshot(self) -> dict[str, float | int]:
        return {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
