"""PatchPilot command-line interface."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

import typer

from patchpilot.approvals import ApprovalManager
from patchpilot.evaluation import EvaluationHarness
from patchpilot.evidence import EvidenceBuilder
from patchpilot.models import EngineeringTask, PatchFile
from patchpilot.orchestrator import PatchPilotCoordinator, PreparedRun
from patchpilot.persistence import SQLiteStore
from patchpilot.provider import DeterministicAgentProvider
from patchpilot.repository import RepositoryAnalyzer


app = typer.Typer(
    name="patchpilot",
    no_args_is_help=True,
    help="Governed repository analysis, patch validation, approvals, and evidence.",
)


def _database(value: Path | None) -> Path:
    return value or Path(".patchpilot") / "patchpilot.db"


def _emit(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def _task_text(path: Path) -> tuple[str, str]:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise typer.BadParameter("task file is empty")
    lines = content.splitlines()
    title = lines[0].lstrip("# ").strip()
    description = "\n".join(lines[1:]).strip() or title
    return title, description


@app.command()
def analyze(
    repo: Annotated[Path, typer.Argument(help="Local Git repository to analyze")],
    task: Annotated[Path, typer.Option("--task", help="Markdown or text task file")],
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Map a repository, retrieve task context, and persist an engineering plan."""

    title, description = _task_text(task)
    with SQLiteStore(_database(database)) as store:
        coordinator = PatchPilotCoordinator(DeterministicAgentProvider(), store)
        prepared = coordinator.prepare(
            EngineeringTask(
                source="file",
                title=title,
                description=description,
                repository=str(repo.resolve(strict=True)),
            )
        )
        _emit(
            {
                "run_id": prepared.run.id,
                "task_id": prepared.task.id,
                "plan_id": prepared.plan.id,
                "state": prepared.run.state,
                "analysis": prepared.analysis.model_dump(mode="json"),
            }
        )


@app.command()
def plan(
    run_id: Annotated[str, typer.Argument(help="Run identifier from analyze")],
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Show the persisted plan for a run."""

    with SQLiteStore(_database(database)) as store:
        run = store.fetch_run(run_id)
        if not run.plan_id:
            raise typer.BadParameter("run has no plan")
        _emit(store.fetch_plan(run.plan_id).model_dump(mode="json"))


@app.command("run")
def run_agent(
    run_id: Annotated[str, typer.Argument(help="Prepared run identifier")],
    patch: Annotated[Path, typer.Option("--patch", help="JSON array of structured patch files")],
    approval: Annotated[str | None, typer.Option("--approval")] = None,
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Apply a structured patch in a copied sandbox and validate it."""

    operations = tuple(
        PatchFile.model_validate(item)
        for item in json.loads(patch.read_text(encoding="utf-8"))
    )
    with SQLiteStore(_database(database)) as store:
        run = store.fetch_run(run_id)
        if not run.plan_id:
            raise typer.BadParameter("run has no plan")
        task = store.fetch_task(run.task_id)
        repository_map = RepositoryAnalyzer().build_map(Path(task.repository))
        if task.base_revision != repository_map.snapshot.base_sha:
            raise typer.BadParameter("repository HEAD changed after analysis")
        prepared = PreparedRun(
            run=run,
            task=task,
            repository_map=repository_map,
            analysis=store.fetch_analysis(task.id),
            plan=store.fetch_plan(run.plan_id),
        )
        coordinator = PatchPilotCoordinator(DeterministicAgentProvider(), store)
        if approval:
            coordinator.approvals.load(store.fetch_approval(approval))
        outcome = coordinator.execute(prepared, operations, approval_id=approval)
        _emit(
            {
                "run_id": outcome.run.id,
                "state": outcome.run.state,
                "patch_id": outcome.patch.id if outcome.patch else None,
                "approval_id": outcome.approval.id if outcome.approval else None,
                "risk": outcome.risk.model_dump(mode="json") if outcome.risk else None,
                "findings": [finding.__dict__ for finding in outcome.findings],
            }
        )


@app.command()
def status(
    run_id: str,
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Show run state, metrics, and its recorded transition history."""

    with SQLiteStore(_database(database)) as store:
        _emit(
            {
                "run": store.fetch_run(run_id).model_dump(mode="json"),
                "transitions": store.transitions(run_id),
            }
        )


def _decide(approval_id: str, database: Path | None, *, approve: bool) -> None:
    with SQLiteStore(_database(database)) as store:
        manager = ApprovalManager(store.save_approval)
        manager.load(store.fetch_approval(approval_id))
        result = manager.approve(approval_id) if approve else manager.reject(approval_id)
        _emit(result.model_dump(mode="json"))


@app.command()
def approve(
    approval_id: str,
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Approve exactly one persisted task, plan, action, and patch hash."""

    _decide(approval_id, database, approve=True)


@app.command()
def reject(
    approval_id: str,
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Reject exactly one persisted approval request."""

    _decide(approval_id, database, approve=False)


@app.command()
def evidence(
    run_id: str,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Show or write a content-addressed evidence bundle."""

    with SQLiteStore(_database(database)) as store:
        bundle = EvidenceBuilder().bundle(store.evidence(run_id), output=output)
        _emit(bundle)


@app.command("diff")
def show_diff(
    run_id: str,
    database: Annotated[Path | None, typer.Option("--database")] = None,
) -> None:
    """Show the sanitized unified diff recorded for a run."""

    with SQLiteStore(_database(database)) as store:
        records = [record for record in store.evidence(run_id) if record.kind == "diff"]
        if not records:
            raise typer.BadParameter("run has no persisted diff evidence")
        typer.echo(records[-1].data["unified_diff"])


@app.command("eval")
def evaluate(
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Run the deterministic five-scenario software-engineering harness."""

    if workspace:
        results = EvaluationHarness().run_all(workspace)
    else:
        with tempfile.TemporaryDirectory(prefix="patchpilot-eval-") as temporary:
            results = EvaluationHarness().run_all(Path(temporary))
    _emit(
        {
            "passed": all(result.passed for result in results),
            "results": [result.__dict__ for result in results],
        }
    )


if __name__ == "__main__":
    app()
