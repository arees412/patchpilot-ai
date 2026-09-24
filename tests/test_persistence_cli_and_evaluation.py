from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from patchpilot.cancellation import CancellationToken
from patchpilot.cli import app
from patchpilot.evaluation import EvaluationHarness
from patchpilot.evidence import AuditTrail, EvidenceBuilder, digest, sanitize
from patchpilot.models import AgentRun, AgentState, EngineeringTask
from patchpilot.persistence import SQLiteStore
from patchpilot.planning import TaskPlanner
from patchpilot.provider import DeterministicAgentProvider
from patchpilot.repository import RepositoryAnalyzer
from patchpilot.sandbox import LocalSandbox
from patchpilot.validation import ValidationPipeline, ValidationPlan, ValidationStage


def test_store_round_trips_task_analysis_plan_and_run(git_repo: Path, tmp_path: Path) -> None:
    repository_map = RepositoryAnalyzer().build_map(git_repo)
    task = EngineeringTask(
        title="Change greet", description="Change greet", repository=str(git_repo)
    )
    planner = TaskPlanner(DeterministicAgentProvider())
    analysis = planner.analyze(task, repository_map)
    plan = planner.plan(task, analysis)
    run = AgentRun(task_id=task.id, state=AgentState.PLANNING, plan_id=plan.id)
    with SQLiteStore(tmp_path / "store.db") as store:
        store.save_task(task)
        store.save_analysis(analysis)
        store.save_plan(plan)
        store.save_run(run)
        store.record_transition(run.id, AgentState.ANALYZING, AgentState.PLANNING)
        assert store.fetch_task(task.id) == task
        assert store.fetch_analysis(task.id) == analysis
        assert store.fetch_plan(plan.id) == plan
        assert store.fetch_run(run.id) == run
        assert store.transitions(run.id)[0]["next_state"] == "planning"


def test_evidence_is_sanitized_and_content_addressed(tmp_path: Path) -> None:
    secret = "Q" * 24
    record = EvidenceBuilder().record(
        "command", {"token": secret, "stdout": "password=" + secret, "exit_code": 0}
    )
    assert secret not in repr(record.data)
    assert record.sha256 == digest(record.data)
    bundle_path = tmp_path / "evidence.json"
    bundle = EvidenceBuilder().bundle((record,), output=bundle_path)
    assert bundle_path.exists()
    assert bundle["record_hashes"] == [record.sha256]


def test_audit_trail_redacts_nested_sensitive_values() -> None:
    secret = "R" * 24
    event = AuditTrail().event(
        "run", "provider", {"request": {"authorization": secret}, "message": "token=" + secret}
    )
    assert secret not in repr(event.data)
    assert sanitize({"api_key": secret}) == {"api_key": "<redacted>"}


def test_cancellation_token_is_idempotent() -> None:
    token = CancellationToken()
    token.cancel("first")
    first = token.record
    token.cancel("second")
    assert token.cancelled
    assert token.record == first
    assert token.record and token.record.reason == "first"


def test_cli_help_lists_governed_workflow() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "analyze",
        "plan",
        "run",
        "status",
        "approve",
        "reject",
        "evidence",
        "diff",
        "eval",
    ):
        assert command in result.stdout


def test_cli_analyze_persists_run(git_repo: Path, tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("# Change greeting\nUpdate greet and its test.\n", encoding="utf-8")
    database = tmp_path / "cli.db"
    result = CliRunner().invoke(
        app,
        ["analyze", str(git_repo), "--task", str(task), "--database", str(database)],
    )
    assert result.exit_code == 0, result.stdout
    assert '"state": "planning"' in result.stdout
    assert database.exists()


def test_offline_evaluation_safety_scenarios() -> None:
    harness = EvaluationHarness()
    assert harness.unsafe_request().passed
    assert harness.approval_required().passed
    assert harness.test_failure_repair().passed


def test_validation_pipeline_records_real_failure(git_repo: Path) -> None:
    with LocalSandbox(git_repo, base_directory=git_repo.parent) as sandbox:
        (sandbox.workspace / "invalid.py").write_text("def broken(:\n", encoding="utf-8")
        results = ValidationPipeline().run(
            sandbox,
            ValidationPlan(
                (
                    ValidationStage(
                        "failure",
                        ("python", "-m", "compileall", "-q", "invalid.py"),
                    ),
                )
            ),
        )
    assert len(results) == 1
    assert results[0].execution.status.value == "failed"
    assert results[0].execution.exit_code != 0


def test_deterministic_python_e2e(tmp_path: Path) -> None:
    assert EvaluationHarness().python_bug_fix(tmp_path / "python-e2e").passed


def test_deterministic_typescript_e2e(tmp_path: Path) -> None:
    assert EvaluationHarness().typescript_api_change(tmp_path / "typescript-e2e").passed
