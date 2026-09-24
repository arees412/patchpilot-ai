"""Provider-neutral agent reasoning interfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, HttpUrl, SecretStr

from patchpilot.models import (
    ContextMatch,
    EngineeringTask,
    PatchFile,
    PlanStep,
    RepositoryMap,
    RiskLevel,
    TaskAnalysis,
    TaskPlan,
)


class AgentModelProvider(Protocol):
    """Contract for task analysis, planning, patch proposal, and review."""

    name: str

    def analyze_task(
        self,
        task: EngineeringTask,
        repository_map: RepositoryMap,
        context: Sequence[ContextMatch],
    ) -> TaskAnalysis: ...

    def create_plan(self, task: EngineeringTask, analysis: TaskAnalysis) -> TaskPlan: ...

    def propose_patch(self, task: EngineeringTask, plan: TaskPlan) -> tuple[PatchFile, ...]: ...

    def review_patch(self, unified_diff: str) -> tuple[str, ...]: ...


class ProviderUnavailable(RuntimeError):
    """Raised when a configured provider cannot be used safely."""


class DeterministicAgentProvider:
    """Offline provider with transparent rules for CI and evaluation."""

    name = "deterministic"

    def __init__(self, patches: Mapping[str, Sequence[PatchFile]] | None = None) -> None:
        self._patches = {key: tuple(value) for key, value in (patches or {}).items()}

    def analyze_task(
        self,
        task: EngineeringTask,
        repository_map: RepositoryMap,
        context: Sequence[ContextMatch],
    ) -> TaskAnalysis:
        likely_files = tuple(match.path for match in context if not self._is_test(match.path))
        likely_tests = tuple(match.path for match in context if self._is_test(match.path))
        combined = f"{task.title} {task.description}".lower()
        security_terms = {"auth", "security", "secret", "credential", "permission"}
        migration_terms = {"migration", "schema", "database"}
        confidence = min(0.95, 0.45 + (0.1 * len(context))) if context else 0.3
        unknowns = () if context else ("No matching repository context was found.",)
        affected = tuple(sorted({path.split("/", 1)[0] for path in likely_files + likely_tests}))
        return TaskAnalysis(
            task_id=task.id,
            objective=task.title.strip(),
            affected_area=affected,
            likely_files=likely_files,
            likely_tests=likely_tests,
            constraints=(
                "Only modify files within the sandbox workspace.",
                "Validate changed behavior before marking the patch ready.",
            ),
            unknowns=unknowns,
            security_impact=(
                "review required" if security_terms & set(combined.split()) else "none identified"
            ),
            migration_impact=(
                "review required" if migration_terms & set(combined.split()) else "none identified"
            ),
            confidence=confidence,
            blocked=confidence < 0.5,
        )

    def create_plan(self, task: EngineeringTask, analysis: TaskAnalysis) -> TaskPlan:
        if analysis.blocked:
            raise ProviderUnavailable("task analysis is blocked pending clarification")
        scope = analysis.likely_files + analysis.likely_tests
        steps = (
            PlanStep(
                index=1,
                objective="Confirm the affected implementation and test scope.",
                files_or_symbols=scope,
                expected_modification="No edit; verify the retrieved evidence.",
                validation=("repository context remains consistent with the task",),
            ),
            PlanStep(
                index=2,
                objective=task.title,
                files_or_symbols=analysis.likely_files,
                expected_modification="Apply the configured deterministic patch.",
                validation=(
                    "patch hash matches the inspected base",
                    "changed files remain in scope",
                ),
                dependencies=(1,),
                risk=(
                    RiskLevel.HIGH
                    if analysis.security_impact == "review required"
                    else RiskLevel.LOW
                ),
            ),
            PlanStep(
                index=3,
                objective="Validate the proposed change.",
                files_or_symbols=analysis.likely_tests,
                expected_modification="No edit; run ordered static checks and tests.",
                validation=("syntax", "lint", "types", "targeted tests", "diff review"),
                dependencies=(2,),
            ),
        )
        return TaskPlan(task_id=task.id, steps=steps)

    def propose_patch(self, task: EngineeringTask, plan: TaskPlan) -> tuple[PatchFile, ...]:
        if plan.task_id != task.id:
            raise ValueError("plan is not scoped to this task")
        return self._patches.get(task.id, ())

    def review_patch(self, unified_diff: str) -> tuple[str, ...]:
        findings: list[str] = []
        if not unified_diff.strip():
            findings.append("patch is empty")
        if "TODO" in unified_diff or "FIXME" in unified_diff:
            findings.append("patch adds an unresolved marker")
        return tuple(findings)

    @staticmethod
    def _is_test(path: str) -> bool:
        lowered = path.lower()
        return "test" in lowered or "spec" in lowered


class RemoteProviderConfig(BaseModel):
    """Configuration holder for optional remote adapters.

    PatchPilot does not activate a remote provider implicitly. A caller must
    supply a key at runtime and implement the transport boundary explicitly.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl
    model: str
    api_key: SecretStr


class OpenAICompatibleProvider:
    """Deliberately inert optional adapter boundary."""

    name = "openai-compatible"

    def __init__(self, config: RemoteProviderConfig) -> None:
        self.config = config

    def _unavailable(self) -> ProviderUnavailable:
        return ProviderUnavailable(
            "remote provider transport is optional and is not enabled in the core package"
        )

    def analyze_task(
        self,
        task: EngineeringTask,
        repository_map: RepositoryMap,
        context: Sequence[ContextMatch],
    ) -> TaskAnalysis:
        raise self._unavailable()

    def create_plan(self, task: EngineeringTask, analysis: TaskAnalysis) -> TaskPlan:
        raise self._unavailable()

    def propose_patch(self, task: EngineeringTask, plan: TaskPlan) -> tuple[PatchFile, ...]:
        raise self._unavailable()

    def review_patch(self, unified_diff: str) -> tuple[str, ...]:
        raise self._unavailable()
