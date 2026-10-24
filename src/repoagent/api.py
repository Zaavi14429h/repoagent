from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from repoagent.config import get_settings
from repoagent.models import RepoAgentFinalReport, RepoRunState, RunRequest
from repoagent.runtime import RepoAgentRuntime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

runtime: RepoAgentRuntime | None = None
_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global runtime
    runtime = RepoAgentRuntime(get_settings())
    yield


app = FastAPI(
    title="RepoAgent API",
    version="0.1.0",
    description="Natural-language requirement to tested, reviewed pull request.",
    lifespan=lifespan,
)


class RunAccepted(BaseModel):
    run_id: UUID
    status: str


def get_runtime() -> RepoAgentRuntime:
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime is not initialized")
    return runtime


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "repoagent-api"}


@app.post("/runs", response_model=RunAccepted, status_code=202)
async def create_run(request: RunRequest) -> RunAccepted:
    active = get_runtime()
    state = RepoRunState(request=request, max_corrections=active.settings.max_corrections)
    active.store.save_run(state)
    task = asyncio.create_task(active.run(request, state))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return RunAccepted(run_id=state.run_id, status=state.status.value)


@app.get("/runs", response_model=list[RepoRunState])
async def list_runs(limit: int = 100) -> list[RepoRunState]:
    return get_runtime().store.list_runs(max(1, min(limit, 500)))


@app.get("/runs/{run_id}", response_model=RepoRunState)
async def get_run(run_id: UUID) -> RepoRunState:
    state = get_runtime().store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return state


@app.get("/runs/{run_id}/events")
async def get_run_events(run_id: UUID):
    if get_runtime().store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return get_runtime().store.get_events(run_id)


@app.get("/runs/{run_id}/report", response_model=RepoAgentFinalReport)
async def get_run_report(run_id: UUID) -> RepoAgentFinalReport:
    state = get_runtime().store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RepoAgentFinalReport.from_state(state)


def main() -> None:
    uvicorn.run("repoagent.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
