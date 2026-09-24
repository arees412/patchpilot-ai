"""Scan tracked text for a narrow set of high-confidence credential patterns."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RULES = (
    ("private-key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github-token", re.compile(rb"\bgh[opsu]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer-token", re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
)


def tracked_files(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=root,
        capture_output=True,
        check=True,
    )
    return tuple(root / path.decode("utf-8") for path in result.stdout.split(b"\0") if path)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings: list[tuple[str, str]] = []
    for path in tracked_files(root):
        data = path.read_bytes()
        if b"\0" in data:
            continue
        for name, pattern in RULES:
            if pattern.search(data):
                findings.append((path.relative_to(root).as_posix(), name))
    if findings:
        print("Potential secrets found; matched values are intentionally omitted:")
        print("\n".join(f"- {path}: {rule}" for path, rule in findings))
        return 1
    print("No high-confidence secret patterns found in tracked text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
