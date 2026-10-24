from __future__ import annotations

import asyncio
import json
import statistics
from pathlib import Path

from pydantic import BaseModel, Field

from repoagent.config import get_settings
from repoagent.models import RepoAgentFinalReport, RunRequest, RunStatus
from repoagent.runtime import RepoAgentRuntime


class EvaluationCase(BaseModel):
    case_id: str
    repository: str
    requirement: str
    base_branch: str = "main"
    validation_commands: list[list[str]] | None = None
    tags: list[str] = []


class EvaluationSummary(BaseModel):
    total_cases: int
    completed: int
    task_completion_rate: float = Field(ge=0, le=1)
    validation_pass_rate: float = Field(ge=0, le=1)
    mean_correction_iterations: float
    mean_duration_seconds: float
    p95_duration_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float
    results: list[RepoAgentFinalReport]


def load_cases(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            cases.append(EvaluationCase.model_validate_json(line))
        except Exception as error:
            raise ValueError(f"Invalid evaluation case at line {line_number}: {error}") from error
    return cases


async def evaluate(path: Path, output: Path) -> EvaluationSummary:
    runtime = RepoAgentRuntime(get_settings())
    results: list[RepoAgentFinalReport] = []
    for case in load_cases(path):
        results.append(
            await runtime.run(
                RunRequest(
                    repository=case.repository,
                    requirement=case.requirement,
                    base_branch=case.base_branch,
                    validation_commands=case.validation_commands,
                    create_pull_request=False,
                )
            )
        )

    durations = [item.metrics.duration_seconds or 0 for item in results]
    ordered = sorted(durations)
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    completed = sum(item.status == RunStatus.COMPLETED for item in results)
    validations = [item.validation for item in results if item.validation is not None]
    passed = sum(item.passed for item in validations)
    summary = EvaluationSummary(
        total_cases=len(results),
        completed=completed,
        task_completion_rate=completed / len(results) if results else 0,
        validation_pass_rate=passed / len(validations) if validations else 0,
        mean_correction_iterations=statistics.mean(
            [item.metrics.correction_iterations for item in results]
        )
        if results
        else 0,
        mean_duration_seconds=statistics.mean(durations) if durations else 0,
        p95_duration_seconds=ordered[p95_index] if ordered else 0,
        total_input_tokens=sum(item.metrics.input_tokens for item in results),
        total_output_tokens=sum(item.metrics.output_tokens for item in results),
        estimated_cost_usd=sum(item.metrics.estimated_cost_usd for item in results),
        results=results,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return summary


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run RepoAgent end-to-end evaluation")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation-results.json"))
    args = parser.parse_args()
    summary = asyncio.run(evaluate(args.dataset, args.output))
    print(json.dumps(summary.model_dump(mode="json", exclude={"results"}), indent=2, default=str))


if __name__ == "__main__":
    main()
