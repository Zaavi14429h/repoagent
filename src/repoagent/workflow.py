from __future__ import annotations

import traceback
from collections.abc import Callable
from pathlib import Path

from repoagent.a2a_client import ValidationClient
from repoagent.agents.base import AgentBackend
from repoagent.events import EventBus
from repoagent.guardrails import GuardrailViolation, PromptGuard
from repoagent.mcp_client import ToolGateway
from repoagent.models import (
    RepoAgentFinalReport,
    RepoRunState,
    RunRequest,
    RunStatus,
    ValidationRequest,
)
from repoagent.observability import TraceManager
from repoagent.store import StateStore


class WorkflowEngine:
    """Deterministic control plane for the agentic workflow."""

    def __init__(
        self,
        *,
        store: StateStore,
        events: EventBus,
        tools: ToolGateway,
        agents_factory: Callable[[RepoRunState], AgentBackend],
        validation_client: ValidationClient,
        trace: TraceManager,
        max_corrections: int,
    ) -> None:
        self.store = store
        self.events = events
        self.tools = tools
        self.agents_factory = agents_factory
        self.validation_client = validation_client
        self.trace = trace
        self.max_corrections = max_corrections
        self.prompt_guard = PromptGuard()

    async def run(
        self, request: RunRequest, state: RepoRunState | None = None
    ) -> RepoAgentFinalReport:
        state = state or RepoRunState(request=request, max_corrections=self.max_corrections)
        self.store.save_run(state)
        self.events.emit(
            state.run_id,
            "run_started",
            "orchestrator",
            "RepoAgent run accepted",
            data={"repository": request.repository, "base_branch": request.base_branch},
        )
        try:
            with self.trace.span(
                "repoagent-run",
                input={"requirement": request.requirement, "repository": request.repository},
                metadata={"run_id": str(state.run_id)},
                as_type="agent",
            ):
                await self._execute(state)
        except GuardrailViolation as error:
            state.finish(RunStatus.BLOCKED, str(error))
            self.events.emit(
                state.run_id,
                "guardrail_blocked",
                "guardrails",
                "The workflow was blocked by a guardrail",
                error=str(error),
            )
        except Exception as error:
            state.finish(RunStatus.FAILED, str(error))
            self.events.emit(
                state.run_id,
                "run_failed",
                "orchestrator",
                "Unhandled workflow failure",
                error=str(error),
                data={"traceback": traceback.format_exc(limit=20)},
            )
        finally:
            if not state.status.terminal:
                state.finish(RunStatus.FAILED, "Workflow exited without a terminal state")
            self.store.save_run(state)
            self.events.emit(
                state.run_id,
                "run_finished",
                "orchestrator",
                f"Run finished with status {state.status.value}",
                data={"status": state.status.value},
            )
            self.trace.flush()
        return RepoAgentFinalReport.from_state(state)

    async def _execute(self, state: RepoRunState) -> None:
        await self._guard(state)
        await self._prepare_and_analyze(state)
        agents = self.agents_factory(state)
        await self._plan(state, agents)
        await self._implementation_loop(state, agents)
        if state.status.terminal:
            return
        await self._create_pr_or_complete(state, agents)

    async def _guard(self, state: RepoRunState) -> None:
        state.transition(RunStatus.GUARDING)
        self.events.state_updated(state, "Validating requirement")
        result = self.prompt_guard.check(state.request.requirement)
        state.guardrail = result
        if not result.allowed:
            raise GuardrailViolation(result.reason or "Requirement rejected")
        self.events.emit(
            state.run_id,
            "guardrail_passed",
            "guardrails",
            "Requirement passed input guardrails",
            data={"flags": result.flags},
        )

    async def _prepare_and_analyze(self, state: RepoRunState) -> None:
        state.transition(RunStatus.ANALYZING)
        self.events.state_updated(state, "Preparing isolated repository workspace")
        with self.trace.span("mcp.prepare_workspace", as_type="tool"):
            root, branch = await self.tools.prepare_workspace(
                state.request.repository, state.request.base_branch, state.run_id
            )
        state.metrics.tool_calls += 1
        state.workspace_path = root
        state.working_branch = branch
        with self.trace.span("mcp.inspect_repository", as_type="tool"):
            state.repository_summary = await self.tools.inspect_repository(
                root, state.request.base_branch
            )
        state.metrics.tool_calls += 1
        self.events.state_updated(state, "Repository analysis completed")

    async def _plan(self, state: RepoRunState, agents: AgentBackend) -> None:
        state.transition(RunStatus.PLANNING)
        self.events.state_updated(state, "Planning implementation")
        context = await self._load_context(state)
        with self.trace.span("crew.planning_agent", as_type="agent"):
            state.plan = await agents.plan(state, context)
        state.metrics.agent_calls += 1
        self._sync_usage(state, agents)
        self.events.state_updated(state, "Implementation plan completed")

    async def _implementation_loop(self, state: RepoRunState, agents: AgentBackend) -> None:
        feedback: str | None = None
        while True:
            state.transition(RunStatus.CODING if feedback is None else RunStatus.CORRECTING)
            self.events.state_updated(
                state,
                "Generating implementation" if feedback is None else "Correcting implementation",
            )
            context = await self._load_context(state)
            with self.trace.span("crew.coding_agent", as_type="agent"):
                change_set = await agents.code(state, context, feedback)
            state.metrics.agent_calls += 1
            self._sync_usage(state, agents)
            if not change_set.changes:
                state.finish(
                    RunStatus.NEEDS_HUMAN,
                    "Coding Agent produced no changes; human intervention is required.",
                )
                self.events.state_updated(state, "No implementation changes were produced")
                return

            with self.trace.span("mcp.apply_change_set", as_type="tool"):
                changed_files = await self.tools.apply_change_set(self._root(state), change_set)
            state.metrics.tool_calls += 1
            state.change_sets.append(change_set)
            self.events.emit(
                state.run_id,
                "changes_applied",
                "repository_mcp",
                f"Applied {len(changed_files)} file change(s)",
                data={"files": changed_files},
            )

            report = await self._validate(state)
            if not report.passed:
                feedback = self._validation_feedback(report)
                if not self._can_retry(state, "validation_failed"):
                    return
                continue

            review = await self._review(state, agents)
            if review.approved and not review.has_critical_findings:
                return
            feedback = self._review_feedback(review)
            if not self._can_retry(state, "review_changes_requested"):
                return

    async def _validate(self, state: RepoRunState):
        state.transition(RunStatus.VALIDATING)
        self.events.state_updated(state, "Sending validation task to ADK agent over A2A")
        commands = state.request.validation_commands or (
            state.repository_summary.test_commands if state.repository_summary else []
        )
        request = ValidationRequest(
            run_id=state.run_id,
            repository_path=self._root(state),
            commands=commands,
            attempt=len(state.validation_reports) + 1,
        )
        with self.trace.span(
            "a2a.adk_validation_agent",
            input=request.model_dump(mode="json"),
            as_type="agent",
        ):
            report = await self.validation_client.validate(request)
        state.metrics.agent_calls += 1
        state.validation_reports.append(report)
        self.events.emit(
            state.run_id,
            "validation_completed",
            "adk_validation_agent",
            report.summary,
            data={"passed": report.passed, "attempt": report.attempt},
        )
        self.store.save_run(state)
        return report

    async def _review(self, state: RepoRunState, agents: AgentBackend):
        state.transition(RunStatus.REVIEWING)
        self.events.state_updated(state, "Reviewing validated changes")
        diff, changed_files = await self.tools.get_diff(self._root(state))
        state.metrics.tool_calls += 1
        with self.trace.span("crew.review_agent", as_type="agent"):
            report = await agents.review(state, diff)
        state.metrics.agent_calls += 1
        self._sync_usage(state, agents)
        state.review_reports.append(report)
        self.events.emit(
            state.run_id,
            "review_completed",
            "review_agent",
            report.summary,
            data={"approved": report.approved, "changed_files": changed_files},
        )
        self.store.save_run(state)
        return report

    def _can_retry(self, state: RepoRunState, event_type: str) -> bool:
        if state.correction_count >= state.max_corrections:
            state.finish(
                RunStatus.NEEDS_HUMAN,
                f"Correction limit ({state.max_corrections}) reached.",
            )
            self.events.state_updated(state, "Correction limit reached")
            return False
        state.correction_count += 1
        state.metrics.correction_iterations = state.correction_count
        self.events.emit(
            state.run_id,
            event_type,
            "orchestrator",
            f"Routing feedback to Coding Agent (correction {state.correction_count})",
            data={"correction": state.correction_count},
        )
        return True

    async def _create_pr_or_complete(self, state: RepoRunState, agents: AgentBackend) -> None:
        if not state.request.create_pull_request:
            state.finish(RunStatus.COMPLETED)
            self.events.state_updated(state, "Run completed without PR creation by request")
            return

        state.transition(RunStatus.PR_CREATING)
        self.events.state_updated(state, "Creating reviewed pull request")
        diff, _ = await self.tools.get_diff(self._root(state))
        state.metrics.tool_calls += 1
        draft = await agents.draft_pull_request(state, diff)
        state.metrics.agent_calls += 1
        self._sync_usage(state, agents)
        try:
            push = await self.tools.commit_and_push(
                self._root(state),
                state.working_branch or "",
                draft.title,
            )
            state.metrics.tool_calls += 1
            state.pull_request = await self.tools.create_pull_request(
                repository=push["repository"],
                head=state.working_branch or "",
                base=state.request.base_branch,
                title=draft.title,
                body=draft.body,
                draft=state.request.draft_pull_request,
            )
            state.metrics.tool_calls += 1
        except Exception as error:
            state.finish(
                RunStatus.NEEDS_HUMAN,
                f"Changes passed validation and review, but PR creation failed: {error}",
            )
            self.events.state_updated(state, "PR creation requires human intervention")
            return

        if not state.pull_request.created:
            state.finish(
                RunStatus.NEEDS_HUMAN,
                f"PR creation failed: {state.pull_request.error}",
            )
            self.events.state_updated(state, "GitHub rejected PR creation")
            return
        state.finish(RunStatus.COMPLETED)
        self.events.state_updated(state, "Pull request created successfully")

    async def _load_context(self, state: RepoRunState) -> dict[str, str]:
        summary = state.repository_summary
        if summary is None:
            return {}
        candidates: list[str] = []
        candidates.extend(summary.manifests)
        candidates.extend(
            path
            for path in summary.tree
            if Path(path).name.lower().startswith(("readme", "contributing"))
        )
        if state.plan:
            candidates.extend(path for step in state.plan.steps for path in step.files)
        candidates.extend(change.path for cs in state.change_sets for change in cs.changes)
        candidates.extend(summary.relevant_files[:40])
        unique = list(dict.fromkeys(candidates))[:80]
        context = await self.tools.read_context(self._root(state), unique)
        state.metrics.tool_calls += 1
        flagged_files: list[str] = []
        sanitized: dict[str, str] = {}
        for path, content in context.items():
            sanitized[path], detected = self.prompt_guard.sanitize_repository_content(content)
            if detected:
                flagged_files.append(path)
        if flagged_files:
            self.events.emit(
                state.run_id,
                "repository_prompt_injection_detected",
                "guardrails",
                "Potential prompt-injection text was neutralized in repository content",
                data={"files": flagged_files},
            )
        return sanitized

    @staticmethod
    def _validation_feedback(report) -> str:
        parts = [report.summary]
        for result in report.command_results:
            if result.passed:
                continue
            parts.append(
                f"Command: {' '.join(result.argv)}\n"
                f"Exit code: {result.exit_code}\n"
                f"stdout:\n{result.stdout[-20_000:]}\n"
                f"stderr:\n{result.stderr[-20_000:]}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _review_feedback(report) -> str:
        return (
            "\n".join(
                f"[{finding.severity}] {finding.file or 'general'}: {finding.message}"
                for finding in report.findings
            )
            or report.summary
        )

    @staticmethod
    def _root(state: RepoRunState) -> Path:
        if state.workspace_path is None:
            raise RuntimeError("Run workspace has not been prepared")
        return state.workspace_path

    @staticmethod
    def _sync_usage(state: RepoRunState, agents: AgentBackend) -> None:
        usage = agents.usage_snapshot()
        state.metrics.input_tokens = int(usage.get("input_tokens", 0))
        state.metrics.output_tokens = int(usage.get("output_tokens", 0))
        state.metrics.estimated_cost_usd = float(usage.get("estimated_cost_usd", 0.0))


def build_crewai_flow(
    engine: WorkflowEngine,
    request: RunRequest,
    state: RepoRunState | None = None,
):
    """Create the CrewAI Flow wrapper used by the API/CLI entrypoints."""

    try:
        from crewai.flow.flow import Flow, start
    except ImportError as error:
        raise RuntimeError("CrewAI is not installed") from error

    class RepoAgentCrewFlow(Flow):
        # RepoRunState/SQLite is the authoritative memory. Avoid CrewAI's optional
        # vector-memory backend, which would duplicate state and add native deps.
        _skip_auto_memory = True

        @start()
        async def execute(self):
            return await engine.run(request, state)

    return RepoAgentCrewFlow(suppress_flow_events=True, tracing=False)
