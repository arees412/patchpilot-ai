"""Typed domain models used across PatchPilot."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return a timezone-aware timestamp."""

    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Create a readable, collision-resistant identifier."""

    return f"{prefix}_{uuid4().hex}"


class FrozenModel(BaseModel):
    """Immutable base for evidence-friendly domain values."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentState(StrEnum):
    CREATED = "created"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    PREPARING_WORKSPACE = "preparing_workspace"
    EDITING = "editing"
    VALIDATING = "validating"
    TESTING = "testing"
    REVIEWING_PATCH = "reviewing_patch"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


class ValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class PatchOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class EngineeringTask(FrozenModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    source: str = "local"
    title: str
    description: str
    repository: str
    base_revision: str | None = None
    status: AgentState = AgentState.CREATED
    created_at: datetime = Field(default_factory=utc_now)
    risk_level: RiskLevel = RiskLevel.LOW


class CodeSymbol(FrozenModel):
    name: str
    kind: str
    path: str
    line: int
    signature: str | None = None


class RepositoryFile(FrozenModel):
    path: str
    sha256: str
    size: int
    language: str
    is_test: bool = False
    is_config: bool = False
    is_manifest: bool = False
    imports: tuple[str, ...] = ()
    symbols: tuple[CodeSymbol, ...] = ()


class RepositorySnapshot(FrozenModel):
    repository_path: str
    base_sha: str
    branch: str
    language_breakdown: dict[str, int]
    package_manifests: tuple[str, ...] = ()
    test_frameworks: tuple[str, ...] = ()
    build_configuration: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class RepositoryMap(FrozenModel):
    snapshot: RepositorySnapshot
    files: tuple[RepositoryFile, ...]
    symbols: tuple[CodeSymbol, ...]
    likely_entry_points: tuple[str, ...] = ()


class ContextMatch(FrozenModel):
    path: str
    score: float
    reasons: tuple[str, ...]
    symbols: tuple[str, ...] = ()


class TaskAnalysis(FrozenModel):
    task_id: str
    objective: str
    affected_area: tuple[str, ...]
    likely_files: tuple[str, ...]
    likely_tests: tuple[str, ...]
    constraints: tuple[str, ...]
    unknowns: tuple[str, ...] = ()
    security_impact: str = "none identified"
    migration_impact: str = "none identified"
    confidence: float = Field(ge=0, le=1)
    blocked: bool = False


class PlanStep(FrozenModel):
    index: int = Field(ge=1)
    objective: str
    files_or_symbols: tuple[str, ...]
    expected_modification: str
    validation: tuple[str, ...]
    dependencies: tuple[int, ...] = ()
    risk: RiskLevel = RiskLevel.LOW


class TaskPlan(FrozenModel):
    id: str = Field(default_factory=lambda: new_id("plan"))
    task_id: str
    steps: tuple[PlanStep, ...]
    created_at: datetime = Field(default_factory=utc_now)


class SandboxSession(FrozenModel):
    id: str = Field(default_factory=lambda: new_id("sandbox"))
    repository_path: str
    workspace_path: str
    network_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class CommandExecution(FrozenModel):
    command: tuple[str, ...]
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    status: ValidationStatus
    timed_out: bool = False
    output_truncated: bool = False


class PatchFile(FrozenModel):
    path: str
    operation: PatchOperation
    expected_sha256: str | None = None
    content: str | None = None


class TestRun(FrozenModel):
    stage: str
    execution: CommandExecution


class PatchCandidate(FrozenModel):
    id: str = Field(default_factory=lambda: new_id("patch"))
    task_id: str
    base_sha: str
    files_changed: tuple[str, ...]
    insertions: int
    deletions: int
    tests_run: tuple[TestRun, ...] = ()
    validation_state: ValidationStatus = ValidationStatus.SKIPPED
    risk_score: int = Field(default=0, ge=0, le=100)
    diff_sha256: str


class RiskAssessment(FrozenModel):
    score: int = Field(ge=0, le=100)
    level: RiskLevel
    reasons: tuple[str, ...]
    requires_approval: bool


class ApprovalRequest(FrozenModel):
    id: str = Field(default_factory=lambda: new_id("approval"))
    task_id: str
    plan_id: str
    action: str
    patch_hash: str
    state: ApprovalState = ApprovalState.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    decided_at: datetime | None = None


class EvidenceRecord(FrozenModel):
    kind: str
    data: dict[str, object]
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(FrozenModel):
    id: str = Field(default_factory=lambda: new_id("event"))
    run_id: str
    event_type: str
    data: dict[str, object]
    created_at: datetime = Field(default_factory=utc_now)


class RunMetrics(FrozenModel):
    total_duration_seconds: float = 0
    analysis_duration_seconds: float = 0
    planning_duration_seconds: float = 0
    sandbox_duration_seconds: float = 0
    command_count: int = 0
    test_count: int = 0
    repair_attempts: int = 0
    files_changed: int = 0
    lines_changed: int = 0
    validation_failures: int = 0
    provider: str = "deterministic"
    model: str | None = None
    token_usage: int | None = None


class AgentRun(FrozenModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    task_id: str
    state: AgentState = AgentState.CREATED
    plan_id: str | None = None
    patch_id: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
