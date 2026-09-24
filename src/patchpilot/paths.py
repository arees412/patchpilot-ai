"""Filesystem confinement helpers."""

from __future__ import annotations

from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a repository path escapes the allowed workspace."""


FORBIDDEN_PARTS = {
    ".git",
    ".ssh",
    ".aws",
    ".azure",
    ".config",
    "credentials",
    "id_rsa",
    "id_ed25519",
}


def resolve_workspace_path(
    root: Path, relative_path: str | Path, *, must_exist: bool | None = None
) -> Path:
    """Resolve a repository-relative path and reject traversal or symlink escapes."""

    root = root.resolve(strict=True)
    raw = Path(relative_path)
    if raw.is_absolute():
        raise UnsafePathError("absolute paths are not allowed")
    if any(part in {"", ".", ".."} for part in raw.parts):
        raise UnsafePathError("path traversal is not allowed")
    if any(part.lower() in FORBIDDEN_PARTS for part in raw.parts):
        raise UnsafePathError("access to protected paths is not allowed")

    candidate = root.joinpath(raw)
    probe = candidate if candidate.exists() else candidate.parent
    resolved_probe = probe.resolve(strict=True)
    if not resolved_probe.is_relative_to(root):
        raise UnsafePathError("path escapes the workspace")

    if candidate.exists() and candidate.is_symlink():
        resolved_candidate = candidate.resolve(strict=True)
        if not resolved_candidate.is_relative_to(root):
            raise UnsafePathError("symlink escapes the workspace")

    if must_exist is True and not candidate.exists():
        raise FileNotFoundError(candidate)
    if must_exist is False and candidate.exists():
        raise FileExistsError(candidate)
    return candidate
