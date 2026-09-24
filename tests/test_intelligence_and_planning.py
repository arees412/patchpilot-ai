from __future__ import annotations

from pathlib import Path

import pytest

from patchpilot.models import AgentState, EngineeringTask
from patchpilot.planning import TaskPlanner
from patchpilot.provider import DeterministicAgentProvider
from patchpilot.repository import RepositoryAnalyzer, RepositoryIntakeError
from patchpilot.retrieval import ContextRetriever
from patchpilot.state_machine import AgentStateMachine, InvalidStateTransition


def test_repository_snapshot_is_commit_anchored(git_repo: Path) -> None:
    snapshot = RepositoryAnalyzer().snapshot(git_repo)
    assert len(snapshot.base_sha) == 40
    assert snapshot.branch == "main"
    assert snapshot.language_breakdown["Python"] == 2
    assert snapshot.test_frameworks == ("pytest",)


def test_repository_map_extracts_symbols_and_tests(git_repo: Path) -> None:
    repository_map = RepositoryAnalyzer().build_map(git_repo)
    assert any(symbol.name == "greet" for symbol in repository_map.symbols)
    assert any(
        item.path == "tests/test_service.py" and item.is_test for item in repository_map.files
    )
    assert "pyproject.toml" in repository_map.snapshot.package_manifests


def test_repository_intake_rejects_non_git_directory(tmp_path: Path) -> None:
    with pytest.raises(RepositoryIntakeError, match="Git working tree"):
        RepositoryAnalyzer().snapshot(tmp_path)


def test_context_retrieval_is_deterministic_and_explained(git_repo: Path) -> None:
    repository_map = RepositoryAnalyzer().build_map(git_repo)
    task = EngineeringTask(
        title="Change greet response",
        description="Update the greet symbol and its test.",
        repository=str(git_repo),
    )
    first = ContextRetriever().retrieve(task, repository_map)
    second = ContextRetriever().retrieve(task, repository_map)
    assert first == second
    assert first[0].path == "src/service.py"
    assert first[0].reasons


def test_deterministic_provider_builds_scoped_plan(git_repo: Path) -> None:
    repository_map = RepositoryAnalyzer().build_map(git_repo)
    task = EngineeringTask(
        title="Change greet response",
        description="Update greet and validate the related test.",
        repository=str(git_repo),
    )
    planner = TaskPlanner(DeterministicAgentProvider())
    analysis = planner.analyze(task, repository_map)
    plan = planner.plan(task, analysis)
    assert analysis.task_id == task.id
    assert analysis.likely_files
    assert plan.task_id == task.id
    assert plan.steps[0].validation


def test_planner_rejects_cross_task_analysis(git_repo: Path) -> None:
    repository_map = RepositoryAnalyzer().build_map(git_repo)
    planner = TaskPlanner(DeterministicAgentProvider())
    first = EngineeringTask(title="One", description="One", repository=str(git_repo))
    second = EngineeringTask(title="Two", description="Two", repository=str(git_repo))
    analysis = planner.analyze(first, repository_map)
    with pytest.raises(ValueError, match="not scoped"):
        planner.plan(second, analysis)


def test_state_machine_records_valid_transitions() -> None:
    transitions: list[tuple[AgentState, AgentState]] = []
    machine = AgentStateMachine(
        on_transition=lambda before, after: transitions.append((before, after))
    )
    machine.transition(AgentState.ANALYZING)
    machine.transition(AgentState.PLANNING)
    assert machine.state is AgentState.PLANNING
    assert transitions[-1] == (AgentState.ANALYZING, AgentState.PLANNING)


def test_state_machine_rejects_silent_jump() -> None:
    machine = AgentStateMachine()
    with pytest.raises(InvalidStateTransition, match="cannot transition"):
        machine.transition(AgentState.READY)
