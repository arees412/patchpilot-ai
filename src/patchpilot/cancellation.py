"""Cooperative cancellation for bounded agent runs."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from patchpilot.models import utc_now


@dataclass(frozen=True)
class CancellationRecord:
    reason: str
    requested_at: str


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._record: CancellationRecord | None = None

    @property
    def event(self) -> threading.Event:
        return self._event

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def record(self) -> CancellationRecord | None:
        return self._record

    def cancel(self, reason: str = "operator requested cancellation") -> None:
        if not self._event.is_set():
            self._record = CancellationRecord(reason=reason, requested_at=utc_now().isoformat())
            self._event.set()
