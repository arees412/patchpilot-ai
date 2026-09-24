"""Read-oriented repository host boundary and PR draft generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from patchpilot.models import PatchCandidate, RiskAssessment, TestRun


@dataclass(frozen=True)
class RepositoryMetadata:
    full_name: str
    default_branch: str
    private: bool
    url: str


@dataclass(frozen=True)
class IssueMetadata:
    number: int
    title: str
    body: str
    url: str


@dataclass(frozen=True)
class PullRequestDraft:
    title: str
    body: str
    base: str
    head: str


class RepositoryHostAdapter(Protocol):
    def repository(self, full_name: str) -> RepositoryMetadata: ...

    def issue(self, full_name: str, number: int) -> IssueMetadata: ...


class FakeRepositoryHostAdapter:
    """Deterministic repository host used by CI without GitHub access."""

    def __init__(
        self,
        repositories: Mapping[str, RepositoryMetadata] | None = None,
        issues: Mapping[tuple[str, int], IssueMetadata] | None = None,
    ) -> None:
        self.repositories = dict(repositories or {})
        self.issues = dict(issues or {})

    def repository(self, full_name: str) -> RepositoryMetadata:
        return self.repositories[full_name]

    def issue(self, full_name: str, number: int) -> IssueMetadata:
        return self.issues[(full_name, number)]

    def push(self, *_: object) -> None:
        raise PermissionError("fake host adapter never publishes")

    def merge(self, *_: object) -> None:
        raise PermissionError("fake host adapter never merges")


class PullRequestDraftGenerator:
    """Generate reviewable Markdown without publishing it."""

    def generate(
        self,
        *,
        title: str,
        base: str,
        head: str,
        task_summary: str,
        implementation_summary: Sequence[str],
        patch: PatchCandidate,
        validations: Sequence[TestRun],
        risk: RiskAssessment,
        limitations: Sequence[str],
        rollback: str,
        evidence: Sequence[str],
        references: Sequence[str],
    ) -> PullRequestDraft:
        validation_lines = [
            f"- `{run.stage}`: {run.execution.status.value} "
            f"(`{' '.join(run.execution.command)}`)"
            for run in validations
        ] or ["- No validation results recorded."]
        body = "\n".join(
            (
                "## Task summary",
                task_summary,
                "",
                "## Implementation",
                *(f"- {item}" for item in implementation_summary),
                "",
                "## Changed files",
                *(f"- `{path}`" for path in patch.files_changed),
                "",
                "## Validation",
                *validation_lines,
                "",
                "## Risk and security boundaries",
                f"Deterministic risk category: **{risk.level.value}** ({risk.score}/100).",
                *(f"- {reason}" for reason in risk.reasons),
                "- Git publication and merge are outside the agent core and require operator action.",
                "- Sandboxing reduces risk but does not prove arbitrary repositories are safe.",
                "",
                "## Known limitations",
                *(f"- {item}" for item in limitations),
                "",
                "## Rollback",
                rollback,
                "",
                "## Evidence",
                *(f"- {item}" for item in evidence),
                "",
                "## Architecture references",
                *(f"- {item}" for item in references),
                "",
                "PatchPilot is an independent implementation. No source code or commit history from the "
                "referenced projects is included.",
            )
        )
        return PullRequestDraft(title=title, body=body, base=base, head=head)
