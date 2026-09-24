"""Bounded execution adapters for controlled workspaces."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from patchpilot.models import CommandExecution, PolicyDecision, SandboxSession, ValidationStatus
from patchpilot.paths import UnsafePathError, resolve_workspace_path
from patchpilot.policy import CommandPolicyEngine


DEFAULT_ENV_ALLOWLIST = frozenset(
    {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TMP", "TEMP", "LANG", "LC_ALL"}
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret)\b(\s*[:=]\s*)([^\s'\"]+)"
)
BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


class SandboxError(RuntimeError):
    """Base sandbox failure."""


class CommandDenied(SandboxError):
    """Raised when policy denies a command."""


class CommandApprovalRequired(SandboxError):
    """Raised when a command lacks a scoped approval."""


class SandboxAdapter(Protocol):
    session: SandboxSession

    def execute(
        self,
        command: str | Sequence[str],
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        approved: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> CommandExecution: ...

    def close(self) -> None: ...


def redact_output(value: str) -> str:
    """Remove high-confidence credentials without returning their values."""

    value = PRIVATE_KEY.sub("<redacted-private-key>", value)
    value = BEARER.sub("Bearer <redacted>", value)
    return SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", value)


def _tokens(command: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, str):
        return tuple(shlex.split(command, posix=os.name != "nt"))
    return tuple(str(token) for token in command)


def _validate_symlinks(root: Path) -> None:
    resolved_root = root.resolve(strict=True)
    for path in root.rglob("*"):
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(resolved_root):
            raise UnsafePathError(f"symlink escapes repository: {path.relative_to(root)}")


class LocalSandbox:
    """Trusted-fixture adapter with copied workspace and strict command policy.

    This adapter is deterministic and suitable for CI fixtures. It is not an
    OS security boundary for arbitrary hostile repository code; use the Docker
    adapter (or a stronger external isolation system) for that threat model.
    """

    def __init__(
        self,
        repository: Path,
        *,
        policy: CommandPolicyEngine | None = None,
        base_directory: Path | None = None,
        default_timeout_seconds: float = 30,
        output_limit_bytes: int = 64_000,
        environment: Mapping[str, str] | None = None,
        environment_allowlist: frozenset[str] = DEFAULT_ENV_ALLOWLIST,
    ) -> None:
        source = repository.resolve(strict=True)
        _validate_symlinks(source)
        temporary_root = Path(
            tempfile.mkdtemp(prefix="patchpilot-", dir=base_directory.resolve() if base_directory else None)
        )
        workspace = temporary_root / "repository"
        shutil.copytree(
            source,
            workspace,
            symlinks=True,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".mypy_cache"),
        )
        self._temporary_root = temporary_root
        self._closed = False
        self.policy = policy or CommandPolicyEngine()
        self.default_timeout_seconds = default_timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.environment_allowlist = environment_allowlist
        self.environment = self._build_environment(environment or {})
        self.session = SandboxSession(
            repository_path=str(source),
            workspace_path=str(workspace),
            network_enabled=False,
        )

    @property
    def workspace(self) -> Path:
        return Path(self.session.workspace_path)

    def _build_environment(self, overrides: Mapping[str, str]) -> dict[str, str]:
        unexpected = set(overrides) - self.environment_allowlist
        if unexpected:
            raise SandboxError(f"environment keys are not allowed: {sorted(unexpected)}")
        selected = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in self.environment_allowlist
        }
        selected.update(overrides)
        selected["PATCHPILOT_NETWORK"] = "disabled"
        selected["GIT_TERMINAL_PROMPT"] = "0"
        selected.pop("GIT_ASKPASS", None)
        selected.pop("SSH_ASKPASS", None)
        return selected

    def execute(
        self,
        command: str | Sequence[str],
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        approved: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> CommandExecution:
        if self._closed:
            raise SandboxError("sandbox is closed")
        policy_result = self.policy.classify(command)
        if policy_result.decision is PolicyDecision.DENY:
            raise CommandDenied(f"{policy_result.rule}: {policy_result.reason}")
        if policy_result.decision is PolicyDecision.REQUIRES_APPROVAL and not approved:
            raise CommandApprovalRequired(f"{policy_result.rule}: {policy_result.reason}")

        working_directory = resolve_workspace_path(self.workspace, cwd, must_exist=True)
        if not working_directory.is_dir():
            raise UnsafePathError("command working directory must be a directory")
        tokens = _tokens(command)
        if not tokens:
            raise CommandDenied("command is empty")
        timeout = timeout_seconds or self.default_timeout_seconds
        started = time.monotonic()
        process = subprocess.Popen(
            tokens,
            cwd=working_directory,
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
        )
        timed_out = False
        cancelled = False
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if cancel_event and cancel_event.is_set():
                process.kill()
                cancelled = True
                break
            if elapsed >= timeout:
                process.kill()
                timed_out = True
                break
            time.sleep(0.02)
        stdout_bytes, stderr_bytes = process.communicate()
        stdout, stdout_truncated = self._bounded_decode(stdout_bytes)
        stderr, stderr_truncated = self._bounded_decode(stderr_bytes)
        exit_code = process.returncode if process.returncode is not None else -1
        status = (
            ValidationStatus.CANCELLED
            if cancelled
            else ValidationStatus.PASSED
            if exit_code == 0 and not timed_out
            else ValidationStatus.FAILED
        )
        return CommandExecution(
            command=tokens,
            exit_code=exit_code,
            duration_seconds=round(time.monotonic() - started, 6),
            stdout=redact_output(stdout),
            stderr=redact_output(stderr),
            status=status,
            timed_out=timed_out,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    def _bounded_decode(self, data: bytes) -> tuple[str, bool]:
        truncated = len(data) > self.output_limit_bytes
        if truncated:
            half = self.output_limit_bytes // 2
            data = data[:half] + b"\n<output-truncated>\n" + data[-half:]
        return data.decode("utf-8", errors="replace"), truncated

    def close(self) -> None:
        if not self._closed:
            shutil.rmtree(self._temporary_root)
            self._closed = True

    def __enter__(self) -> LocalSandbox:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class DockerSandbox:
    """Docker execution adapter with explicit isolation defaults."""

    def __init__(
        self,
        repository: Path,
        *,
        image: str = "python:3.12-slim",
        policy: CommandPolicyEngine | None = None,
        timeout_seconds: float = 60,
        output_limit_bytes: int = 64_000,
    ) -> None:
        root = repository.resolve(strict=True)
        _validate_symlinks(root)
        self.policy = policy or CommandPolicyEngine()
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.session = SandboxSession(
            repository_path=str(root),
            workspace_path=str(root),
            network_enabled=False,
        )

    def build_invocation(self, command: Sequence[str], cwd: str = ".") -> tuple[str, ...]:
        relative = Path(cwd)
        if relative.is_absolute() or ".." in relative.parts:
            raise UnsafePathError("Docker working directory escapes the repository")
        container_cwd = Path("/workspace").joinpath(relative).as_posix()
        mount = f"type=bind,src={self.session.workspace_path},dst=/workspace,rw"
        return (
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "256",
            "--memory",
            "1g",
            "--cpus",
            "1.0",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            "--mount",
            mount,
            "--workdir",
            container_cwd,
            self.image,
            *command,
        )

    def execute(
        self,
        command: str | Sequence[str],
        *,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        approved: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> CommandExecution:
        decision = self.policy.classify(command)
        if decision.decision is PolicyDecision.DENY:
            raise CommandDenied(f"{decision.rule}: {decision.reason}")
        if decision.decision is PolicyDecision.REQUIRES_APPROVAL and not approved:
            raise CommandApprovalRequired(f"{decision.rule}: {decision.reason}")
        tokens = _tokens(command)
        invocation = self.build_invocation(tokens, cwd)
        started = time.monotonic()
        process = subprocess.Popen(
            invocation,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
        )
        timed_out = False
        cancelled = False
        timeout = timeout_seconds or self.timeout_seconds
        while process.poll() is None:
            if cancel_event and cancel_event.is_set():
                process.kill()
                cancelled = True
                break
            if time.monotonic() - started >= timeout:
                process.kill()
                timed_out = True
                break
            time.sleep(0.02)
        stdout_bytes, stderr_bytes = process.communicate()
        cap = self.output_limit_bytes
        truncated = len(stdout_bytes) > cap or len(stderr_bytes) > cap
        stdout = redact_output(stdout_bytes[:cap].decode("utf-8", errors="replace"))
        stderr = redact_output(stderr_bytes[:cap].decode("utf-8", errors="replace"))
        exit_code = process.returncode if process.returncode is not None else -1
        status = (
            ValidationStatus.CANCELLED
            if cancelled
            else ValidationStatus.PASSED
            if exit_code == 0 and not timed_out
            else ValidationStatus.FAILED
        )
        return CommandExecution(
            command=tokens,
            exit_code=exit_code,
            duration_seconds=round(time.monotonic() - started, 6),
            stdout=stdout,
            stderr=stderr,
            status=status,
            timed_out=timed_out,
            output_truncated=truncated,
        )

    def close(self) -> None:
        """Docker runs are ephemeral, so no persistent resource remains."""

