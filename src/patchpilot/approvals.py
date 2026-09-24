"""Patch-scoped human approval records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from patchpilot.models import ApprovalRequest, ApprovalState, utc_now


class ApprovalError(ValueError):
    """Raised for missing, stale, rejected, or incorrectly scoped approval."""


class ApprovalManager:
    """Issue and resolve approvals that cannot be reused for another patch."""

    def __init__(self, persist: Callable[[ApprovalRequest], None] | None = None) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._persist = persist

    def request(
        self,
        *,
        task_id: str,
        plan_id: str,
        action: str,
        patch_hash: str,
        expires_at: datetime | None = None,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            task_id=task_id,
            plan_id=plan_id,
            action=action,
            patch_hash=patch_hash,
            expires_at=expires_at,
        )
        self._save(request)
        return request

    def approve(self, approval_id: str) -> ApprovalRequest:
        return self._decide(approval_id, ApprovalState.APPROVED)

    def reject(self, approval_id: str) -> ApprovalRequest:
        return self._decide(approval_id, ApprovalState.REJECTED)

    def require(
        self,
        approval_id: str,
        *,
        task_id: str,
        plan_id: str,
        action: str,
        patch_hash: str,
    ) -> ApprovalRequest:
        request = self.get(approval_id)
        request = self._expire_if_needed(request)
        if request.state is not ApprovalState.APPROVED:
            raise ApprovalError(f"approval state is {request.state}")
        expected = (task_id, plan_id, action, patch_hash)
        actual = (request.task_id, request.plan_id, request.action, request.patch_hash)
        if actual != expected:
            raise ApprovalError("approval scope does not match this action and patch")
        return request

    def get(self, approval_id: str) -> ApprovalRequest:
        try:
            return self._requests[approval_id]
        except KeyError as error:
            raise ApprovalError("approval does not exist") from error

    def _decide(self, approval_id: str, state: ApprovalState) -> ApprovalRequest:
        request = self._expire_if_needed(self.get(approval_id))
        if request.state is not ApprovalState.PENDING:
            raise ApprovalError(f"approval is already {request.state}")
        decided = request.model_copy(update={"state": state, "decided_at": utc_now()})
        self._save(decided)
        return decided

    def _expire_if_needed(self, request: ApprovalRequest) -> ApprovalRequest:
        expiry = request.expires_at
        if expiry and expiry.astimezone(UTC) <= utc_now() and request.state is ApprovalState.PENDING:
            request = request.model_copy(
                update={"state": ApprovalState.EXPIRED, "decided_at": utc_now()}
            )
            self._save(request)
        return request

    def _save(self, request: ApprovalRequest) -> None:
        self._requests[request.id] = request
        if self._persist:
            self._persist(request)
