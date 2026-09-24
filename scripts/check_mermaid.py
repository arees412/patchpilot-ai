"""Perform deterministic structural checks for checked-in Mermaid blocks."""

from __future__ import annotations

import re
import sys
from pathlib import Path

BLOCK = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
START = re.compile(r"^\s*(?:flowchart|graph|stateDiagram(?:-v2)?)\b", re.MULTILINE)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    count = 0
    for document in root.rglob("*.md"):
        if ".git" in document.parts or ".venv" in document.parts:
            continue
        text = document.read_text(encoding="utf-8")
        openings = text.count("```mermaid")
        blocks = BLOCK.findall(text)
        count += len(blocks)
        if openings != len(blocks):
            failures.append(f"{document.relative_to(root)} has an unclosed Mermaid fence")
        for index, block in enumerate(blocks, start=1):
            if not START.search(block):
                failures.append(
                    f"{document.relative_to(root)} Mermaid block {index} has no supported header"
                )
    if not count:
        failures.append("no Mermaid diagrams found")
    if failures:
        print("Mermaid validation failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"Validated {count} Mermaid blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
