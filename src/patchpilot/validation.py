"""Test discovery, planning, and ordered validation execution."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from patchpilot.models import RepositoryMap, TestRun, ValidationStatus
from patchpilot.sandbox import SandboxAdapter


@dataclass(frozen=True)
class ValidationStage:
    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class ValidationPlan:
    stages: tuple[ValidationStage, ...]


class TestPlanner:
    """Create a focused validation plan from repository metadata."""

    def plan(
        self,
        repository_map: RepositoryMap,
        *,
        likely_tests: tuple[str, ...] = (),
    ) -> ValidationPlan:
        stages: list[ValidationStage] = []
        languages = repository_map.snapshot.language_breakdown
        manifests = set(repository_map.snapshot.package_manifests)

        if "Python" in languages:
            stages.extend(
                (
                    ValidationStage("syntax", ("python", "-m", "compileall", "-q", ".")),
                    ValidationStage("format", ("ruff", "format", "--check", ".")),
                    ValidationStage("lint", ("ruff", "check", ".")),
                    ValidationStage("type-check", ("mypy", "src")),
                )
            )
            python_tests = tuple(path for path in likely_tests if path.endswith(".py"))
            if python_tests:
                stages.append(ValidationStage("targeted-tests", ("pytest", "-q", *python_tests)))
            if "pytest" in repository_map.snapshot.test_frameworks:
                stages.append(ValidationStage("broader-tests", ("pytest", "-q")))

        if "package.json" in manifests:
            scripts = self._package_scripts(Path(repository_map.snapshot.repository_path))
            if "typecheck" in scripts:
                stages.append(ValidationStage("type-check-js", ("npm", "run", "typecheck")))
            js_tests = tuple(
                path
                for path in likely_tests
                if path.endswith((".js", ".jsx", ".ts", ".tsx"))
            )
            if js_tests and "vitest" in repository_map.snapshot.test_frameworks:
                stages.append(ValidationStage("targeted-tests-js", ("npx", "vitest", "run", *js_tests)))
            if "npm-test" in repository_map.snapshot.test_frameworks:
                stages.append(ValidationStage("broader-tests-js", ("npm", "test")))
        return ValidationPlan(tuple(stages))

    @staticmethod
    def _package_scripts(root: Path) -> dict[str, str]:
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        scripts = package.get("scripts", {})
        return scripts if isinstance(scripts, dict) else {}


class ValidationPipeline:
    """Run planned stages in order with captured, sanitized evidence."""

    def run(
        self,
        sandbox: SandboxAdapter,
        plan: ValidationPlan,
        *,
        cancel_event: threading.Event | None = None,
        stop_on_failure: bool = True,
    ) -> tuple[TestRun, ...]:
        results: list[TestRun] = []
        for stage in plan.stages:
            if cancel_event and cancel_event.is_set():
                break
            execution = sandbox.execute(stage.command, cancel_event=cancel_event)
            results.append(TestRun(stage=stage.name, execution=execution))
            if stop_on_failure and execution.status is not ValidationStatus.PASSED:
                break
        return tuple(results)
