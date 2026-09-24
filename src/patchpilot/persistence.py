"""SQLite persistence for tasks, runs, approvals, validation, audit, and evidence."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from patchpilot.models import (
    AgentRun,
    AgentState,
    ApprovalRequest,
    AuditEvent,
    EngineeringTask,
    EvidenceRecord,
    TaskAnalysis,
    TaskPlan,
    TestRun,
    utc_now,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    task_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    previous_state TEXT NOT NULL,
    next_state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    patch_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validation_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class SQLiteStore:
    """Small explicit store; command output must be sanitized before insertion."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def save_task(self, task: EngineeringTask) -> None:
        now = utc_now().isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO tasks(id, payload, created_at, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
                (task.id, task.model_dump_json(), task.created_at.isoformat(), now),
            )

    def save_run(self, run: AgentRun) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO runs(id, task_id, state, payload, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state=excluded.state, payload=excluded.payload, updated_at=excluded.updated_at""",
                (
                    run.id,
                    run.task_id,
                    run.state.value,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )

    def save_analysis(self, analysis: TaskAnalysis) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO analyses(task_id, payload, created_at)
                VALUES(?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload""",
                (analysis.task_id, analysis.model_dump_json(), utc_now().isoformat()),
            )

    def save_plan(self, plan: TaskPlan) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO plans(id, task_id, payload, created_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                (plan.id, plan.task_id, plan.model_dump_json(), plan.created_at.isoformat()),
            )

    def record_transition(
        self, run_id: str, previous: AgentState, next_state: AgentState
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO state_transitions(run_id, previous_state, next_state, created_at)
                VALUES(?, ?, ?, ?)""",
                (run_id, previous.value, next_state.value, utc_now().isoformat()),
            )

    def save_approval(self, approval: ApprovalRequest) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO approvals(id, task_id, patch_hash, state, payload, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    state=excluded.state, payload=excluded.payload, updated_at=excluded.updated_at""",
                (
                    approval.id,
                    approval.task_id,
                    approval.patch_hash,
                    approval.state.value,
                    approval.model_dump_json(),
                    utc_now().isoformat(),
                ),
            )

    def save_validation(self, run_id: str, result: TestRun) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO validation_records(run_id, stage, status, payload, created_at)
                VALUES(?, ?, ?, ?, ?)""",
                (
                    run_id,
                    result.stage,
                    result.execution.status.value,
                    result.model_dump_json(),
                    utc_now().isoformat(),
                ),
            )

    def append_audit(self, event: AuditEvent) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO audit_events(id, run_id, event_type, payload, created_at)
                VALUES(?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.run_id,
                    event.event_type,
                    event.model_dump_json(),
                    event.created_at.isoformat(),
                ),
            )

    def save_evidence(self, run_id: str, record: EvidenceRecord) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO evidence_records(run_id, kind, sha256, payload, created_at)
                VALUES(?, ?, ?, ?, ?)""",
                (
                    run_id,
                    record.kind,
                    record.sha256,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                ),
            )

    def fetch_run(self, run_id: str) -> AgentRun:
        row = self._one("SELECT payload FROM runs WHERE id = ?", (run_id,))
        return AgentRun.model_validate_json(row["payload"])

    def fetch_task(self, task_id: str) -> EngineeringTask:
        row = self._one("SELECT payload FROM tasks WHERE id = ?", (task_id,))
        return EngineeringTask.model_validate_json(row["payload"])

    def fetch_analysis(self, task_id: str) -> TaskAnalysis:
        row = self._one("SELECT payload FROM analyses WHERE task_id = ?", (task_id,))
        return TaskAnalysis.model_validate_json(row["payload"])

    def fetch_plan(self, plan_id: str) -> TaskPlan:
        row = self._one("SELECT payload FROM plans WHERE id = ?", (plan_id,))
        return TaskPlan.model_validate_json(row["payload"])

    def fetch_approval(self, approval_id: str) -> ApprovalRequest:
        row = self._one("SELECT payload FROM approvals WHERE id = ?", (approval_id,))
        return ApprovalRequest.model_validate_json(row["payload"])

    def transitions(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT previous_state, next_state, created_at
                FROM state_transitions WHERE run_id = ? ORDER BY id""",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def evidence(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM evidence_records WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return tuple(EvidenceRecord.model_validate_json(row["payload"]) for row in rows)

    def _one(self, query: str, parameters: tuple[object, ...]) -> sqlite3.Row:
        with self._lock:
            row = self._connection.execute(query, parameters).fetchone()
        if row is None:
            raise KeyError(parameters[0])
        return row

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
