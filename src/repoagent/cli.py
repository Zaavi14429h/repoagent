from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import typer

from repoagent.config import get_settings
from repoagent.models import RepoAgentFinalReport, RunRequest
from repoagent.runtime import RepoAgentRuntime

app = typer.Typer(help="RepoAgent requirement-to-pull-request workflow")


@app.command()
def run(
    repository: str = typer.Option(..., "--repo", help="Local path or HTTPS Git URL"),
    requirement: str = typer.Option(..., "--requirement", "-r"),
    base_branch: str = typer.Option("main", "--base"),
    create_pr: bool = typer.Option(True, "--create-pr/--no-create-pr"),
    draft_pr: bool = typer.Option(True, "--draft-pr/--ready-pr"),
    output: Path | None = typer.Option(None, "--output", help="Write final JSON report"),
) -> None:
    request = RunRequest(
        repository=repository,
        requirement=requirement,
        base_branch=base_branch,
        create_pull_request=create_pr,
        draft_pull_request=draft_pr,
    )
    report = asyncio.run(RepoAgentRuntime(get_settings()).run(request))
    typer.echo(report.to_markdown())
    if output:
        output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    if report.status.value not in {"completed"}:
        raise typer.Exit(code=1)


@app.command("show")
def show_run(run_id: UUID, json_output: bool = typer.Option(False, "--json")) -> None:
    runtime = RepoAgentRuntime(get_settings())
    state = runtime.store.get_run(run_id)
    if state is None:
        typer.echo("Run not found", err=True)
        raise typer.Exit(code=1)
    report = RepoAgentFinalReport.from_state(state)
    typer.echo(report.model_dump_json(indent=2) if json_output else report.to_markdown())


@app.command("events")
def events(run_id: UUID) -> None:
    runtime = RepoAgentRuntime(get_settings())
    for event in runtime.store.get_events(run_id):
        typer.echo(json.dumps(event.model_dump(mode="json"), default=str))


if __name__ == "__main__":
    app()
