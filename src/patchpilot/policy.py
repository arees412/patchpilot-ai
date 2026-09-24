"""Programmatic command governance independent of model instructions."""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass

from patchpilot.models import PolicyDecision

SHELL_META = re.compile(r"(?:&&|\|\||[|;<>`]|\$\(|\r|\n)")


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str
    rule: str


class CommandPolicyEngine:
    """Classify a bounded command before it reaches any execution adapter."""

    def classify(self, command: str | Sequence[str]) -> PolicyResult:
        if isinstance(command, str):
            if SHELL_META.search(command):
                return self._deny("shell composition is not allowed", "deny.shell-meta")
            try:
                tokens = tuple(shlex.split(command, posix=False))
            except ValueError:
                return self._deny("command could not be parsed", "deny.parse")
        else:
            tokens = tuple(str(token) for token in command)
        if not tokens or any("\x00" in token for token in tokens):
            return self._deny("empty or invalid command", "deny.invalid")

        lowered = tuple(token.strip("\"'").lower() for token in tokens)
        executable = lowered[0].replace("\\", "/").rsplit("/", 1)[-1]
        executable = executable.removesuffix(".exe").removesuffix(".cmd").removesuffix(".bat")

        if executable in {"sudo", "su", "ssh", "scp", "ftp", "telnet"}:
            return self._deny("privilege or remote access is denied", "deny.privilege-network")
        if executable in {"curl", "wget", "invoke-webrequest"}:
            return self._deny("arbitrary network download is denied", "deny.network-download")
        if executable in {"rm", "rmdir", "del", "erase", "remove-item"}:
            return self._deny("destructive shell deletion is denied", "deny.delete")
        if executable in {"cmd", "powershell", "pwsh", "bash", "sh", "zsh"}:
            return self._deny("nested shell execution is denied", "deny.shell")

        if executable == "git":
            return self._git(lowered)
        if executable in {"pip", "pip3"}:
            return self._approval("package installation changes dependencies", "approval.package")
        if executable == "uv" and any(
            token in {"add", "remove", "sync", "lock", "pip"} for token in lowered[1:]
        ):
            return self._approval(
                "dependency environment changes require approval", "approval.package"
            )
        if executable in {"npm", "pnpm", "yarn"}:
            return self._node_package(lowered)
        if executable in {"alembic", "prisma", "flyway", "liquibase"}:
            return self._approval("database migration requires approval", "approval.migration")

        if executable == "find" and any(
            token in {"-exec", "-execdir", "-delete"} for token in lowered
        ):
            return self._deny("find execution or deletion is denied", "deny.find-mutation")
        if executable == "rg" and any(
            token == "--pre" or token.startswith("--pre=") for token in lowered
        ):
            return self._deny("ripgrep preprocessors are denied", "deny.rg-preprocessor")

        safe_executables = {
            "ls",
            "dir",
            "find",
            "findstr",
            "rg",
            "grep",
            "pytest",
            "ruff",
            "mypy",
            "pyright",
            "tsc",
            "vitest",
        }
        if executable in safe_executables:
            return self._allow("recognized inspection or validation command", "allow.validation")
        if executable in {"python", "python3", "py"}:
            return self._python(lowered)
        if (
            executable == "node"
            and len(lowered) >= 2
            and lowered[1] in {"--check", "--version", "-v"}
        ):
            return self._allow("bounded Node validation command", "allow.node")
        if (
            executable == "npx"
            and len(lowered) >= 2
            and lowered[1]
            in {
                "vitest",
                "tsc",
                "eslint",
                "prettier",
            }
        ):
            return self._allow("bounded Node validation tool", "allow.node-tool")
        return self._approval(
            "unrecognized executable requires operator review", "approval.unknown"
        )

    def _git(self, tokens: tuple[str, ...]) -> PolicyResult:
        if len(tokens) < 2:
            return self._deny("Git subcommand is required", "deny.git-empty")
        subcommand = tokens[1]
        if subcommand in {"push", "merge", "tag", "rebase", "filter-branch", "filter-repo"}:
            return self._deny(
                "Git publication or history rewriting is disabled", "deny.git-publish"
            )
        if subcommand == "reset" and "--hard" in tokens:
            return self._deny("destructive Git reset is denied", "deny.git-destructive")
        if subcommand == "clean" and any(
            token.startswith("-") and "f" in token for token in tokens[2:]
        ):
            return self._deny("destructive Git clean is denied", "deny.git-destructive")
        if subcommand in {"status", "diff", "show", "log", "rev-parse", "ls-files", "grep"}:
            return self._allow("read-only Git command", "allow.git-read")
        if subcommand in {"switch", "checkout", "worktree", "branch", "add", "commit"}:
            return self._approval(
                "local Git mutation requires explicit configuration", "approval.git-local"
            )
        return self._approval("unrecognized Git operation requires review", "approval.git-unknown")

    def _python(self, tokens: tuple[str, ...]) -> PolicyResult:
        if len(tokens) < 2:
            return self._allow("Python version or REPL invocation", "allow.python")
        if tokens[1:3] in {("-m", "pytest"), ("-m", "compileall"), ("-m", "mypy")}:
            return self._allow("bounded Python validation module", "allow.python-validation")
        if tokens[1] in {"--version", "-v"}:
            return self._allow("Python version query", "allow.python")
        return self._approval("arbitrary Python execution requires approval", "approval.python")

    def _node_package(self, tokens: tuple[str, ...]) -> PolicyResult:
        if len(tokens) == 1:
            return self._allow("package manager version query", "allow.package-query")
        if tokens[1] in {"install", "add", "remove", "update", "upgrade", "ci"}:
            return self._approval("dependency installation requires approval", "approval.package")
        if tokens[1] == "test" or tokens[1:3] in {
            ("run", "test"),
            ("run", "lint"),
            ("run", "typecheck"),
        }:
            return self._allow("declared package validation script", "allow.package-test")
        return self._approval("package script requires review", "approval.package-script")

    @staticmethod
    def _allow(reason: str, rule: str) -> PolicyResult:
        return PolicyResult(PolicyDecision.ALLOW, reason, rule)

    @staticmethod
    def _deny(reason: str, rule: str) -> PolicyResult:
        return PolicyResult(PolicyDecision.DENY, reason, rule)

    @staticmethod
    def _approval(reason: str, rule: str) -> PolicyResult:
        return PolicyResult(PolicyDecision.REQUIRES_APPROVAL, reason, rule)
