"""Deterministic repository context retrieval."""

from __future__ import annotations

import re
from pathlib import Path

from patchpilot.models import ContextMatch, EngineeringTask, RepositoryMap

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
STOP_WORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "when",
    "should",
    "repository",
}


def _tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN.finditer(value)} - STOP_WORDS


class ContextRetriever:
    """Rank files using auditable lexical, symbol, import, and test signals."""

    def retrieve(
        self, task: EngineeringTask, repository_map: RepositoryMap, *, limit: int = 12
    ) -> tuple[ContextMatch, ...]:
        query = _tokens(f"{task.title} {task.description}")
        ranked: list[ContextMatch] = []
        for item in repository_map.files:
            path_tokens = _tokens(item.path)
            symbol_names = {symbol.name.lower() for symbol in item.symbols}
            import_tokens = {token for name in item.imports for token in _tokens(name)}
            reasons: list[str] = []
            score = 0.0

            lexical = query & path_tokens
            if lexical:
                score += 3.0 * len(lexical)
                reasons.append(f"path terms: {', '.join(sorted(lexical))}")
            symbols = query & symbol_names
            if symbols:
                score += 5.0 * len(symbols)
                reasons.append(f"symbols: {', '.join(sorted(symbols))}")
            imports = query & import_tokens
            if imports:
                score += 1.5 * len(imports)
                reasons.append(f"imports: {', '.join(sorted(imports))}")
            if item.is_test and query & _tokens(Path(item.path).stem.replace("test", "")):
                score += 2.5
                reasons.append("test proximity")
            if item.is_manifest and query & {"dependency", "dependencies", "package", "build"}:
                score += 4.0
                reasons.append("dependency manifest")
            if item.is_config and query & {"config", "configuration", "ci", "workflow"}:
                score += 4.0
                reasons.append("configuration file")

            if score:
                ranked.append(
                    ContextMatch(
                        path=item.path,
                        score=score,
                        reasons=tuple(reasons),
                        symbols=tuple(sorted(symbol.name for symbol in item.symbols)),
                    )
                )
        ranked.sort(key=lambda match: (-match.score, match.path))
        return tuple(ranked[:limit])
