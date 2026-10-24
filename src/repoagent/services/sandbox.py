from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from repoagent.models import CommandResult, ValidationReport


class CommandPolicyError(ValueError):
    pass


class SandboxService:
    """Runs allow-listed validation commands locally or in isolated containers."""

    allowed_programs = {
        "python",
        "python3",
        "pytest",
        "ruff",
        "mypy",
        "npm",
        "npx",
        "node",
        "go",
        "cargo",
        "git",
    }
    forbidden_tokens = {
        "rm",
        "del",
        "format",
        "shutdown",
        "reboot",
        "curl",
        "wget",
        "powershell",
        "pwsh",
        "cmd",
        "bash",
        "sh",
    }

    def __init__(
        self,
        mode: str,
        timeout_seconds: int,
        max_output_chars: int,
        python_image: str,
        node_image: str,
    ) -> None:
        self.mode = mode
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.python_image = python_image
        self.node_image = node_image

    def validate_argv(self, argv: list[str]) -> None:
        if not argv or len(argv) > 20:
            raise CommandPolicyError("Validation command must contain 1-20 arguments")
        program = Path(argv[0]).name.lower()
        if program not in self.allowed_programs:
            raise CommandPolicyError(f"Program is not allow-listed: {program}")
        lowered = {part.lower() for part in argv}
        blocked = lowered.intersection(self.forbidden_tokens)
        if blocked:
            raise CommandPolicyError(f"Forbidden command tokens: {sorted(blocked)}")
        if any("&&" in part or ";" in part or "|" in part for part in argv):
            raise CommandPolicyError("Shell operators are not allowed")

    def run_validation(
        self, root: Path, commands: list[list[str]], attempt: int
    ) -> ValidationReport:
        results: list[CommandResult] = []
        for argv in commands:
            self.validate_argv(argv)
            if self.mode == "docker":
                result = self._run_docker(root, argv)
            else:
                result = self._run_local(root, argv)
            results.append(result)
            if not result.passed:
                break
        passed = bool(results) and all(result.passed for result in results)
        failed = next((result for result in results if not result.passed), None)
        summary = (
            f"All {len(results)} validation command(s) passed."
            if passed
            else f"Validation failed at: {' '.join(failed.argv) if failed else 'no command'}"
        )
        actions = []
        if failed:
            actions.append("Use the captured stdout and stderr to correct the implementation.")
        return ValidationReport(
            passed=passed,
            attempt=attempt,
            command_results=results,
            summary=summary,
            recommended_actions=actions,
        )

    def _run_local(self, root: Path, argv: list[str]) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
            return CommandResult(
                argv=argv,
                exit_code=completed.returncode,
                stdout=completed.stdout[-self.max_output_chars :],
                stderr=completed.stderr[-self.max_output_chars :],
                duration_seconds=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as error:
            return CommandResult(
                argv=argv,
                exit_code=124,
                stdout=(error.stdout or "")[-self.max_output_chars :],
                stderr=(error.stderr or "")[-self.max_output_chars :],
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )

    def _run_docker(self, root: Path, argv: list[str]) -> CommandResult:
        if not shutil.which("docker"):
            raise RuntimeError("Docker sandbox requested, but Docker is not installed")
        started = time.monotonic()
        image = (
            self.node_image if Path(argv[0]).name in {"npm", "npx", "node"} else self.python_image
        )
        with tempfile.TemporaryDirectory(prefix="repoagent-sandbox-") as temporary:
            copy_root = Path(temporary) / "workspace"
            shutil.copytree(
                root,
                copy_root,
                ignore=shutil.ignore_patterns(".git", ".env", ".repoagent", "node_modules"),
            )
            docker_argv = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--cpus",
                "1.0",
                "--memory",
                "1g",
                "--pids-limit",
                "256",
                "--security-opt",
                "no-new-privileges",
                "--cap-drop",
                "ALL",
                "--user",
                "65534:65534",
                "--volume",
                f"{copy_root}:/workspace:rw",
                "--workdir",
                "/workspace",
                image,
                *argv,
            ]
            try:
                completed = subprocess.run(
                    docker_argv,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                )
                return CommandResult(
                    argv=argv,
                    exit_code=completed.returncode,
                    stdout=completed.stdout[-self.max_output_chars :],
                    stderr=completed.stderr[-self.max_output_chars :],
                    duration_seconds=time.monotonic() - started,
                )
            except subprocess.TimeoutExpired as error:
                return CommandResult(
                    argv=argv,
                    exit_code=124,
                    stdout=(error.stdout or "")[-self.max_output_chars :],
                    stderr=(error.stderr or "")[-self.max_output_chars :],
                    duration_seconds=time.monotonic() - started,
                    timed_out=True,
                )
