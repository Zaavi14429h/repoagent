from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from repoagent.guardrails import PathGuard
from repoagent.models import ChangeSet, RepositorySummary


class RepositoryError(RuntimeError):
    pass


class RepositoryService:
    ignored_directories = {
        ".git",
        ".repoagent",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
    }
    manifest_names = {
        "pyproject.toml",
        "requirements.txt",
        "poetry.lock",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "go.mod",
        "Cargo.toml",
    }

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if check and result.returncode != 0:
            raise RepositoryError(result.stderr.strip() or result.stdout.strip())
        return result

    def prepare_workspace(self, source: str, base_branch: str, run_id: UUID) -> tuple[Path, str]:
        destination = (self.workspace_root / str(run_id)).resolve()
        if not destination.is_relative_to(self.workspace_root):
            raise RepositoryError("Invalid run workspace")
        if destination.exists():
            raise RepositoryError(f"Run workspace already exists: {destination}")

        source_path = Path(source).expanduser()
        local_source = source_path.exists()
        if local_source:
            source_value = str(source_path.resolve())
        elif source.startswith("https://"):
            source_value = source
        else:
            raise RepositoryError("Repository must be an existing local path or HTTPS URL")

        clone_args = ["git", "clone", "--no-tags"]
        # Git's local hard-link optimization can hang for Windows drive paths.
        # Force the normal object-transfer path for reliable isolated clones.
        if local_source:
            clone_args.append("--no-local")
        clone = subprocess.run(
            [*clone_args, source_value, str(destination)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if clone.returncode != 0:
            raise RepositoryError(clone.stderr.strip() or clone.stdout.strip())

        checkout = self._git(destination, "switch", base_branch, check=False)
        if checkout.returncode != 0:
            self._git(destination, "switch", "--detach", f"origin/{base_branch}")

        branch = self._branch_name(run_id)
        self._git(destination, "switch", "-c", branch)
        return destination, branch

    @staticmethod
    def _branch_name(run_id: UUID) -> str:
        return f"repoagent/run-{str(run_id)[:8]}"

    def inspect(self, root: Path, base_branch: str) -> RepositorySummary:
        root = root.resolve()
        tree: list[str] = []
        manifests: list[str] = []
        languages: set[str] = set()
        extension_map = {
            ".py": "Python",
            ".js": "JavaScript",
            ".jsx": "JavaScript",
            ".ts": "TypeScript",
            ".tsx": "TypeScript",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
        }
        for path in root.rglob("*"):
            relative_parts = path.relative_to(root).parts
            if any(part in self.ignored_directories for part in relative_parts):
                continue
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                tree.append(relative)
                if path.name in self.manifest_names:
                    manifests.append(relative)
                language = extension_map.get(path.suffix.lower())
                if language:
                    languages.add(language)
                if len(tree) >= 5_000:
                    break

        commands = self.detect_validation_commands(root)
        return RepositorySummary(
            root=root,
            name=root.name,
            default_branch=base_branch,
            languages=sorted(languages),
            manifests=sorted(manifests),
            test_commands=commands,
            relevant_files=tree[:250],
            tree=tree,
        )

    @staticmethod
    def detect_validation_commands(root: Path) -> list[list[str]]:
        commands: list[list[str]] = []
        has_python_tests = any(
            (
                (root / "pyproject.toml").exists(),
                (root / "pytest.ini").exists(),
                bool(list(root.glob("test*.py"))),
                (root / "tests").exists(),
            )
        )
        if has_python_tests:
            commands.append(["python", "-m", "pytest", "-q"])

        if (root / "package.json").exists():
            commands.append(["npm", "test", "--", "--runInBand"])
        if (root / "go.mod").exists():
            commands.append(["go", "test", "./..."])
        if (root / "Cargo.toml").exists():
            commands.append(["cargo", "test", "--locked"])
        return commands or [["git", "diff", "--check"]]

    @staticmethod
    def read_context(root: Path, files: list[str], max_chars: int = 120_000) -> dict[str, str]:
        context: dict[str, str] = {}
        remaining = max_chars
        for value in files:
            target = PathGuard.resolve(root, value)
            if not target.is_file():
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
            context[value] = text[:remaining]
            remaining -= len(context[value])
            if remaining <= 0:
                break
        return context

    @staticmethod
    def apply_change_set(root: Path, change_set: ChangeSet) -> list[str]:
        PathGuard.validate_changes(root, change_set.changes)
        changed: list[str] = []
        for change in change_set.changes:
            target = PathGuard.resolve(root, change.path)
            if change.operation == "delete":
                if target.exists() and target.is_file():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(change.content or "", encoding="utf-8", newline="\n")
            changed.append(change.path)
        return changed

    @classmethod
    def diff(cls, root: Path) -> str:
        return cls._git(root, "diff", "--no-ext-diff", "--", ".").stdout

    @classmethod
    def status_files(cls, root: Path) -> list[str]:
        output = cls._git(root, "status", "--porcelain=v1").stdout
        return [line[3:] for line in output.splitlines() if len(line) > 3]

    @classmethod
    def commit(cls, root: Path, message: str) -> str:
        cls._git(root, "add", "--all")
        cls._git(root, "commit", "-m", message)
        return cls._git(root, "rev-parse", "HEAD").stdout.strip()

    @classmethod
    def push(cls, root: Path, branch: str) -> None:
        cls._git(root, "push", "--set-upstream", "origin", branch)

    @classmethod
    def remote_slug(cls, root: Path) -> str:
        remote = cls._git(root, "remote", "get-url", "origin").stdout.strip()
        if remote.startswith("git@github.com:"):
            slug = remote.removeprefix("git@github.com:")
        else:
            parsed = urlparse(remote)
            if parsed.hostname != "github.com":
                raise RepositoryError("PR creation currently supports GitHub remotes only")
            slug = parsed.path.lstrip("/")
        slug = re.sub(r"\.git$", "", slug)
        if slug.count("/") != 1:
            raise RepositoryError("Could not determine GitHub owner/repository")
        return slug

    def remove_workspace(self, root: Path) -> None:
        resolved = root.resolve()
        if not resolved.is_relative_to(self.workspace_root) or resolved == self.workspace_root:
            raise RepositoryError("Refusing to remove a path outside the workspace root")
        shutil.rmtree(resolved)
