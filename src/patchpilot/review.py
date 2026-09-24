"""Deterministic diff review and secret detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SecretFinding:
    path: str
    rule: str
    classification: str = "redacted high-confidence match"


@dataclass(frozen=True)
class ReviewFinding:
    rule: str
    message: str
    path: str | None = None


SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|password|passwd|secret)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}"
        ),
    ),
)


class SecretScanner:
    """Return only rule metadata; never return the matched credential value."""

    def scan(self, path: str, content: str) -> tuple[SecretFinding, ...]:
        findings = [
            SecretFinding(path=path, rule=name)
            for name, pattern in SECRET_RULES
            if pattern.search(content)
        ]
        return tuple(findings)


class DiffReviewEngine:
    """Run transparent heuristics over only the proposed change."""

    def __init__(self, scanner: SecretScanner | None = None) -> None:
        self.scanner = scanner or SecretScanner()

    def review(
        self, unified_diff: str, changed_content: dict[str, str]
    ) -> tuple[ReviewFinding, ...]:
        findings: list[ReviewFinding] = []
        added = "\n".join(
            line[1:]
            for line in unified_diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        checks = (
            ("debug-print", r"(?m)^\s*(print\(|console\.log\()", "debug output was added"),
            ("unresolved-marker", r"\b(?:TODO|FIXME)\b", "an unresolved marker was added"),
            (
                "disabled-test",
                r"(?:pytest\.mark\.skip|@unittest\.skip|\b(?:it|test)\.skip\()",
                "a disabled test marker was added",
            ),
            (
                "broad-exception",
                r"(?:except\s+(?:Exception|BaseException)\s*:|catch\s*\([^)]*\)\s*\{)",
                "broad exception handling was added",
            ),
            (
                "dangerous-subprocess",
                r"(?:shell\s*=\s*True|os\.system\(|subprocess\.(?:run|Popen)\([^\n]*shell\s*=\s*True)",
                "dangerous subprocess behavior was added",
            ),
        )
        for rule, pattern, message in checks:
            if re.search(pattern, added):
                findings.append(ReviewFinding(rule=rule, message=message))

        dependency_names = {
            "pyproject.toml",
            "requirements.txt",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        }
        if {Path(path).name.lower() for path in changed_content} & dependency_names:
            findings.append(
                ReviewFinding(
                    rule="dependency-change",
                    message="dependency or lock-file change requires explanation and approval",
                )
            )
        for path, content in changed_content.items():
            for secret in self.scanner.scan(path, content):
                findings.append(
                    ReviewFinding(
                        rule=f"secret.{secret.rule}",
                        message=secret.classification,
                        path=secret.path,
                    )
                )
        return tuple(findings)
