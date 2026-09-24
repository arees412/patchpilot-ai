"""Bounded deterministic patch repair loop."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from patchpilot.models import PatchFile, TestRun, ValidationStatus


@dataclass(frozen=True)
class RepairResult:
    succeeded: bool
    attempts: int
    validations: tuple[tuple[TestRun, ...], ...]


class BoundedRepairLoop:
    """Retry only from concrete failure evidence and never exceed the limit."""

    def __init__(self, maximum_attempts: int = 3) -> None:
        if maximum_attempts < 0:
            raise ValueError("maximum attempts cannot be negative")
        self.maximum_attempts = maximum_attempts

    def run(
        self,
        initial_results: Sequence[TestRun],
        repair: Callable[[int, Sequence[TestRun]], Sequence[PatchFile]],
        apply: Callable[[Sequence[PatchFile]], None],
        validate: Callable[[], Sequence[TestRun]],
    ) -> RepairResult:
        history: list[tuple[TestRun, ...]] = [tuple(initial_results)]
        if self._passed(initial_results):
            return RepairResult(True, 0, tuple(history))

        for attempt in range(1, self.maximum_attempts + 1):
            proposed = tuple(repair(attempt, history[-1]))
            if not proposed:
                return RepairResult(False, attempt - 1, tuple(history))
            apply(proposed)
            results = tuple(validate())
            history.append(results)
            if self._passed(results):
                return RepairResult(True, attempt, tuple(history))
        return RepairResult(False, self.maximum_attempts, tuple(history))

    @staticmethod
    def _passed(results: Sequence[TestRun]) -> bool:
        return bool(results) and all(
            result.execution.status is ValidationStatus.PASSED for result in results
        )
