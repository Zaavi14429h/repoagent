from pathlib import Path

import pytest

from repoagent.services.sandbox import CommandPolicyError, SandboxService


def sandbox() -> SandboxService:
    return SandboxService(
        mode="local",
        timeout_seconds=10,
        max_output_chars=10_000,
        python_image="unused",
        node_image="unused",
    )


def test_rejects_shell_operators() -> None:
    with pytest.raises(CommandPolicyError):
        sandbox().validate_argv(["python", "-V", "&&", "whoami"])


def test_runs_allowlisted_command(tmp_path: Path) -> None:
    report = sandbox().run_validation(tmp_path, [["python", "-c", "print('ok')"]], 1)
    assert report.passed
    assert report.command_results[0].stdout.strip() == "ok"
