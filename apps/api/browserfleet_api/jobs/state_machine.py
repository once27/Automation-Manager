"""FastAPI-independent job states and API transition rules."""

from dataclasses import dataclass
from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    RETRY_PENDING = "RETRY_PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class JobDomainError(Exception):
    """Base class for safe domain failures that an API layer can map to HTTP."""

    code = "JOB_DOMAIN_ERROR"


class JobNotFoundError(JobDomainError):
    """A job does not exist in the requested tenant scope."""

    code = "JOB_NOT_FOUND"


class JobVersionConflictError(JobDomainError):
    """The job changed since the caller read its version."""

    code = "JOB_VERSION_MISMATCH"


class InvalidJobTransitionError(JobDomainError):
    """The requested operation is invalid for the current state."""

    code = "INVALID_JOB_TRANSITION"


class TerminalJobError(JobDomainError):
    """The job is terminal and cannot accept the requested operation."""

    code = "JOB_TERMINAL"


class WorkflowDisabledError(JobDomainError):
    """A disabled workflow cannot be manually retried."""

    code = "WORKFLOW_DISABLED"


class RetryLimitExceededError(JobDomainError):
    """The retry counter cannot be incremented within its database range."""

    code = "RETRY_LIMIT_REACHED"


@dataclass(frozen=True, slots=True)
class StateTransition:
    from_state: JobState
    to_state: JobState
    event_type: str
    finish_job: bool = False


def cancellation_transition(state: str) -> StateTransition | None:
    """Return the API cancellation transition, or None for an idempotent repeat."""
    current = parse_storage_state(state)
    if current is JobState.QUEUED:
        return StateTransition(
            from_state=current,
            to_state=JobState.CANCELLED,
            event_type="job.cancelled",
            finish_job=True,
        )
    if current in (JobState.DISPATCHED, JobState.RUNNING):
        return StateTransition(
            from_state=current,
            to_state=JobState.CANCEL_REQUESTED,
            event_type="job.cancel_requested",
        )
    if current in (JobState.CANCEL_REQUESTED, JobState.CANCELLED):
        return None
    if current in (JobState.SUCCEEDED, JobState.FAILED, JobState.TIMED_OUT):
        raise TerminalJobError("Terminal jobs cannot be cancelled.")
    raise InvalidJobTransitionError("The job cannot be cancelled in its current state.")


def manual_retry_transition(state: str) -> StateTransition:
    """Return the sole API transition that manually retries a failed job."""
    current = parse_storage_state(state)
    if current is JobState.FAILED:
        return StateTransition(
            from_state=current,
            to_state=JobState.QUEUED,
            event_type="job.retry_requested",
        )
    if current in (JobState.SUCCEEDED, JobState.CANCELLED, JobState.TIMED_OUT):
        raise TerminalJobError("Only failed jobs can be manually retried.")
    raise InvalidJobTransitionError("Only failed jobs can be manually retried.")


def parse_storage_state(value: str) -> JobState:
    """Map persisted job states to canonical API/domain identifiers."""
    if value == "CANCELED":
        return JobState.CANCELLED
    try:
        return JobState(value)
    except ValueError as error:
        raise InvalidJobTransitionError("The job has an unsupported state.") from error
