"""Explicit PatchPilot lifecycle state machine."""

from __future__ import annotations

from collections.abc import Callable

from patchpilot.models import AgentState


class InvalidStateTransition(ValueError):
    """Raised when a run attempts a silent or unsupported state jump."""


ALLOWED_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.CREATED: frozenset({AgentState.ANALYZING, AgentState.CANCELLED, AgentState.FAILED}),
    AgentState.ANALYZING: frozenset({AgentState.PLANNING, AgentState.CANCELLED, AgentState.FAILED}),
    AgentState.PLANNING: frozenset(
        {
            AgentState.AWAITING_APPROVAL,
            AgentState.PREPARING_WORKSPACE,
            AgentState.CANCELLED,
            AgentState.FAILED,
        }
    ),
    AgentState.AWAITING_APPROVAL: frozenset(
        {
            AgentState.PREPARING_WORKSPACE,
            AgentState.REVIEWING_PATCH,
            AgentState.READY,
            AgentState.CANCELLED,
            AgentState.FAILED,
        }
    ),
    AgentState.PREPARING_WORKSPACE: frozenset(
        {AgentState.EDITING, AgentState.CANCELLED, AgentState.FAILED}
    ),
    AgentState.EDITING: frozenset({AgentState.VALIDATING, AgentState.CANCELLED, AgentState.FAILED}),
    AgentState.VALIDATING: frozenset(
        {AgentState.TESTING, AgentState.EDITING, AgentState.CANCELLED, AgentState.FAILED}
    ),
    AgentState.TESTING: frozenset(
        {
            AgentState.REVIEWING_PATCH,
            AgentState.EDITING,
            AgentState.CANCELLED,
            AgentState.FAILED,
        }
    ),
    AgentState.REVIEWING_PATCH: frozenset(
        {
            AgentState.READY,
            AgentState.AWAITING_APPROVAL,
            AgentState.EDITING,
            AgentState.CANCELLED,
            AgentState.FAILED,
        }
    ),
    AgentState.READY: frozenset(),
    AgentState.FAILED: frozenset(),
    AgentState.CANCELLED: frozenset(),
}


class AgentStateMachine:
    """Reject invalid transitions and notify persistence after every change."""

    def __init__(
        self,
        initial: AgentState = AgentState.CREATED,
        on_transition: Callable[[AgentState, AgentState], None] | None = None,
    ) -> None:
        self._state = initial
        self._on_transition = on_transition

    @property
    def state(self) -> AgentState:
        return self._state

    def can_transition(self, target: AgentState) -> bool:
        return target in ALLOWED_TRANSITIONS[self._state]

    def transition(self, target: AgentState) -> AgentState:
        previous = self._state
        if not self.can_transition(target):
            raise InvalidStateTransition(f"cannot transition from {previous} to {target}")
        self._state = target
        if self._on_transition:
            self._on_transition(previous, target)
        return target
