"""Task analysis and plan orchestration."""

from __future__ import annotations

from patchpilot.models import EngineeringTask, RepositoryMap, TaskAnalysis, TaskPlan
from patchpilot.provider import AgentModelProvider
from patchpilot.retrieval import ContextRetriever


class TaskPlanner:
    """Keep deterministic retrieval separate from provider reasoning."""

    def __init__(
        self, provider: AgentModelProvider, retriever: ContextRetriever | None = None
    ) -> None:
        self.provider = provider
        self.retriever = retriever or ContextRetriever()

    def analyze(self, task: EngineeringTask, repository_map: RepositoryMap) -> TaskAnalysis:
        context = self.retriever.retrieve(task, repository_map)
        return self.provider.analyze_task(task, repository_map, context)

    def plan(self, task: EngineeringTask, analysis: TaskAnalysis) -> TaskPlan:
        if analysis.task_id != task.id:
            raise ValueError("analysis is not scoped to this task")
        return self.provider.create_plan(task, analysis)
