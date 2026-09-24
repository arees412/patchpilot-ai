"""Structured, hash-checked patch application."""

from __future__ import annotations

import difflib
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from patchpilot.models import PatchCandidate, PatchFile, PatchOperation, ValidationStatus
from patchpilot.paths import UnsafePathError, resolve_workspace_path


class PatchValidationError(ValueError):
    """Raised when a patch is stale, unsafe, empty, or outside task scope."""


@dataclass(frozen=True)
class AppliedPatch:
    candidate: PatchCandidate
    unified_diff: str


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


class PatchEngine:
    """Apply a complete patch transaction or restore the original workspace."""

    def __init__(self, *, allow_deletes: bool = False, max_files: int = 50) -> None:
        self.allow_deletes = allow_deletes
        self.max_files = max_files

    def apply(
        self,
        workspace: Path,
        operations: Iterable[PatchFile],
        *,
        task_id: str,
        base_sha: str,
        allowed_paths: set[str] | None = None,
    ) -> AppliedPatch:
        root = workspace.resolve(strict=True)
        patches = tuple(operations)
        if not patches:
            raise PatchValidationError("patch is empty")
        if len(patches) > self.max_files:
            raise PatchValidationError("patch changes too many files")
        paths = [item.path for item in patches]
        if len(paths) != len(set(paths)):
            raise PatchValidationError("patch contains duplicate file operations")
        if allowed_paths is not None and not set(paths).issubset(allowed_paths):
            unexpected = sorted(set(paths) - allowed_paths)
            raise PatchValidationError(f"patch changes files outside task scope: {unexpected}")

        targets: dict[str, Path] = {}
        originals: dict[str, bytes | None] = {}
        before_text: dict[str, str] = {}
        after_text: dict[str, str] = {}
        for patch in patches:
            try:
                target = resolve_workspace_path(root, patch.path)
            except (UnsafePathError, FileNotFoundError, FileExistsError) as error:
                raise PatchValidationError(str(error)) from error
            self._reject_pollution(patch.path)
            targets[patch.path] = target
            existing = target.read_bytes() if target.exists() else None
            originals[patch.path] = existing
            before_text[patch.path] = self._decode(existing, patch.path) if existing is not None else ""
            self._preflight(patch, existing)
            after_text[patch.path] = "" if patch.operation is PatchOperation.DELETE else patch.content or ""

        try:
            for patch in patches:
                target = targets[patch.path]
                if patch.operation is PatchOperation.DELETE:
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(patch.content or "", encoding="utf-8", newline="\n")
        except Exception:
            self._rollback(targets, originals)
            raise

        unified_diff = self._diff(patches, before_text, after_text)
        if not unified_diff.strip():
            self._rollback(targets, originals)
            raise PatchValidationError("patch produces no content change")
        insertions, deletions = self._counts(unified_diff)
        digest = sha256_text(unified_diff)
        return AppliedPatch(
            candidate=PatchCandidate(
                task_id=task_id,
                base_sha=base_sha,
                files_changed=tuple(paths),
                insertions=insertions,
                deletions=deletions,
                validation_state=ValidationStatus.SKIPPED,
                diff_sha256=digest,
            ),
            unified_diff=unified_diff,
        )

    def _preflight(self, patch: PatchFile, existing: bytes | None) -> None:
        if patch.operation is PatchOperation.CREATE:
            if existing is not None:
                raise PatchValidationError(f"create target already exists: {patch.path}")
            if patch.content is None:
                raise PatchValidationError(f"create content is missing: {patch.path}")
        elif patch.operation in {PatchOperation.UPDATE, PatchOperation.DELETE}:
            if existing is None:
                raise PatchValidationError(f"patch target does not exist: {patch.path}")
            if not patch.expected_sha256:
                raise PatchValidationError(f"base hash is required: {patch.path}")
            if sha256_bytes(existing) != patch.expected_sha256:
                raise PatchValidationError(f"base hash mismatch: {patch.path}")
            if patch.operation is PatchOperation.UPDATE and patch.content is None:
                raise PatchValidationError(f"update content is missing: {patch.path}")
            if patch.operation is PatchOperation.DELETE and not self.allow_deletes:
                raise PatchValidationError(f"file deletion is not enabled: {patch.path}")
        if patch.content is not None and "\x00" in patch.content:
            raise PatchValidationError(f"binary content is not supported: {patch.path}")

    @staticmethod
    def _decode(value: bytes, path: str) -> str:
        if b"\x00" in value:
            raise PatchValidationError(f"binary target is not supported: {path}")
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PatchValidationError(f"target is not UTF-8 text: {path}") from error

    @staticmethod
    def _reject_pollution(path: str) -> None:
        parts = {part.lower() for part in Path(path).parts}
        forbidden = {
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".coverage",
            "dist",
            "build",
        }
        if parts & forbidden or Path(path).suffix.lower() in {".pyc", ".pyo"}:
            raise PatchValidationError(f"generated artifact path is not allowed: {path}")

    @staticmethod
    def _rollback(targets: dict[str, Path], originals: dict[str, bytes | None]) -> None:
        for path, original in originals.items():
            target = targets[path]
            if original is None:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original)

    @staticmethod
    def _diff(
        patches: tuple[PatchFile, ...], before: dict[str, str], after: dict[str, str]
    ) -> str:
        chunks: list[str] = []
        for patch in patches:
            old_name = "/dev/null" if patch.operation is PatchOperation.CREATE else f"a/{patch.path}"
            new_name = "/dev/null" if patch.operation is PatchOperation.DELETE else f"b/{patch.path}"
            chunks.extend(
                difflib.unified_diff(
                    before[patch.path].splitlines(keepends=True),
                    after[patch.path].splitlines(keepends=True),
                    fromfile=old_name,
                    tofile=new_name,
                    lineterm="\n",
                )
            )
        return "".join(chunks)

    @staticmethod
    def _counts(unified_diff: str) -> tuple[int, int]:
        insertions = 0
        deletions = 0
        for line in unified_diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                insertions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
        return insertions, deletions
