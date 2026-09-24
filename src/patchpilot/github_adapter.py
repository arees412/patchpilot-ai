"""Read-oriented repository host boundary and PR draft generation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import SecretStr

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


class GitHubAdapterError(RuntimeError):
    """Raised when the optional read-only GitHub boundary fails safely."""


class GitHubRestAdapter:
    """Minimal read-only GitHub REST adapter with no publication methods."""

    _name = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

    def __init__(
        self,
        *,
        token: SecretStr | None = None,
        api_url: str = "https://api.github.com",
        timeout_seconds: float = 10,
    ) -> None:
        if not api_url.startswith("https://"):
            raise ValueError("GitHub API URL must use HTTPS")
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def repository(self, full_name: str) -> RepositoryMetadata:
        payload = self._get(f"/repos/{self._repository_name(full_name)}")
        return RepositoryMetadata(
            full_name=str(payload["full_name"]),
            default_branch=str(payload["default_branch"]),
            private=bool(payload["private"]),
            url=str(payload["html_url"]),
        )

    def issue(self, full_name: str, number: int) -> IssueMetadata:
        if number < 1:
            raise ValueError("issue number must be positive")
        payload = self._get(f"/repos/{self._repository_name(full_name)}/issues/{number}")
        response_number = payload["number"]
        if not isinstance(response_number, int):
            raise GitHubAdapterError("GitHub issue number was invalid")
        return IssueMetadata(
            number=response_number,
            title=str(payload["title"]),
            body=str(payload.get("body") or ""),
            url=str(payload["html_url"]),
        )

    def _get(self, path: str) -> dict[str, object]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "PatchPilot/0.1 read-only adapter",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token.get_secret_value()}"
        request = Request(f"{self.api_url}{path}", headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise GitHubAdapterError("GitHub metadata request failed") from error
        if not isinstance(payload, dict):
            raise GitHubAdapterError("GitHub metadata response was not an object")
        return payload

    def _repository_name(self, full_name: str) -> str:
        if not self._name.fullmatch(full_name):
            raise ValueError("repository name must be owner/name")
        return full_name


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
            f"- `{run.stage}`: {run.execution.status.value} (`{' '.join(run.execution.command)}`)"
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
                "- Git publication and merge are outside the agent core and "
                "require operator action.",
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
                "PatchPilot is an independent implementation. No source code or commit history "
                "from the "
                "referenced projects is included.",
            )
        )
        return PullRequestDraft(title=title, body=body, base=base, head=head)
