from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from patchpilot.approvals import ApprovalError, ApprovalManager
from patchpilot.git_safety import GitSafety, GitSafetyError
from patchpilot.github_adapter import PullRequestDraftGenerator
from patchpilot.models import (
    CommandExecution,
    PatchCandidate,
    PatchFile,
    PatchOperation,
    RiskLevel,
    ValidationStatus,
    utc_now,
)
from patchpilot.models import (
    TestRun as RecordedTestRun,
)
from patchpilot.patching import PatchEngine, PatchValidationError, sha256_text
from patchpilot.review import DiffReviewEngine, SecretScanner
from patchpilot.risk import RiskEngine


def test_patch_update_is_hash_checked_and_diffed(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8", newline="\n")
    applied = PatchEngine().apply(
        tmp_path,
        (
            PatchFile(
                path="module.py",
                operation=PatchOperation.UPDATE,
                expected_sha256=sha256_text(original),
                content="value = 2\n",
            ),
        ),
        task_id="task",
        base_sha="base",
    )
    assert applied.candidate.files_changed == ("module.py",)
    assert "+value = 2" in applied.unified_diff
    assert applied.candidate.insertions == 1


def test_patch_rejects_stale_hash_without_change(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("original\n", encoding="utf-8", newline="\n")
    with pytest.raises(PatchValidationError, match="hash mismatch"):
        PatchEngine().apply(
            tmp_path,
            (
                PatchFile(
                    path="module.py",
                    operation=PatchOperation.UPDATE,
                    expected_sha256="0" * 64,
                    content="changed\n",
                ),
            ),
            task_id="task",
            base_sha="base",
        )
    assert target.read_text(encoding="utf-8") == "original\n"


def test_patch_rejects_scope_escape_and_generated_artifact(tmp_path: Path) -> None:
    with pytest.raises(PatchValidationError, match="outside task scope"):
        PatchEngine().apply(
            tmp_path,
            (PatchFile(path="other.py", operation=PatchOperation.CREATE, content="x = 1\n"),),
            task_id="task",
            base_sha="base",
            allowed_paths={"expected.py"},
        )
    with pytest.raises(PatchValidationError, match="generated artifact"):
        PatchEngine().apply(
            tmp_path,
            (PatchFile(path="__pycache__/x.pyc", operation=PatchOperation.CREATE, content="x"),),
            task_id="task",
            base_sha="base",
        )


def test_patch_deletion_is_opt_in(tmp_path: Path) -> None:
    target = tmp_path / "delete.py"
    target.write_text("delete_me = True\n", encoding="utf-8", newline="\n")
    patch = PatchFile(
        path="delete.py",
        operation=PatchOperation.DELETE,
        expected_sha256=sha256_text("delete_me = True\n"),
    )
    with pytest.raises(PatchValidationError, match="not enabled"):
        PatchEngine().apply(tmp_path, (patch,), task_id="task", base_sha="base")
    PatchEngine(allow_deletes=True).apply(tmp_path, (patch,), task_id="task", base_sha="base")
    assert not target.exists()


def test_risk_engine_scores_sensitive_change() -> None:
    candidate = PatchCandidate(
        task_id="task",
        base_sha="base",
        files_changed=("src/auth/permissions.py", "pyproject.toml"),
        insertions=80,
        deletions=40,
        validation_state=ValidationStatus.PASSED,
        diff_sha256="a" * 64,
    )
    assessment = RiskEngine().assess(candidate, "+public API\n")
    assert assessment.level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    assert assessment.requires_approval
    assert any("security" in reason for reason in assessment.reasons)


def test_low_risk_patch_is_not_forced_to_approval() -> None:
    candidate = PatchCandidate(
        task_id="task",
        base_sha="base",
        files_changed=("docs/guide.md",),
        insertions=2,
        deletions=0,
        validation_state=ValidationStatus.PASSED,
        diff_sha256="a" * 64,
    )
    assessment = RiskEngine().assess(candidate, "+small clarification\n")
    assert assessment.level is RiskLevel.LOW
    assert not assessment.requires_approval


def test_approval_is_bound_to_exact_patch_scope() -> None:
    manager = ApprovalManager()
    request = manager.request(
        task_id="task", plan_id="plan", action="apply-patch", patch_hash="hash-one"
    )
    manager.approve(request.id)
    manager.require(
        request.id,
        task_id="task",
        plan_id="plan",
        action="apply-patch",
        patch_hash="hash-one",
    )
    with pytest.raises(ApprovalError, match="scope"):
        manager.require(
            request.id,
            task_id="task",
            plan_id="plan",
            action="apply-patch",
            patch_hash="hash-two",
        )


def test_approval_expires_before_decision() -> None:
    manager = ApprovalManager()
    request = manager.request(
        task_id="task",
        plan_id="plan",
        action="apply-patch",
        patch_hash="hash",
        expires_at=utc_now() - timedelta(seconds=1),
    )
    with pytest.raises(ApprovalError, match="already expired"):
        manager.approve(request.id)


def test_secret_scanner_returns_metadata_not_value() -> None:
    secret = "A" * 20
    findings = SecretScanner().scan("config.py", "ACCESS_TOKEN='" + secret + "'")
    assert findings
    assert secret not in repr(findings)
    assert findings[0].path == "config.py"


def test_diff_review_flags_security_and_quality_findings() -> None:
    secret = "B" * 20
    content = "print('debug')\npassword='" + secret + "'\n# TODO remove\n"
    findings = DiffReviewEngine().review(
        "+print('debug')\n+# TODO remove\n+password='" + secret + "'\n",
        {"src/module.py": content},
    )
    rules = {finding.rule for finding in findings}
    assert {"debug-print", "unresolved-marker", "secret.credential-assignment"} <= rules
    assert secret not in repr(findings)


def test_git_safety_allows_reads_but_disables_publication(git_repo: Path) -> None:
    safety = GitSafety(git_repo)
    assert safety.revision()
    assert "main" in safety.status()
    with pytest.raises(GitSafetyError, match="disabled"):
        safety.push()
    with pytest.raises(GitSafetyError, match="disabled"):
        safety.merge()


def test_pr_draft_is_generated_without_publication() -> None:
    execution = CommandExecution(
        command=("python", "-m", "pytest", "-q"),
        exit_code=0,
        duration_seconds=0.2,
        stdout="",
        stderr="",
        status=ValidationStatus.PASSED,
    )
    candidate = PatchCandidate(
        task_id="task",
        base_sha="base",
        files_changed=("src/module.py",),
        insertions=2,
        deletions=1,
        tests_run=(RecordedTestRun(stage="tests", execution=execution),),
        validation_state=ValidationStatus.PASSED,
        diff_sha256="c" * 64,
    )
    risk = RiskEngine().assess(candidate, "+safe change\n")
    draft = PullRequestDraftGenerator().generate(
        title="Fix module",
        base="main",
        head="fix/module",
        task_summary="Correct the module.",
        implementation_summary=("Updated the bounded implementation.",),
        patch=candidate,
        validations=candidate.tests_run,
        risk=risk,
        limitations=("Deterministic fixture only.",),
        rollback="Revert the patch commit.",
        evidence=(candidate.diff_sha256,),
        references=("Architecture references only.",),
    )
    assert draft.base == "main"
    assert "## Validation" in draft.body
    assert "independent implementation" in draft.body
