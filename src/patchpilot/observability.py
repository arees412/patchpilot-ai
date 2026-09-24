"""In-process run metrics without invented cost values."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from patchpilot.models import RunMetrics


class RunObserver:
    """Collect monotonic durations and explicit counters."""

    def __init__(self, provider: str = "deterministic", model: str | None = None) -> None:
        self._started = time.monotonic()
        self._metrics = RunMetrics(provider=provider, model=model)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        yield
        elapsed = time.monotonic() - started
        field = f"{name}_duration_seconds"
        if field not in RunMetrics.model_fields:
            raise ValueError(f"unknown observable phase: {name}")
        self._metrics = self._metrics.model_copy(update={field: round(elapsed, 6)})

    def increment(self, field: str, amount: int = 1) -> None:
        if field not in RunMetrics.model_fields:
            raise ValueError(f"unknown metric: {field}")
        current = getattr(self._metrics, field)
        if not isinstance(current, int):
            raise TypeError(f"metric is not an integer counter: {field}")
        self._metrics = self._metrics.model_copy(update={field: current + amount})

    def set_token_usage(self, usage: int | None) -> None:
        if usage is not None and usage < 0:
            raise ValueError("token usage cannot be negative")
        self._metrics = self._metrics.model_copy(update={"token_usage": usage})

    def snapshot(self) -> RunMetrics:
        return self._metrics.model_copy(
            update={"total_duration_seconds": round(time.monotonic() - self._started, 6)}
        )
