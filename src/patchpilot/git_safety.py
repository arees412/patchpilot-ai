"""Git operations constrained to local review by default."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from patchpilot.models import PolicyDecision
from patchpilot.policy import CommandPolicyEngine


class GitSafetyError(RuntimeError):
    """Raised for disabled or failed Git operations."""


class GitSafety:
    """Expose review-safe Git operations and keep publication disabled."""

    def __init__(self, repository: Path, *, allow_local_commit: bool = False) -> None:
        self.repository = repository.resolve(strict=True)
        self.allow_local_commit = allow_local_commit
        self.policy = CommandPolicyEngine()

    def status(self) -> str:
        return self._read(("git", "status", "--short", "--branch"))

    def diff(self, *paths: str) -> str:
        return self._read(("git", "diff", "--no-ext-diff", "--", *paths))

    def revision(self) -> str:
        return self._read(("git", "rev-parse", "HEAD")).strip()

    def commit(self, message: str) -> str:
        if not self.allow_local_commit:
            raise GitSafetyError("local commits are disabled by configuration")
        if not message.strip():
            raise GitSafetyError("commit message cannot be empty")
        return self._run(("git", "commit", "-m", message), approved=True)

    def push(self) -> None:
        raise GitSafetyError("Git push is disabled; publication requires an operator")

    def merge(self) -> None:
        raise GitSafetyError("Git merge is disabled; publication requires an operator")

    def force_push(self) -> None:
        raise GitSafetyError("force push is permanently denied by the core adapter")

    def create_tag(self) -> None:
        raise GitSafetyError("tag creation is disabled")

    def _read(self, command: Sequence[str]) -> str:
        return self._run(command, approved=False)

    def _run(self, command: Sequence[str], *, approved: bool) -> str:
        decision = self.policy.classify(command)
        if decision.decision is PolicyDecision.DENY:
            raise GitSafetyError(decision.reason)
        if decision.decision is PolicyDecision.REQUIRES_APPROVAL and not approved:
            raise GitSafetyError(decision.reason)
        result = subprocess.run(
            command,
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env={
                key: value
                for key, value in os.environ.items()
                if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR"}
            }
            | {"GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise GitSafetyError(result.stderr.strip() or "Git operation failed")
        return result.stdout
