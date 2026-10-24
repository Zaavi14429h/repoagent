from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from repoagent.agents.base import AgentBackend
from repoagent.agents.provider import ModelAdapter
from repoagent.events import AgentCallback
from repoagent.models import (
    ChangeSet,
    ImplementationPlan,
    PullRequestDraft,
    RepoRunState,
    ReviewReport,
)

T = TypeVar("T", bound=BaseModel)


class CrewAIBackend(AgentBackend):
    """Specialized CrewAI agents with Pydantic-validated outputs."""

    def __init__(
        self,
        model_adapter: ModelAdapter,
        callback_factory: Callable[[str], AgentCallback],
    ) -> None:
        self.model_adapter = model_adapter
        self.callback_factory = callback_factory
        self._input_tokens = 0
        self._output_tokens = 0

    async def plan(
        self, state: RepoRunState, repository_context: dict[str, str]
    ) -> ImplementationPlan:
        description = f"""
Create an implementation plan for this software requirement:

{state.guardrail.normalized_requirement if state.guardrail else state.request.requirement}

Repository summary:
{state.repository_summary.model_dump_json(indent=2) if state.repository_summary else "{}"}

Selected repository files:
{self._context_text(repository_context)}

The plan must be minimal, file-specific, testable, and preserve existing behavior.
Do not claim to have modified files. Return only the requested structured output.
"""
        return await self._run_agent(
            name="planning_agent",
            role="Senior software architect",
            goal="Produce safe, minimal, testable implementation plans for existing repositories",
            backstory="You specialize in repository-aware change planning and dependency analysis.",
            description=description,
            output_model=ImplementationPlan,
        )

    async def code(
        self,
        state: RepoRunState,
        repository_context: dict[str, str],
        feedback: str | None,
    ) -> ChangeSet:
        description = f"""
Implement the requirement as a complete file change set.

Requirement:
{state.guardrail.normalized_requirement if state.guardrail else state.request.requirement}

Approved plan:
{state.plan.model_dump_json(indent=2) if state.plan else "{}"}

Repository files:
{self._context_text(repository_context)}

Validation or review feedback from the previous attempt:
{feedback or "No previous failure; this is the first implementation attempt."}

For every create or update operation, return the complete final file content. Do not use
ellipses, placeholders, Markdown fences, or partial snippets. Keep the change set tightly
scoped. Never modify .git, .env, CI workflows, or files outside the repository.
"""
        return await self._run_agent(
            name="coding_agent",
            role="Senior implementation engineer",
            goal="Implement correct, minimal repository changes that satisfy the plan and tests",
            backstory=(
                "You write production-quality code and use failure logs to correct your work."
            ),
            description=description,
            output_model=ChangeSet,
        )

    async def review(self, state: RepoRunState, diff: str) -> ReviewReport:
        description = f"""
Review the Git diff against the requirement and implementation plan.

Requirement:
{state.request.requirement}

Plan:
{state.plan.model_dump_json(indent=2) if state.plan else "{}"}

Validation report:
{state.validation_reports[-1].model_dump_json(indent=2) if state.validation_reports else "{}"}

Diff:
{diff[:120_000]}

Focus on correctness, security, regressions, missing tests, and requirement coverage.
Approve only when there are no critical findings. Return evidence-based findings only.
"""
        return await self._run_agent(
            name="review_agent",
            role="Independent code reviewer",
            goal="Find concrete correctness and security problems before pull request creation",
            backstory="You perform precise, evidence-based reviews of tested changes.",
            description=description,
            output_model=ReviewReport,
        )

    async def draft_pull_request(self, state: RepoRunState, diff: str) -> PullRequestDraft:
        description = f"""
Write a concise GitHub pull request title and Markdown body.

Requirement: {state.request.requirement}
Plan: {state.plan.model_dump_json(indent=2) if state.plan else "{}"}
Validation: {
            state.validation_reports[-1].model_dump_json(indent=2)
            if state.validation_reports
            else "{}"
        }
Review: {state.review_reports[-1].model_dump_json(indent=2) if state.review_reports else "{}"}
Changed files: {json.dumps(sorted({c.path for cs in state.change_sets for c in cs.changes}))}

The body must contain Summary, Changes, Validation, Review, and Human Checklist sections.
Do not claim a command passed unless it appears in the validation report.
"""
        return await self._run_agent(
            name="pr_agent",
            role="Pull request author",
            goal="Create accurate and reviewable pull request descriptions",
            backstory="You summarize implementation evidence without exaggeration.",
            description=description,
            output_model=PullRequestDraft,
        )

    async def _run_agent(
        self,
        *,
        name: str,
        role: str,
        goal: str,
        backstory: str,
        description: str,
        output_model: type[T],
    ) -> T:
        callback = self.callback_factory(name)
        callback.started()
        try:
            result, usage = await asyncio.to_thread(
                self._kickoff,
                role,
                goal,
                backstory,
                description,
                output_model,
                callback,
            )
        except Exception as error:
            callback.failed(error)
            raise
        self._capture_usage(usage)
        callback.completed(data=usage)
        return result

    def _kickoff(
        self,
        role: str,
        goal: str,
        backstory: str,
        description: str,
        output_model: type[T],
        callback: AgentCallback,
    ) -> tuple[T, dict[str, int]]:
        try:
            from crewai import Agent, Crew, Process, Task
        except ImportError as error:
            raise RuntimeError("CrewAI is not installed") from error

        llm = self.model_adapter.for_crewai()

        def on_step(output) -> None:
            callback.progress(
                step_type=type(output).__name__,
                tool=getattr(output, "tool", None),
            )

        agent = Agent(
            role=role,
            goal=goal,
            backstory=backstory,
            llm=llm,
            allow_delegation=False,
            verbose=False,
            max_iter=12,
            step_callback=on_step,
        )
        task = Task(
            description=description,
            expected_output=f"A JSON object matching the {output_model.__name__} schema",
            agent=agent,
            output_pydantic=output_model,
        )
        output = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        ).kickoff()
        usage = self._usage_dict(getattr(output, "token_usage", None))
        value = getattr(output, "pydantic", None)
        if isinstance(value, output_model):
            return value, usage
        json_value = getattr(output, "json_dict", None)
        if isinstance(json_value, dict):
            return output_model.model_validate(json_value), usage
        return output_model.model_validate_json(str(getattr(output, "raw", output))), usage

    @staticmethod
    def _usage_dict(value) -> dict[str, int]:
        if value is None:
            return {"input_tokens": 0, "output_tokens": 0}
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        elif hasattr(value, "dict"):
            value = value.dict()
        if not isinstance(value, dict):
            return {"input_tokens": 0, "output_tokens": 0}
        return {
            "input_tokens": int(value.get("prompt_tokens", value.get("input_tokens", 0)) or 0),
            "output_tokens": int(
                value.get("completion_tokens", value.get("output_tokens", 0)) or 0
            ),
        }

    def _capture_usage(self, usage: dict[str, int]) -> None:
        self._input_tokens += usage["input_tokens"]
        self._output_tokens += usage["output_tokens"]

    def usage_snapshot(self) -> dict[str, float | int]:
        settings = getattr(self.model_adapter, "settings", None)
        input_rate = getattr(settings, "model_input_cost_per_million", 0.0)
        output_rate = getattr(settings, "model_output_cost_per_million", 0.0)
        cost = (self._input_tokens * input_rate + self._output_tokens * output_rate) / 1_000_000
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "estimated_cost_usd": cost,
        }

    @staticmethod
    def _context_text(context: dict[str, str]) -> str:
        sections = []
        for path, content in context.items():
            sections.append(f"--- FILE: {path} ---\n{content}")
        return "\n\n".join(sections)[:140_000]
