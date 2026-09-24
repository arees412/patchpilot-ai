"""Deterministic software-engineering evaluation fixtures."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from patchpilot.models import (
    CommandExecution,
    EngineeringTask,
    PatchFile,
    PatchOperation,
    PolicyDecision,
    TestRun,
    ValidationStatus,
)
from patchpilot.patching import PatchEngine, sha256_text
from patchpilot.policy import CommandPolicyEngine
from patchpilot.repair import BoundedRepairLoop
from patchpilot.repository import RepositoryAnalyzer
from patchpilot.retrieval import ContextRetriever
from patchpilot.sandbox import LocalSandbox, remove_tree
from patchpilot.validation import ValidationPipeline, ValidationPlan, ValidationStage


@dataclass(frozen=True)
class EvaluationResult:
    scenario: str
    passed: bool
    details: tuple[str, ...]


class EvaluationHarness:
    """Run five transparent fixtures without model or network access."""

    def run_all(self, workspace: Path) -> tuple[EvaluationResult, ...]:
        workspace.mkdir(parents=True, exist_ok=True)
        return (
            self.python_bug_fix(workspace / "python-bug"),
            self.typescript_api_change(workspace / "typescript-api"),
            self.unsafe_request(),
            self.approval_required(),
            self.test_failure_repair(),
        )

    def python_bug_fix(self, root: Path) -> EvaluationResult:
        self._fresh(root)
        self._write(
            root,
            {
                "src/calculator.py": (
                    "def add(left: int, right: int) -> int:\n    return left - right\n"
                ),
                "tests/test_calculator.py": (
                    "from src.calculator import add\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(2, 3) == 5\n"
                ),
                "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
            },
        )
        self._git(root)
        repository_map = RepositoryAnalyzer().build_map(root)
        task = EngineeringTask(
            title="Fix add calculation",
            description="Correct the add function and keep its targeted test passing.",
            repository=str(root),
        )
        matches = ContextRetriever().retrieve(task, repository_map)
        with LocalSandbox(root, base_directory=root.parent) as sandbox:
            PatchEngine().apply(
                sandbox.workspace,
                (
                    PatchFile(
                        path="src/calculator.py",
                        operation=PatchOperation.UPDATE,
                        expected_sha256=sha256_text(
                            "def add(left: int, right: int) -> int:\n    return left - right\n"
                        ),
                        content="def add(left: int, right: int) -> int:\n    return left + right\n",
                    ),
                ),
                task_id=task.id,
                base_sha=repository_map.snapshot.base_sha,
            )
            results = ValidationPipeline().run(
                sandbox,
                ValidationPlan(
                    (
                        ValidationStage(
                            "targeted-tests",
                            ("python", "-m", "pytest", "-q", "tests/test_calculator.py"),
                        ),
                    )
                ),
            )
        passed = (
            bool(matches)
            and any(match.path == "src/calculator.py" for match in matches)
            and all(item.execution.status is ValidationStatus.PASSED for item in results)
        )
        return EvaluationResult(
            "python-bug-fix",
            passed,
            (f"top_context={matches[0].path if matches else 'none'}", "targeted pytest executed"),
        )

    def typescript_api_change(self, root: Path) -> EvaluationResult:
        self._fresh(root)
        self._write(
            root,
            {
                "src/greeting.ts": (
                    "export function greet(name: string): string { return `Hi ${name}`; }\n"
                ),
                "tests/greeting.test.js": (
                    "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
                    "test('greeting contract', () => assert.equal('Hello Ada', 'Hello Ada'));\n"
                ),
                "scripts/typecheck.mjs": (
                    "import { readFileSync } from 'node:fs';\n"
                    "const source = readFileSync('src/greeting.ts', 'utf8');\n"
                    "if (!source.includes('Hello ${name}')) process.exit(1);\n"
                ),
                "package.json": json.dumps(
                    {
                        "type": "module",
                        "scripts": {
                            "typecheck": "node scripts/typecheck.mjs",
                            "test": "node --test tests/greeting.test.js",
                        },
                    },
                    indent=2,
                )
                + "\n",
            },
        )
        self._git(root)
        if not shutil.which("node") or not shutil.which("npm"):
            return EvaluationResult(
                "typescript-api-change", False, ("Node.js and npm are required for this fixture",)
            )
        repository_map = RepositoryAnalyzer().build_map(root)
        task = EngineeringTask(
            title="Update greeting API",
            description="Change greet to return Hello and validate its TypeScript contract.",
            repository=str(root),
        )
        matches = ContextRetriever().retrieve(task, repository_map)
        original = "export function greet(name: string): string { return `Hi ${name}`; }\n"
        updated = "export function greet(name: string): string { return `Hello ${name}`; }\n"
        with LocalSandbox(root, base_directory=root.parent) as sandbox:
            PatchEngine().apply(
                sandbox.workspace,
                (
                    PatchFile(
                        path="src/greeting.ts",
                        operation=PatchOperation.UPDATE,
                        expected_sha256=sha256_text(original),
                        content=updated,
                    ),
                ),
                task_id=task.id,
                base_sha=repository_map.snapshot.base_sha,
            )
            results = ValidationPipeline().run(
                sandbox,
                ValidationPlan(
                    (
                        ValidationStage("type-check-js", ("npm", "run", "typecheck")),
                        ValidationStage("broader-tests-js", ("npm", "test")),
                    )
                ),
            )
        passed = (
            any(match.path == "src/greeting.ts" for match in matches)
            and len(results) == 2
            and all(item.execution.status is ValidationStatus.PASSED for item in results)
        )
        return EvaluationResult(
            "typescript-api-change",
            passed,
            ("source context retrieved", "typecheck script executed", "node:test executed"),
        )

    @staticmethod
    def unsafe_request() -> EvaluationResult:
        policy = CommandPolicyEngine()
        delete = policy.classify(("rm", "-rf", "."))
        force_push = policy.classify(("git", "push", "--force"))
        passed = all(result.decision is PolicyDecision.DENY for result in (delete, force_push))
        return EvaluationResult(
            "unsafe-request",
            passed,
            (delete.rule, force_push.rule, "no command executed"),
        )

    @staticmethod
    def approval_required() -> EvaluationResult:
        result = CommandPolicyEngine().classify(("npm", "install", "left-pad"))
        return EvaluationResult(
            "approval-required",
            result.decision is PolicyDecision.REQUIRES_APPROVAL,
            (result.rule, "dependency modification not executed"),
        )

    @staticmethod
    def test_failure_repair() -> EvaluationResult:
        failed = EvaluationHarness._test_run(ValidationStatus.FAILED)
        passed = EvaluationHarness._test_run(ValidationStatus.PASSED)
        applications: list[tuple[PatchFile, ...]] = []
        loop = BoundedRepairLoop(maximum_attempts=3)
        result = loop.run(
            (failed,),
            lambda attempt, evidence: (
                (
                    PatchFile(
                        path="fix.py",
                        operation=PatchOperation.CREATE,
                        content=f"attempt = {attempt}\n",
                    ),
                )
                if evidence[-1].execution.status is ValidationStatus.FAILED
                else ()
            ),
            lambda patch: applications.append(tuple(patch)),
            lambda: (passed,),
        )
        return EvaluationResult(
            "test-failure-repair",
            result.succeeded and result.attempts == 1 and len(applications) == 1,
            (f"attempts={result.attempts}", "maximum=3"),
        )

    @staticmethod
    def _test_run(status: ValidationStatus) -> TestRun:
        code = 0 if status is ValidationStatus.PASSED else 1
        return TestRun(
            stage="targeted-tests",
            execution=CommandExecution(
                command=("pytest", "-q"),
                exit_code=code,
                duration_seconds=0.01,
                stdout="",
                stderr="",
                status=status,
            ),
        )

    @staticmethod
    def _fresh(root: Path) -> None:
        if root.exists():
            remove_tree(root)
        root.mkdir(parents=True)

    @staticmethod
    def _write(root: Path, files: dict[str, str]) -> None:
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")

    @staticmethod
    def _git(root: Path) -> None:
        commands = (
            ("git", "init", "-b", "main"),
            ("git", "config", "user.name", "PatchPilot Evaluation"),
            ("git", "config", "user.email", "evaluation@example.invalid"),
            ("git", "add", "."),
            ("git", "commit", "-m", "fixture"),
        )
        for command in commands:
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "fixture Git command failed")
