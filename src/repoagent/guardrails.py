from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from repoagent.models import FileChange, GuardrailResult


class GuardrailViolation(ValueError):
    pass


class PromptGuard:
    """Reject high-confidence prompt injection and clearly destructive requests."""

    _injection_patterns = [
        re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|system)\s+instructions"),
        re.compile(r"(?i)reveal\s+(the\s+)?(system|developer)\s+prompt"),
        re.compile(r"(?i)act\s+as\s+if\s+you\s+have\s+no\s+(rules|restrictions)"),
        re.compile(r"(?i)exfiltrat(e|ion).*(secret|token|key|credential)"),
    ]
    _dangerous_patterns = [
        re.compile(r"(?i)delete\s+(the\s+)?(entire\s+)?(disk|filesystem|home directory)"),
        re.compile(r"(?i)(steal|dump|print).*(password|token|secret|credential)"),
        re.compile(r"(?i)(ransomware|credential stealer|keylogger)"),
    ]
    _abusive_patterns = [
        re.compile(r"(?i)\b(idiot|stupid|moron)\b"),
    ]

    def check(self, requirement: str) -> GuardrailResult:
        normalized = " ".join(requirement.split())
        flags: list[str] = []
        if any(pattern.search(normalized) for pattern in self._injection_patterns):
            flags.append("prompt_injection")
        if any(pattern.search(normalized) for pattern in self._dangerous_patterns):
            flags.append("destructive_or_malicious")
        if any(pattern.search(normalized) for pattern in self._abusive_patterns):
            flags.append("abusive_language")

        blocking = {"prompt_injection", "destructive_or_malicious"}.intersection(flags)
        return GuardrailResult(
            allowed=not blocking,
            normalized_requirement=normalized,
            flags=flags,
            reason=(
                "The requirement contains prompt-injection or destructive instructions."
                if blocking
                else None
            ),
        )

    def sanitize_repository_content(self, content: str) -> tuple[str, bool]:
        """Neutralize high-confidence instruction injection in untrusted files."""
        sanitized = content
        detected = False
        for pattern in self._injection_patterns:
            sanitized, count = pattern.subn("[POTENTIAL PROMPT INJECTION REDACTED]", sanitized)
            detected = detected or count > 0
        return sanitized, detected


class PathGuard:
    sensitive_paths = {
        ".env",
        ".git/config",
        ".git/hooks",
    }
    sensitive_prefixes = (
        ".github/workflows/",
        ".git/",
    )

    @classmethod
    def normalize_relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        path = PurePosixPath(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise GuardrailViolation(f"Unsafe repository path: {value!r}")
        if normalized in cls.sensitive_paths or normalized.startswith(cls.sensitive_prefixes):
            raise GuardrailViolation(f"Sensitive repository path requires human approval: {value}")
        return str(path)

    @classmethod
    def resolve(cls, root: Path, value: str) -> Path:
        relative = cls.normalize_relative(value)
        root_resolved = root.resolve()
        target = (root_resolved / relative).resolve()
        if not target.is_relative_to(root_resolved):
            raise GuardrailViolation(f"Path escapes repository root: {value!r}")
        return target

    @classmethod
    def validate_changes(cls, root: Path, changes: list[FileChange]) -> None:
        if len(changes) > 100:
            raise GuardrailViolation("A single change set cannot modify more than 100 files")
        for change in changes:
            cls.resolve(root, change.path)
            if change.content and len(change.content) > 1_000_000:
                raise GuardrailViolation(f"Generated file is too large: {change.path}")
