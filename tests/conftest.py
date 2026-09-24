from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "service.py").write_text(
        "def greet(name: str) -> str:\n    return f'Hello {name}'\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_service.py").write_text(
        "from src.service import greet\n\n\ndef test_greet() -> None:\n"
        "    assert greet('Ada') == 'Hello Ada'\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n# pytest\n",
        encoding="utf-8",
    )
    commands = (
        ("git", "init", "-b", "main"),
        ("git", "config", "user.name", "PatchPilot Tests"),
        ("git", "config", "user.email", "tests@example.invalid"),
        ("git", "add", "."),
        ("git", "commit", "-m", "fixture"),
    )
    for command in commands:
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    return root
