"""Transactional persistence operations for job state and event records."""

from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from browserfleet_api.db.models import (
    Job,
    JobEvent,
    OutboxEvent,
    Workflow,
    WorkflowFamily,
)
from browserfleet_api.jobs.state_machine import (
    JobNotFoundError,
    JobVersionConflictError,
    RetryLimitExceededError,
    StateTransition,
    WorkflowDisabledError,
    cancellation_transition,
    manual_retry_transition,
)


def create_queued_job(session: Session, job: Job) -> Job:
    """Stage a new queued job, its initial event, and its pending outbox row.

    The caller must wrap this operation in a transaction. No input, execution,
    result, or credential payload is copied into event or outbox records.
    """
    _require_transaction(session)
    job.status = "QUEUED"
    job.version = 1
    job.attempt_count = 0
    job.assigned_worker_id = None
    job.current_lease_id = None
    job.started_at = None
    job.finished_at = None
    job.result_payload = None
    session.add(job)
    session.flush()

    _append_event_and_outbox(
        session,
        job,
        event_type="job.queued",
        from_status=None,
        to_status="QUEUED",
    )
    return job


def cancel_job(
    session: Session,
    *,
    tenant_id: UUID,
    job_id: UUID,
    expected_version: int,
) -> Job:
    """Cancel a job or durably request cancellation using compare-and-swap."""
    _require_transaction(session)
    job = _get_job(session, tenant_id=tenant_id, job_id=job_id)
    _require_version(job, expected_version)
    transition = cancellation_transition(job.status)
    if transition is None:
        return job

    _apply_transition(
        session,
        job,
        transition=transition,
        expected_version=expected_version,
    )
    return job


def retry_failed_job(
    session: Session,
    *,
    tenant_id: UUID,
    job_id: UUID,
    expected_version: int,
) -> Job:
    """Requeue a failed job once per current version, preserving its history."""
    _require_transaction(session)
    job = _get_job(session, tenant_id=tenant_id, job_id=job_id)
    _require_version(job, expected_version)
    transition = manual_retry_transition(job.status)
    _require_workflow_enabled(session, tenant_id=tenant_id, job=job)

    new_max_attempts = job.max_attempts + 1
    if new_max_attempts > 2_147_483_647:
        raise RetryLimitExceededError("The job has reached the maximum retry limit.")

    _apply_transition(
        session,
        job,
        transition=transition,
        expected_version=expected_version,
        values={"max_attempts": new_max_attempts, "finished_at": None},
    )
    return job


def _apply_transition(
    session: Session,
    job: Job,
    *,
    transition: StateTransition,
    expected_version: int,
    values: dict[str, object] | None = None,
) -> None:
    new_version = expected_version + 1
    update_values: dict[str, object] = {
        "status": transition.to_state.value,
        "version": Job.version + 1,
    }
    if transition.finish_job:
        update_values["finished_at"] = datetime.now(UTC)
    if values is not None:
        update_values.update(values)

    statement = (
        update(Job)
        .where(
            Job.tenant_id == job.tenant_id,
            Job.id == job.id,
            Job.version == expected_version,
            Job.status == transition.from_state.value,
        )
        .values(**update_values)
        .returning(Job.updated_at)
        .execution_options(synchronize_session=False)
    )
    updated_at = session.execute(statement).scalar_one_or_none()
    if updated_at is None:
        _raise_update_conflict(session, tenant_id=job.tenant_id, job_id=job.id)

    job.status = transition.to_state.value
    job.version = new_version
    job.updated_at = updated_at
    if transition.finish_job:
        job.finished_at = update_values["finished_at"]  # type: ignore[assignment]
    if values is not None:
        for field, value in values.items():
            setattr(job, field, value)

    _append_event_and_outbox(
        session,
        job,
        event_type=transition.event_type,
        from_status=transition.from_state.value,
        to_status=transition.to_state.value,
    )


def _append_event_and_outbox(
    session: Session,
    job: Job,
    *,
    event_type: str,
    from_status: str | None,
    to_status: str,
) -> None:
    payload: dict[str, str | int | None] = {
        "from_status": from_status,
        "to_status": to_status,
        "version": job.version,
    }
    session.add(
        JobEvent(
            tenant_id=job.tenant_id,
            job_id=job.id,
            event_type=event_type,
            source="api",
            payload=payload,
        )
    )
    outbox_payload = {
        "tenant_id": str(job.tenant_id),
        "job_id": str(job.id),
        **payload,
    }
    session.add(
        OutboxEvent(
            aggregate_type="job",
            aggregate_id=job.id,
            event_type=event_type,
            payload=outbox_payload,
            status="pending",
        )
    )
    session.flush()


def _get_job(session: Session, *, tenant_id: UUID, job_id: UUID) -> Job:
    job = session.scalar(
        select(Job).where(Job.tenant_id == tenant_id, Job.id == job_id)
    )
    if job is None:
        raise JobNotFoundError("The job was not found.")
    return job


def _require_version(job: Job, expected_version: int) -> None:
    if expected_version < 1 or job.version != expected_version:
        raise JobVersionConflictError("The job has changed since it was read.")


def _raise_update_conflict(
    session: Session, *, tenant_id: UUID, job_id: UUID
) -> NoReturn:
    exists = session.scalar(
        select(Job.id).where(Job.tenant_id == tenant_id, Job.id == job_id)
    )
    if exists is None:
        raise JobNotFoundError("The job was not found.")
    raise JobVersionConflictError("The job has changed since it was read.")


def _require_workflow_enabled(
    session: Session, *, tenant_id: UUID, job: Job
) -> None:
    enabled = session.execute(
        select(Workflow.enabled, WorkflowFamily.enabled)
        .join(
            WorkflowFamily,
            (WorkflowFamily.tenant_id == Workflow.tenant_id)
            & (WorkflowFamily.id == Workflow.workflow_family_id),
        )
        .where(
            Workflow.tenant_id == tenant_id,
            Workflow.id == job.workflow_id,
        )
        .with_for_update()
    ).one_or_none()
    if enabled is None or not all(enabled):
        raise WorkflowDisabledError("The job workflow is disabled.")


def _require_transaction(session: Session) -> None:
    if not session.in_transaction():
        raise RuntimeError("Job persistence operations require an active transaction.")
