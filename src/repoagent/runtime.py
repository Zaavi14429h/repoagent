from __future__ import annotations

from repoagent.a2a_client import A2AValidationClient, ValidationClient
from repoagent.agents.base import AgentBackend
from repoagent.agents.crewai_backend import CrewAIBackend
from repoagent.agents.deterministic import DeterministicBackend
from repoagent.agents.provider import build_model_adapter
from repoagent.config import Settings, get_settings
from repoagent.events import AgentCallback, EventBus
from repoagent.mcp_client import MCPToolGateway, ToolGateway
from repoagent.models import RepoAgentFinalReport, RepoRunState, RunRequest
from repoagent.observability import TraceManager
from repoagent.store import StateStore
from repoagent.workflow import WorkflowEngine, build_crewai_flow


class RepoAgentRuntime:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        tools: ToolGateway | None = None,
        validation_client: ValidationClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_directories()
        self.store = StateStore(self.settings.database_path)
        self.events = EventBus(self.store)
        self.tools = tools or MCPToolGateway()
        self.validation_client = validation_client or A2AValidationClient(
            self.settings.adk_a2a_url,
            timeout_seconds=self.settings.command_timeout_seconds + 120,
        )
        self.trace = TraceManager(self.settings)

    def _agents_factory(self, state: RepoRunState) -> AgentBackend:
        if self.settings.agent_backend == "deterministic":
            return DeterministicBackend()
        adapter = build_model_adapter(self.settings)
        return CrewAIBackend(
            adapter,
            callback_factory=lambda name: AgentCallback(self.events, state.run_id, name),
        )

    def build_engine(self) -> WorkflowEngine:
        return WorkflowEngine(
            store=self.store,
            events=self.events,
            tools=self.tools,
            agents_factory=self._agents_factory,
            validation_client=self.validation_client,
            trace=self.trace,
            max_corrections=self.settings.max_corrections,
        )

    async def run(
        self, request: RunRequest, state: RepoRunState | None = None
    ) -> RepoAgentFinalReport:
        engine = self.build_engine()
        if self.settings.agent_backend == "crewai":
            flow = build_crewai_flow(engine, request, state)
            result = await flow.kickoff_async()
            if isinstance(result, RepoAgentFinalReport):
                return result
            return RepoAgentFinalReport.model_validate(result)
        return await engine.run(request, state)
