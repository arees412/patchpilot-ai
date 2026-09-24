"""Repository intake and deterministic intelligence mapping."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from patchpilot.models import CodeSymbol, RepositoryFile, RepositoryMap, RepositorySnapshot

LANGUAGES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".md": "Markdown",
}
MANIFEST_NAMES = {
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "cargo.toml",
    "go.mod",
}
CONFIG_NAMES = {
    "tox.ini",
    "pytest.ini",
    "mypy.ini",
    "ruff.toml",
    "tsconfig.json",
    "vite.config.ts",
    "jest.config.js",
    "dockerfile",
    "makefile",
}
IGNORED_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}


class RepositoryIntakeError(RuntimeError):
    """Raised when a directory cannot be accepted as a Git repository."""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RepositoryIntakeError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_test(path: Path) -> bool:
    lowered = path.as_posix().lower()
    return (
        "tests/" in lowered
        or "__tests__/" in lowered
        or path.name.lower().startswith("test_")
        or path.name.lower().endswith(("_test.py", ".test.ts", ".spec.ts", ".test.js", ".spec.js"))
    )


def _python_symbols(path: str, text: str) -> tuple[list[CodeSymbol], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []
    symbols: list[CodeSymbol] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [argument.arg for argument in node.args.args]
            symbols.append(
                CodeSymbol(
                    name=node.name,
                    kind="function",
                    path=path,
                    line=node.lineno,
                    signature=f"{node.name}({', '.join(args)})",
                )
            )
        elif isinstance(node, ast.ClassDef):
            symbols.append(CodeSymbol(name=node.name, kind="class", path=path, line=node.lineno))
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return symbols, imports


JS_SYMBOL = re.compile(
    r"(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)|"
    r"(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
)
JS_IMPORT = re.compile(r"(?:from\s+|require\(['\"])([^'\"]+)")


def _javascript_symbols(path: str, text: str) -> tuple[list[CodeSymbol], list[str]]:
    symbols: list[CodeSymbol] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in JS_SYMBOL.finditer(line):
            name = match.group(1) or match.group(2)
            kind = "class" if "class" in match.group(0) else "function"
            symbols.append(CodeSymbol(name=name, kind=kind, path=path, line=line_number))
    return symbols, JS_IMPORT.findall(text)


def _read_text(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


class RepositoryAnalyzer:
    """Create an evidence-backed map of a local Git repository."""

    def snapshot(self, repository: Path) -> RepositorySnapshot:
        root = repository.resolve(strict=True)
        if not root.is_dir() or not (root / ".git").exists():
            raise RepositoryIntakeError("repository must be a local Git working tree")
        base_sha = _git(root, "rev-parse", "HEAD")
        branch = _git(root, "branch", "--show-current") or "DETACHED"

        mapped = self._map_files(root)
        languages = Counter(item.language for item in mapped if item.language != "Unknown")
        manifests = tuple(item.path for item in mapped if item.is_manifest)
        configs = tuple(item.path for item in mapped if item.is_config)
        frameworks = self._test_frameworks(root, manifests)
        return RepositorySnapshot(
            repository_path=str(root),
            base_sha=base_sha,
            branch=branch,
            language_breakdown=dict(sorted(languages.items())),
            package_manifests=manifests,
            test_frameworks=frameworks,
            build_configuration=configs,
        )

    def build_map(self, repository: Path) -> RepositoryMap:
        root = repository.resolve(strict=True)
        snapshot = self.snapshot(root)
        files = tuple(self._map_files(root))
        symbols = tuple(symbol for item in files for symbol in item.symbols)
        entry_points = tuple(
            item.path
            for item in files
            if Path(item.path).name.lower()
            in {"main.py", "app.py", "cli.py", "index.ts", "index.js", "server.ts", "server.js"}
        )
        return RepositoryMap(
            snapshot=snapshot,
            files=files,
            symbols=symbols,
            likely_entry_points=entry_points,
        )

    def _map_files(self, root: Path) -> list[RepositoryFile]:
        mapped: list[RepositoryFile] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in IGNORED_DIRS for part in path.parts):
                continue
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            text = _read_text(data)
            language = LANGUAGES.get(path.suffix.lower(), "Unknown")
            symbols: list[CodeSymbol] = []
            imports: list[str] = []
            if text is not None and language == "Python":
                symbols, imports = _python_symbols(relative, text)
            elif text is not None and language in {"JavaScript", "TypeScript"}:
                symbols, imports = _javascript_symbols(relative, text)
            name = path.name.lower()
            mapped.append(
                RepositoryFile(
                    path=relative,
                    sha256=_hash_bytes(data),
                    size=len(data),
                    language=language,
                    is_test=_is_test(path.relative_to(root)),
                    is_config=name in CONFIG_NAMES or relative.startswith(".github/workflows/"),
                    is_manifest=name in MANIFEST_NAMES,
                    imports=tuple(sorted(set(imports))),
                    symbols=tuple(symbols),
                )
            )
        return mapped

    @staticmethod
    def _test_frameworks(root: Path, manifests: tuple[str, ...]) -> tuple[str, ...]:
        frameworks: set[str] = set()
        if "pyproject.toml" in manifests:
            text = (root / "pyproject.toml").read_text(encoding="utf-8")
            if "pytest" in text:
                frameworks.add("pytest")
        if "requirements.txt" in manifests:
            text = (root / "requirements.txt").read_text(encoding="utf-8")
            if "pytest" in text:
                frameworks.add("pytest")
        if "package.json" in manifests:
            try:
                package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                package = {}
            combined = json.dumps(package).lower()
            for candidate in ("vitest", "jest"):
                if candidate in combined:
                    frameworks.add(candidate)
            if package.get("scripts", {}).get("test"):
                frameworks.add("npm-test")
        return tuple(sorted(frameworks))
