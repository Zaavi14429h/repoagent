from __future__ import annotations

from repoagent.agents.base import AgentBackend
from repoagent.models import (
    ChangeSet,
    ImplementationPlan,
    PlanStep,
    PullRequestDraft,
    RepoRunState,
    ReviewReport,
)


class DeterministicBackend(AgentBackend):
    """Offline smoke-test backend. It intentionally does not generate source changes."""

    async def plan(
        self, state: RepoRunState, repository_context: dict[str, str]
    ) -> ImplementationPlan:
        return ImplementationPlan(
            summary=f"Analyze and implement: {state.request.requirement}",
            assumptions=["Offline deterministic mode cannot synthesize implementation code."],
            steps=[
                PlanStep(
                    order=1,
                    description="Inspect relevant source and test files",
                    files=list(repository_context)[:10],
                    validation="Run repository validation commands",
                )
            ],
            acceptance_criteria=["Existing validation commands pass"],
        )

    async def code(
        self,
        state: RepoRunState,
        repository_context: dict[str, str],
        feedback: str | None,
    ) -> ChangeSet:
        return ChangeSet(
            summary="No changes generated in deterministic mode",
            notes=["Configure REPOAGENT_AGENT_BACKEND=crewai and a model API key."],
        )

    async def review(self, state: RepoRunState, diff: str) -> ReviewReport:
        return ReviewReport(
            approved=not bool(diff.strip()),
            summary="Offline structural review completed.",
        )

    async def draft_pull_request(self, state: RepoRunState, diff: str) -> PullRequestDraft:
        return PullRequestDraft(
            title=f"RepoAgent: {state.request.requirement[:80]}",
            body="## Summary\n\nOffline deterministic run.\n\n## Validation\n\nSee run report.",
        )
