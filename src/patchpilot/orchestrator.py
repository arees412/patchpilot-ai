"""Governed orchestration across planning, sandboxing, validation, and evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from patchpilot.approvals import ApprovalError, ApprovalManager
from patchpilot.cancellation import CancellationToken
from patchpilot.evidence import AuditTrail, EvidenceBuilder, digest
from patchpilot.models import (
    AgentRun,
    AgentState,
    ApprovalRequest,
    EngineeringTask,
    EvidenceRecord,
    PatchCandidate,
    PatchFile,
    RepositoryMap,
    RiskAssessment,
    TaskAnalysis,
    TaskPlan,
    TestRun,
    ValidationStatus,
    utc_now,
)
from patchpilot.observability import RunObserver
from patchpilot.patching import PatchEngine
from patchpilot.persistence import SQLiteStore
from patchpilot.planning import TaskPlanner
from patchpilot.provider import AgentModelProvider
from patchpilot.repository import RepositoryAnalyzer
from patchpilot.review import DiffReviewEngine, ReviewFinding
from patchpilot.risk import (
    DEPENDENCY_FILES,
    INFRA_TERMS,
    MIGRATION_TERMS,
    SECURITY_TERMS,
    RiskEngine,
)
from patchpilot.sandbox import LocalSandbox
from patchpilot.state_machine import AgentStateMachine
from patchpilot.validation import TestPlanner, ValidationPipeline, ValidationPlan


@dataclass(frozen=True)
class PreparedRun:
    run: AgentRun
    task: EngineeringTask
    repository_map: RepositoryMap
    analysis: TaskAnalysis
    plan: TaskPlan


@dataclass(frozen=True)
class ExecutionOutcome:
    run: AgentRun
    patch: PatchCandidate | None = None
    unified_diff: str = ""
    validations: tuple[TestRun, ...] = ()
    risk: RiskAssessment | None = None
    findings: tuple[ReviewFinding, ...] = ()
    approval: ApprovalRequest | None = None
    evidence: tuple[EvidenceRecord, ...] = ()


class PatchPilotCoordinator:
    """Coordinate a run without ever modifying the intake repository."""

    def __init__(
        self,
        provider: AgentModelProvider,
        store: SQLiteStore,
        *,
        sandbox_base_directory: Path | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.repository_analyzer = RepositoryAnalyzer()
        self.task_planner = TaskPlanner(provider)
        self.patch_engine = PatchEngine()
        self.test_planner = TestPlanner()
        self.validation = ValidationPipeline()
        self.risk_engine = RiskEngine()
        self.review_engine = DiffReviewEngine()
        self.approvals = ApprovalManager(self.store.save_approval)
        self.audit = AuditTrail()
        self.evidence = EvidenceBuilder()
        self.sandbox_base_directory = sandbox_base_directory

    def prepare(self, task: EngineeringTask) -> PreparedRun:
        self.store.save_task(task)
        observer = RunObserver(provider=self.provider.name)
        run = AgentRun(task_id=task.id)
        self.store.save_run(run)
        self._audit(run.id, "task_created", task.model_dump(mode="json"))
        machine = self._machine(run)
        machine.transition(AgentState.ANALYZING)
        with observer.phase("analysis"):
            repository_map = self.repository_analyzer.build_map(Path(task.repository))
            if task.base_revision and task.base_revision != repository_map.snapshot.base_sha:
                machine.transition(AgentState.FAILED)
                self._run(run, machine.state, observer)
                raise ValueError("repository HEAD no longer matches the task base revision")
            if task.base_revision is None:
                task = task.model_copy(update={"base_revision": repository_map.snapshot.base_sha})
                self.store.save_task(task)
            analysis = self.task_planner.analyze(task, repository_map)
            self.store.save_analysis(analysis)
        self._audit(run.id, "analysis", analysis.model_dump(mode="json"))
        if analysis.blocked:
            machine.transition(AgentState.FAILED)
            run = self._run(run, machine.state, observer)
            return PreparedRun(
                run, task, repository_map, analysis, TaskPlan(task_id=task.id, steps=())
            )
        machine.transition(AgentState.PLANNING)
        with observer.phase("planning"):
            plan = self.task_planner.plan(task, analysis)
            self.store.save_plan(plan)
        self._audit(run.id, "plan", plan.model_dump(mode="json"))
        run = self._run(run, machine.state, observer, plan_id=plan.id)
        return PreparedRun(run, task, repository_map, analysis, plan)

    def execute(
        self,
        prepared: PreparedRun,
        operations: tuple[PatchFile, ...],
        *,
        approval_id: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionOutcome:
        cancellation = cancellation or CancellationToken()
        observer = RunObserver(provider=self.provider.name)
        run = prepared.run
        machine = self._machine(run, initial=AgentState.PLANNING)
        operation_hash = digest([item.model_dump(mode="json") for item in operations])
        if self._operations_require_approval(operations):
            machine.transition(AgentState.AWAITING_APPROVAL)
            approval = self._authorize_or_request(
                prepared,
                action="apply sensitive plan",
                patch_hash=operation_hash,
                approval_id=approval_id,
            )
            if approval.state.value != "approved":
                run = self._run(run, machine.state, observer)
                return ExecutionOutcome(run=run, approval=approval)
            machine.transition(AgentState.PREPARING_WORKSPACE)
        else:
            machine.transition(AgentState.PREPARING_WORKSPACE)

        if cancellation.cancelled:
            machine.transition(AgentState.CANCELLED)
            return ExecutionOutcome(run=self._run(run, machine.state, observer))

        evidence: list[EvidenceRecord] = []
        with (
            observer.phase("sandbox"),
            LocalSandbox(
                Path(prepared.task.repository), base_directory=self.sandbox_base_directory
            ) as sandbox,
        ):
            machine.transition(AgentState.EDITING)
            applied = self.patch_engine.apply(
                sandbox.workspace,
                operations,
                task_id=prepared.task.id,
                base_sha=prepared.repository_map.snapshot.base_sha,
                allowed_paths=set(prepared.analysis.likely_files + prepared.analysis.likely_tests)
                or None,
            )
            self._audit(
                run.id,
                "patch",
                {
                    "files": applied.candidate.files_changed,
                    "diff_sha256": applied.candidate.diff_sha256,
                },
            )
            observer.increment("files_changed", len(applied.candidate.files_changed))
            observer.increment(
                "lines_changed", applied.candidate.insertions + applied.candidate.deletions
            )
            validation_plan = self.test_planner.plan(
                prepared.repository_map, likely_tests=prepared.analysis.likely_tests
            )
            static_plan, test_plan = self._split_validation(validation_plan)
            machine.transition(AgentState.VALIDATING)
            static_results = self.validation.run(
                sandbox, static_plan, cancel_event=cancellation.event
            )
            self._record_validations(run.id, static_results, observer)
            if cancellation.cancelled or self._has_cancelled(static_results):
                machine.transition(AgentState.CANCELLED)
                evidence.extend(
                    self._termination_evidence(
                        prepared,
                        applied.candidate,
                        applied.unified_diff,
                        static_results,
                        "cancelled",
                    )
                )
                self._persist_evidence(run.id, evidence)
                run = self._run(run, machine.state, observer)
                return ExecutionOutcome(
                    run=run,
                    patch=applied.candidate,
                    unified_diff=applied.unified_diff,
                    validations=static_results,
                    evidence=tuple(evidence),
                )
            if not self._passed(static_results):
                machine.transition(AgentState.FAILED)
                evidence.extend(
                    self._termination_evidence(
                        prepared,
                        applied.candidate,
                        applied.unified_diff,
                        static_results,
                        "static validation failed",
                    )
                )
                self._persist_evidence(run.id, evidence)
                run = self._run(run, machine.state, observer)
                return ExecutionOutcome(
                    run=run,
                    patch=applied.candidate,
                    unified_diff=applied.unified_diff,
                    validations=static_results,
                    evidence=tuple(evidence),
                )
            machine.transition(AgentState.TESTING)
            test_results = self.validation.run(sandbox, test_plan, cancel_event=cancellation.event)
            self._record_validations(run.id, test_results, observer)
            validations = static_results + test_results
            if cancellation.cancelled or self._has_cancelled(test_results):
                machine.transition(AgentState.CANCELLED)
                evidence.extend(
                    self._termination_evidence(
                        prepared,
                        applied.candidate,
                        applied.unified_diff,
                        validations,
                        "cancelled",
                    )
                )
                self._persist_evidence(run.id, evidence)
                run = self._run(run, machine.state, observer)
                return ExecutionOutcome(
                    run=run,
                    patch=applied.candidate,
                    unified_diff=applied.unified_diff,
                    validations=validations,
                    evidence=tuple(evidence),
                )
            if test_results and not self._passed(test_results):
                machine.transition(AgentState.FAILED)
                evidence.extend(
                    self._termination_evidence(
                        prepared,
                        applied.candidate,
                        applied.unified_diff,
                        validations,
                        "tests failed",
                    )
                )
                self._persist_evidence(run.id, evidence)
                run = self._run(run, machine.state, observer)
                return ExecutionOutcome(
                    run=run,
                    patch=applied.candidate,
                    unified_diff=applied.unified_diff,
                    validations=validations,
                    evidence=tuple(evidence),
                )

            candidate = applied.candidate.model_copy(
                update={
                    "tests_run": validations,
                    "validation_state": ValidationStatus.PASSED,
                }
            )
            machine.transition(AgentState.REVIEWING_PATCH)
            changed_content = {
                path: (sandbox.workspace / path).read_text(encoding="utf-8")
                for path in candidate.files_changed
                if (sandbox.workspace / path).exists()
            }
            findings = self.review_engine.review(applied.unified_diff, changed_content)
            risk = self.risk_engine.assess(candidate, applied.unified_diff)
            candidate = candidate.model_copy(update={"risk_score": risk.score})
            self._audit(
                run.id,
                "patch_review",
                {
                    "risk": risk.model_dump(mode="json"),
                    "findings": [finding.__dict__ for finding in findings],
                },
            )
            if risk.requires_approval:
                machine.transition(AgentState.AWAITING_APPROVAL)
                approval = self._authorize_or_request(
                    prepared,
                    action="accept reviewed patch",
                    patch_hash=candidate.diff_sha256,
                    approval_id=approval_id,
                )
                if approval.state.value != "approved":
                    run = self._run(run, machine.state, observer)
                    return ExecutionOutcome(
                        run=run,
                        patch=candidate,
                        unified_diff=applied.unified_diff,
                        validations=validations,
                        risk=risk,
                        findings=findings,
                        approval=approval,
                    )
                machine.transition(AgentState.READY)
            else:
                machine.transition(AgentState.READY)

            evidence.extend(
                self._evidence_records(
                    prepared,
                    candidate,
                    applied.unified_diff,
                    validations,
                    risk,
                    findings,
                )
            )
            self._persist_evidence(run.id, evidence)
        run = self._run(run, machine.state, observer, patch_id=candidate.id)
        return ExecutionOutcome(
            run=run,
            patch=candidate,
            unified_diff=applied.unified_diff,
            validations=validations,
            risk=risk,
            findings=findings,
            evidence=tuple(evidence),
        )

    def _machine(self, run: AgentRun, initial: AgentState | None = None) -> AgentStateMachine:
        return AgentStateMachine(
            initial=initial or run.state,
            on_transition=lambda previous, next_state: self.store.record_transition(
                run.id, previous, next_state
            ),
        )

    def _run(
        self,
        run: AgentRun,
        state: AgentState,
        observer: RunObserver,
        **updates: Any,
    ) -> AgentRun:
        updated = run.model_copy(
            update={
                "state": state,
                "metrics": observer.snapshot(),
                "updated_at": utc_now(),
                **updates,
            }
        )
        self.store.save_run(updated)
        event_type = (
            "final_status"
            if state in {AgentState.READY, AgentState.FAILED, AgentState.CANCELLED}
            else "run_state"
        )
        self._audit(updated.id, event_type, {"state": state.value})
        return updated

    def _authorize_or_request(
        self,
        prepared: PreparedRun,
        *,
        action: str,
        patch_hash: str,
        approval_id: str | None,
    ) -> ApprovalRequest:
        if approval_id:
            try:
                approval = self.approvals.require(
                    approval_id,
                    task_id=prepared.task.id,
                    plan_id=prepared.plan.id,
                    action=action,
                    patch_hash=patch_hash,
                )
                self._audit(
                    prepared.run.id,
                    "approval",
                    {"approval_id": approval.id, "state": approval.state.value},
                )
                return approval
            except ApprovalError:
                pass
        approval = self.approvals.request(
            task_id=prepared.task.id,
            plan_id=prepared.plan.id,
            action=action,
            patch_hash=patch_hash,
        )
        self._audit(
            prepared.run.id,
            "approval",
            {"approval_id": approval.id, "state": approval.state.value},
        )
        return approval

    @staticmethod
    def _operations_require_approval(operations: tuple[PatchFile, ...]) -> bool:
        paths = " ".join(item.path.lower() for item in operations)
        names = {Path(item.path).name.lower() for item in operations}
        terms = (*SECURITY_TERMS, *MIGRATION_TERMS, *INFRA_TERMS)
        return (
            bool(names & DEPENDENCY_FILES)
            or any(term in paths for term in terms)
            or any(item.operation.value == "delete" for item in operations)
        )

    @staticmethod
    def _split_validation(plan: ValidationPlan) -> tuple[ValidationPlan, ValidationPlan]:
        static = tuple(stage for stage in plan.stages if "test" not in stage.name)
        tests = tuple(stage for stage in plan.stages if "test" in stage.name)
        return ValidationPlan(static), ValidationPlan(tests)

    @staticmethod
    def _passed(results: tuple[TestRun, ...]) -> bool:
        return all(result.execution.status is ValidationStatus.PASSED for result in results)

    def _audit(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        self.store.append_audit(self.audit.event(run_id, event_type, data))

    def _record_validations(
        self, run_id: str, results: tuple[TestRun, ...], observer: RunObserver
    ) -> None:
        for result in results:
            self.store.save_validation(run_id, result)
            self._audit(
                run_id,
                "command",
                {
                    "stage": result.stage,
                    "command": result.execution.command,
                    "exit_code": result.execution.exit_code,
                    "duration_seconds": result.execution.duration_seconds,
                    "status": result.execution.status.value,
                },
            )
        observer.increment("command_count", len(results))
        observer.increment("test_count", sum("test" in result.stage for result in results))
        observer.increment(
            "validation_failures",
            sum(result.execution.status is ValidationStatus.FAILED for result in results),
        )

    @staticmethod
    def _has_cancelled(results: tuple[TestRun, ...]) -> bool:
        return any(result.execution.status is ValidationStatus.CANCELLED for result in results)

    def _persist_evidence(self, run_id: str, records: list[EvidenceRecord]) -> None:
        for record in records:
            self.store.save_evidence(run_id, record)

    def _termination_evidence(
        self,
        prepared: PreparedRun,
        patch: PatchCandidate,
        unified_diff: str,
        validations: tuple[TestRun, ...],
        reason: str,
    ) -> tuple[EvidenceRecord, ...]:
        return (
            self.evidence.record(
                "base",
                {"base_sha": prepared.repository_map.snapshot.base_sha},
            ),
            self.evidence.record("plan", prepared.plan.model_dump(mode="json")),
            self.evidence.record(
                "patch",
                {
                    "files": patch.files_changed,
                    "diff_sha256": patch.diff_sha256,
                },
            ),
            self.evidence.record(
                "diff",
                {"diff_sha256": patch.diff_sha256, "unified_diff": unified_diff},
            ),
            self.evidence.record(
                "validation",
                {"results": [item.model_dump(mode="json") for item in validations]},
            ),
            self.evidence.record("termination", {"reason": reason}),
        )

    def _evidence_records(
        self,
        prepared: PreparedRun,
        patch: PatchCandidate,
        unified_diff: str,
        validations: tuple[TestRun, ...],
        risk: RiskAssessment,
        findings: tuple[ReviewFinding, ...],
    ) -> tuple[EvidenceRecord, ...]:
        return (
            self.evidence.record(
                "base",
                {
                    "base_sha": prepared.repository_map.snapshot.base_sha,
                    "branch": prepared.repository_map.snapshot.branch,
                },
            ),
            self.evidence.record("plan", prepared.plan.model_dump(mode="json")),
            self.evidence.record(
                "patch",
                {
                    "files": patch.files_changed,
                    "diff_sha256": patch.diff_sha256,
                    "insertions": patch.insertions,
                    "deletions": patch.deletions,
                },
            ),
            self.evidence.record(
                "diff",
                {"diff_sha256": patch.diff_sha256, "unified_diff": unified_diff},
            ),
            self.evidence.record(
                "validation",
                {"results": [item.model_dump(mode="json") for item in validations]},
            ),
            self.evidence.record("risk", risk.model_dump(mode="json")),
            self.evidence.record("review", {"findings": [item.__dict__ for item in findings]}),
        )
