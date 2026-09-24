"""Deterministic, documented patch risk scoring."""

from __future__ import annotations

from pathlib import Path

from patchpilot.models import PatchCandidate, RiskAssessment, RiskLevel, ValidationStatus

DEPENDENCY_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "cargo.toml",
    "go.mod",
}
MIGRATION_TERMS = {"migration", "migrations", "schema", "alembic", "prisma"}
SECURITY_TERMS = {"auth", "security", "secret", "credential", "permission", "oauth"}
CI_PREFIXES = (".github/workflows/", ".gitlab-ci", "azure-pipelines")
INFRA_TERMS = {"terraform", "helm", "k8s", "kubernetes", "dockerfile", "compose"}


class RiskEngine:
    """Score observable signals; the result is not a calibrated probability."""

    def assess(self, patch: PatchCandidate, unified_diff: str) -> RiskAssessment:
        score = 5
        reasons: list[str] = ["base patch risk: +5"]
        file_count_points = min(len(patch.files_changed) * 2, 20)
        score += file_count_points
        reasons.append(f"{len(patch.files_changed)} changed files: +{file_count_points}")

        lines_changed = patch.insertions + patch.deletions
        line_points = 0 if lines_changed <= 20 else 10 if lines_changed <= 100 else 20
        score += line_points
        if line_points:
            reasons.append(f"{lines_changed} changed lines: +{line_points}")

        lowered_paths = tuple(path.lower() for path in patch.files_changed)
        names = {Path(path).name.lower() for path in lowered_paths}
        if names & DEPENDENCY_FILES:
            score += 20
            reasons.append("dependency manifest or lock file changed: +20")
        if any(any(term in path for term in MIGRATION_TERMS) for path in lowered_paths):
            score += 25
            reasons.append("database or schema area changed: +25")
        if any(any(term in path for term in SECURITY_TERMS) for path in lowered_paths):
            score += 25
            reasons.append("authentication or security area changed: +25")
        if any(path.startswith(CI_PREFIXES) for path in lowered_paths):
            score += 20
            reasons.append("CI configuration changed: +20")
        if any(any(term in path for term in INFRA_TERMS) for path in lowered_paths):
            score += 20
            reasons.append("infrastructure area changed: +20")
        if any("test" in path for path in lowered_paths) and any(
            line.startswith("-") and not line.startswith("---")
            for line in unified_diff.splitlines()
        ):
            score += 15
            reasons.append("test content deleted: +15")
        if "__all__" in unified_diff or "public api" in unified_diff.lower():
            score += 10
            reasons.append("possible public API change: +10")

        failed = sum(run.execution.status is ValidationStatus.FAILED for run in patch.tests_run)
        skipped = sum(run.execution.status is ValidationStatus.SKIPPED for run in patch.tests_run)
        if failed:
            points = min(30, failed * 15)
            score += points
            reasons.append(f"failed validation: +{points}")
        if skipped:
            points = min(15, skipped * 5)
            score += points
            reasons.append(f"skipped validation: +{points}")

        score = min(score, 100)
        level = (
            RiskLevel.LOW
            if score < 20
            else RiskLevel.MEDIUM
            if score < 45
            else RiskLevel.HIGH
            if score < 70
            else RiskLevel.CRITICAL
        )
        sensitive = any(
            text in " ".join(lowered_paths)
            for text in (*SECURITY_TERMS, *MIGRATION_TERMS, *INFRA_TERMS)
        ) or bool(names & DEPENDENCY_FILES)
        return RiskAssessment(
            score=score,
            level=level,
            reasons=tuple(reasons),
            requires_approval=level in {RiskLevel.HIGH, RiskLevel.CRITICAL} or sensitive,
        )
