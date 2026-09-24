"""Fail when a relative Markdown link points at a missing repository path."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"(?<!!)\[[^]]*\]\(([^)]+)\)")


def markdown_files(root: Path) -> tuple[Path, ...]:
    ignored = {".git", ".venv", "build", "dist"}
    return tuple(
        path
        for path in root.rglob("*.md")
        if not any(part in ignored for part in path.relative_to(root).parts)
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for document in markdown_files(root):
        text = document.read_text(encoding="utf-8")
        for raw in LINK.findall(text):
            target = raw.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = unquote(target.split("#", 1)[0])
            if path_text and not (document.parent / path_text).resolve().exists():
                failures.append(f"{document.relative_to(root)} -> {target}")
    if failures:
        print("Broken relative Markdown links:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"Markdown links valid across {len(markdown_files(root))} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
