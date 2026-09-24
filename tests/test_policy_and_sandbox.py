from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from patchpilot.models import PolicyDecision, ValidationStatus
from patchpilot.paths import UnsafePathError, resolve_workspace_path
from patchpilot.policy import CommandPolicyEngine
from patchpilot.sandbox import (
    CommandApprovalRequired,
    CommandDenied,
    DockerSandbox,
    LocalSandbox,
    SandboxError,
    redact_output,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (("pytest", "-q"), PolicyDecision.ALLOW),
        (("git", "status", "--short"), PolicyDecision.ALLOW),
        (("rm", "-rf", "."), PolicyDecision.DENY),
        (("git", "push", "--force"), PolicyDecision.DENY),
        (("npm", "install"), PolicyDecision.REQUIRES_APPROVAL),
        (("python", "script.py"), PolicyDecision.REQUIRES_APPROVAL),
    ],
)
def test_command_policy_matrix(command: tuple[str, ...], expected: PolicyDecision) -> None:
    assert CommandPolicyEngine().classify(command).decision is expected


def test_command_policy_rejects_shell_composition() -> None:
    result = CommandPolicyEngine().classify("pytest -q && curl example.invalid")
    assert result.decision is PolicyDecision.DENY
    assert result.rule == "deny.shell-meta"


@pytest.mark.parametrize("path", ("../outside", "/absolute", ".git/config"))
def test_path_resolution_rejects_escape(git_repo: Path, path: str) -> None:
    with pytest.raises(UnsafePathError):
        resolve_workspace_path(git_repo, path)


def test_local_sandbox_copies_without_mutating_source(git_repo: Path) -> None:
    with LocalSandbox(git_repo, base_directory=git_repo.parent) as sandbox:
        target = sandbox.workspace / "src" / "service.py"
        target.write_text("changed\n", encoding="utf-8")
        assert sandbox.session.network_enabled is False
        assert not (sandbox.workspace / ".git").exists()
    assert "changed" not in (git_repo / "src" / "service.py").read_text(encoding="utf-8")


def test_local_sandbox_enforces_policy(git_repo: Path) -> None:
    with LocalSandbox(git_repo, base_directory=git_repo.parent) as sandbox:
        with pytest.raises(CommandDenied):
            sandbox.execute(("rm", "-rf", "."))
        with pytest.raises(CommandApprovalRequired):
            sandbox.execute(("python", "-c", "print('ok')"))


def test_local_sandbox_redacts_and_limits_output(git_repo: Path) -> None:
    script = "print('token=' + 'a' * 40); print('x' * 1000)"
    with LocalSandbox(
        git_repo,
        base_directory=git_repo.parent,
        output_limit_bytes=100,
    ) as sandbox:
        result = sandbox.execute(("python", "-c", script), approved=True)
    assert result.status is ValidationStatus.PASSED
    assert result.output_truncated
    assert "a" * 40 not in result.stdout
    assert "<redacted>" in result.stdout


def test_local_sandbox_timeout(git_repo: Path) -> None:
    with LocalSandbox(git_repo, base_directory=git_repo.parent) as sandbox:
        result = sandbox.execute(
            ("python", "-c", "import time; time.sleep(2)"),
            approved=True,
            timeout_seconds=0.05,
        )
    assert result.status is ValidationStatus.FAILED
    assert result.timed_out


def test_local_sandbox_cancellation(git_repo: Path) -> None:
    cancelled = threading.Event()
    cancelled.set()
    with LocalSandbox(git_repo, base_directory=git_repo.parent) as sandbox:
        result = sandbox.execute(
            ("python", "-c", "import time; time.sleep(2)"),
            approved=True,
            cancel_event=cancelled,
        )
    assert result.status is ValidationStatus.CANCELLED


def test_environment_allowlist_rejects_secret(git_repo: Path) -> None:
    with pytest.raises(SandboxError, match="not allowed"):
        LocalSandbox(git_repo, environment={"SECRET_TOKEN": "never"})


def test_output_redactor_does_not_return_secret_value() -> None:
    secret = "z" * 32
    redacted = redact_output(f"password={secret} Authorization: Bearer {secret}")
    assert secret not in redacted
    assert redacted.count("<redacted>") >= 2


def test_docker_invocation_has_isolation_defaults(git_repo: Path) -> None:
    with DockerSandbox(git_repo) as sandbox:
        invocation = sandbox.build_invocation(("pytest", "-q"))
        rendered = " ".join(invocation)
        assert sandbox.session.workspace_path != str(git_repo)
        assert not (Path(sandbox.session.workspace_path) / ".git").exists()
        assert "--network none" in rendered
        assert "--read-only" in invocation
        assert "--cap-drop ALL" in rendered
        assert "no-new-privileges=true" in rendered
        assert "--pids-limit 256" in rendered


@pytest.mark.skipif(os.name == "nt", reason="ordinary Windows test users cannot create symlinks")
def test_sandbox_rejects_escaping_symlink(git_repo: Path, tmp_path: Path) -> None:
    (git_repo / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        LocalSandbox(git_repo)
