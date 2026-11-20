"""Tenant-scoped job submission and lifecycle API."""

from collections.abc import Generator
from typing import Annotated, Any, NoReturn, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from browserfleet_api.auth import APIKeyPrincipal, require_api_key
from browserfleet_api.db.models import (
    Job,
    JobAttempt,
    JobEvent,
    Workflow,
    WorkflowFamily,
)
from browserfleet_api.errors import APIError
from browserfleet_api.jobs.schemas import (
    JobAccepted,
    JobAttemptListResponse,
    JobAttemptResponse,
    JobDetailResponse,
    JobEventListResponse,
    JobEventResponse,
    JobLinks,
    JobListResponse,
    JobSubmission,
    JobSummary,
)
from browserfleet_api.jobs.service import (
    cancel_job,
    create_queued_job,
    retry_failed_job,
)
from browserfleet_api.jobs.state_machine import (
    InvalidJobTransitionError,
    JobDomainError,
    JobNotFoundError,
    JobState,
    JobVersionConflictError,
    RetryLimitExceededError,
    TerminalJobError,
    WorkflowDisabledError,
    parse_storage_state,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

JobReader = Annotated[APIKeyPrincipal, Depends(require_api_key("job:read"))]
JobWriter = Annotated[APIKeyPrincipal, Depends(require_api_key("job:write"))]

_SAFE_EVENT_FIELDS = frozenset({"from_status", "to_status", "version"})


class JobMutation(Protocol):
    def __call__(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        job_id: UUID,
        expected_version: int,
    ) -> Job: ...


def _session(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def _workflow_for_request(
    session: Session, *, tenant_id: UUID, request: JobSubmission
) -> tuple[Workflow, WorkflowFamily]:
    row = session.execute(
        select(Workflow, WorkflowFamily)
        .join(
            WorkflowFamily,
            (WorkflowFamily.tenant_id == Workflow.tenant_id)
            & (WorkflowFamily.id == Workflow.workflow_family_id),
        )
        .where(
            Workflow.tenant_id == tenant_id,
            Workflow.name == request.workflow.name,
            Workflow.version == request.workflow.version,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise APIError(
            status_code=404,
            code="WORKFLOW_VERSION_NOT_FOUND",
            message="The requested workflow version was not found.",
        )
    return row[0], row[1]


def _workflow_for_job(session: Session, job: Job) -> tuple[Workflow, WorkflowFamily]:
    row = session.execute(
        select(Workflow, WorkflowFamily)
        .join(
            WorkflowFamily,
            (WorkflowFamily.tenant_id == Workflow.tenant_id)
            & (WorkflowFamily.id == Workflow.workflow_family_id),
        )
        .where(
            Workflow.tenant_id == job.tenant_id,
            Workflow.id == job.workflow_id,
        )
    ).one_or_none()
    if row is None:
        raise JobNotFoundError("The job was not found.")
    return row[0], row[1]


def _normalize_submission(
    request: JobSubmission,
) -> tuple[dict[str, Any], int, dict[str, Any], int]:
    execution = dict(request.execution)
    timeout_seconds = execution.get("timeout_seconds", 300)
    execution["timeout_seconds"] = timeout_seconds

    retry_policy = dict(request.retry_policy)
    max_attempts = retry_policy.get("max_attempts", 1)
    retry_policy["max_attempts"] = max_attempts
    return execution, timeout_seconds, retry_policy, max_attempts


def _matches_idempotent_request(
    job: Job,
    workflow: Workflow,
    family: WorkflowFamily,
    request: JobSubmission,
    *,
    execution: dict[str, Any],
    timeout_seconds: int,
    retry_policy: dict[str, Any],
) -> bool:
    return (
        family.name == request.workflow.name
        and workflow.version == request.workflow.version
        and job.workflow_id == workflow.id
        and job.execution_config == execution
        and job.timeout_seconds == timeout_seconds
        and job.requirements == request.requirements
        and job.input_payload == request.input
        and job.retry_policy == retry_policy
        and job.artifact_policy == request.artifacts
    )


def _job_by_idempotency_key(
    session: Session, *, tenant_id: UUID, idempotency_key: str
) -> tuple[Job, Workflow, WorkflowFamily] | None:
    job = session.scalar(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.idempotency_key == idempotency_key,
        )
    )
    if job is None:
        return None
    workflow, family = _workflow_for_job(session, job)
    return job, workflow, family


def _accepted(job: Job) -> JobAccepted:
    return JobAccepted(
        id=job.id,
        status=parse_storage_state(job.status),
        created_at=job.created_at,
        version=job.version,
        links=_links(job.id),
    )


def _links(job_id: UUID) -> JobLinks:
    path = f"/api/v1/jobs/{job_id}"
    return JobLinks(self=path, events=f"{path}/events")


def _summary(job: Job, workflow: Workflow, family: WorkflowFamily) -> JobSummary:
    return JobSummary(
        id=job.id,
        workflow_family_id=family.id,
        workflow_name=family.name,
        workflow_version_id=workflow.id,
        workflow_version=workflow.version,
        status=parse_storage_state(job.status),
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
        version=job.version,
        links=_links(job.id),
    )


def _detail(job: Job, workflow: Workflow, family: WorkflowFamily) -> JobDetailResponse:
    return JobDetailResponse(
        **_summary(job, workflow, family).model_dump(),
        execution=job.execution_config,
        requirements=job.requirements,
        input_payload=job.input_payload,
        retry_policy=job.retry_policy,
        artifacts=job.artifact_policy,
        result_payload=job.result_payload,
    )


def _redacted_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key in _SAFE_EVENT_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key in {"from_status", "to_status"}:
            if value is None:
                redacted[key] = None
            elif isinstance(value, str):
                try:
                    redacted[key] = parse_storage_state(value).value
                except InvalidJobTransitionError:
                    continue
        elif key == "version" and isinstance(value, int) and not isinstance(value, bool):
            redacted[key] = value
    return redacted


def _job_context(
    session: Session, *, tenant_id: UUID, job_id: UUID
) -> tuple[Job, Workflow, WorkflowFamily]:
    row = session.execute(
        select(Job, Workflow, WorkflowFamily)
        .join(
            Workflow,
            (Workflow.tenant_id == Job.tenant_id)
            & (Workflow.id == Job.workflow_id),
        )
        .join(
            WorkflowFamily,
            (WorkflowFamily.tenant_id == Workflow.tenant_id)
            & (WorkflowFamily.id == Workflow.workflow_family_id),
        )
        .where(Job.tenant_id == tenant_id, Job.id == job_id)
    ).one_or_none()
    if row is None:
        raise JobNotFoundError("The job was not found.")
    return row[0], row[1], row[2]


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _raise_idempotency_conflict() -> NoReturn:
    raise APIError(
        status_code=409,
        code="IDEMPOTENCY_KEY_REUSED",
        message="The idempotency key was already used for a different request.",
    )


def _resolve_idempotency_header(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise APIError(
            status_code=422,
            code="INVALID_IDEMPOTENCY_KEY",
            message="The idempotency key must contain 1 to 200 characters.",
        )
    return normalized


def _raise_job_error(error: JobDomainError) -> NoReturn:
    if isinstance(error, JobNotFoundError):
        raise APIError(
            status_code=404,
            code="JOB_NOT_FOUND",
            message="The job was not found.",
        ) from error
    if isinstance(error, JobVersionConflictError):
        raise APIError(
            status_code=412,
            code="JOB_VERSION_MISMATCH",
            message="The job has changed since it was read.",
        ) from error
    if isinstance(error, WorkflowDisabledError):
        raise APIError(
            status_code=409,
            code="WORKFLOW_DISABLED",
            message="The job workflow is disabled.",
        ) from error
    if isinstance(error, RetryLimitExceededError):
        raise APIError(
            status_code=409,
            code="RETRY_LIMIT_REACHED",
            message="The job has reached the maximum retry limit.",
        ) from error
    if isinstance(error, TerminalJobError):
        raise APIError(
            status_code=409,
            code="JOB_TERMINAL",
            message="The job is in a terminal state.",
        ) from error
    if isinstance(error, InvalidJobTransitionError):
        raise APIError(
            status_code=409,
            code="INVALID_JOB_TRANSITION",
            message="The requested transition is not valid for this job.",
        ) from error
    raise APIError(
        status_code=409,
        code=error.code,
        message="The job operation could not be completed.",
    ) from error


def _parse_if_match(value: str | None) -> int:
    if value is None:
        raise APIError(
            status_code=428,
            code="PRECONDITION_REQUIRED",
            message="If-Match must contain the current job version.",
        )
    normalized = value.strip()
    if len(normalized) < 3 or normalized[0] != '"' or normalized[-1] != '"':
        raise APIError(
            status_code=400,
            code="INVALID_IF_MATCH",
            message="If-Match must be one strong job version ETag.",
        )
    version_text = normalized[1:-1]
    if (
        not version_text.isascii()
        or not version_text.isdecimal()
        or len(version_text) > 19
        or version_text.startswith("0")
    ):
        raise APIError(
            status_code=400,
            code="INVALID_IF_MATCH",
            message="If-Match must be one strong job version ETag.",
        )
    version = int(version_text)
    if version < 1:
        raise APIError(
            status_code=400,
            code="INVALID_IF_MATCH",
            message="If-Match must be one strong job version ETag.",
        )
    return version


def _set_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


@router.post("", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
def submit_job(
    body: JobSubmission,
    principal: JobWriter,
    session: Annotated[Session, Depends(_session)],
    response: Response,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobAccepted:
    """Create a queued job or replay an identical tenant-scoped request."""
    idempotency_key = _resolve_idempotency_header(idempotency_header)
    execution, timeout_seconds, retry_policy, max_attempts = _normalize_submission(body)
    result: JobAccepted

    try:
        with session.begin():
            if idempotency_key is not None:
                existing = _job_by_idempotency_key(
                    session,
                    tenant_id=principal.tenant_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    job, workflow, family = existing
                    if not _matches_idempotent_request(
                        job,
                        workflow,
                        family,
                        body,
                        execution=execution,
                        timeout_seconds=timeout_seconds,
                        retry_policy=retry_policy,
                    ):
                        _raise_idempotency_conflict()
                    result = _accepted(job)
                else:
                    result = _create_job(
                        session,
                        body,
                        tenant_id=principal.tenant_id,
                        idempotency_key=idempotency_key,
                        execution=execution,
                        timeout_seconds=timeout_seconds,
                        retry_policy=retry_policy,
                        max_attempts=max_attempts,
                    )
            else:
                result = _create_job(
                    session,
                    body,
                    tenant_id=principal.tenant_id,
                    idempotency_key=None,
                    execution=execution,
                    timeout_seconds=timeout_seconds,
                    retry_policy=retry_policy,
                    max_attempts=max_attempts,
                )
    except IntegrityError as error:
        if (
            idempotency_key is None
            or _constraint_name(error) != "uq_jobs_tenant_idempotency_key"
        ):
            raise
        with session.begin():
            existing = _job_by_idempotency_key(
                session,
                tenant_id=principal.tenant_id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            job, workflow, family = existing
            if not _matches_idempotent_request(
                job,
                workflow,
                family,
                body,
                execution=execution,
                timeout_seconds=timeout_seconds,
                retry_policy=retry_policy,
            ):
                _raise_idempotency_conflict()
            result = _accepted(job)

    _set_etag(response, result.version)
    return result


def _create_job(
    session: Session,
    request: JobSubmission,
    *,
    tenant_id: UUID,
    idempotency_key: str | None,
    execution: dict[str, Any],
    timeout_seconds: int,
    retry_policy: dict[str, Any],
    max_attempts: int,
) -> JobAccepted:
    workflow, family = _workflow_for_request(
        session, tenant_id=tenant_id, request=request
    )
    if not family.enabled or not workflow.enabled:
        raise APIError(
            status_code=409,
            code="WORKFLOW_DISABLED",
            message="A disabled workflow cannot receive new jobs.",
        )

    job = Job(
        tenant_id=tenant_id,
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        idempotency_key=idempotency_key,
        requirements=request.requirements,
        execution_config=execution,
        artifact_policy=request.artifacts,
        input_payload=request.input,
        retry_policy=retry_policy,
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
    )
    create_queued_job(session, job)
    session.refresh(job)
    return _accepted(job)


@router.get("", response_model=JobListResponse)
def list_jobs(
    principal: JobReader,
    session: Annotated[Session, Depends(_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    job_status: Annotated[JobState | None, Query(alias="status")] = None,
    workflow_id: Annotated[
        UUID | None,
        Query(description="Filter by the stable tenant-scoped workflow family ID."),
    ] = None,
) -> JobListResponse:
    """List jobs for the authenticated tenant with bounded pagination."""
    statement = (
        select(Job, Workflow, WorkflowFamily)
        .join(
            Workflow,
            (Workflow.tenant_id == Job.tenant_id)
            & (Workflow.id == Job.workflow_id),
        )
        .join(
            WorkflowFamily,
            (WorkflowFamily.tenant_id == Workflow.tenant_id)
            & (WorkflowFamily.id == Workflow.workflow_family_id),
        )
        .where(Job.tenant_id == principal.tenant_id)
    )
    if job_status is not None:
        if job_status is JobState.CANCELLED:
            statement = statement.where(Job.status.in_(("CANCELLED", "CANCELED")))
        else:
            statement = statement.where(Job.status == job_status.value)
    if workflow_id is not None:
        statement = statement.where(Workflow.workflow_family_id == workflow_id)
    statement = statement.order_by(Job.created_at, Job.id).limit(limit).offset(offset)
    rows = session.execute(statement).all()
    return JobListResponse(
        items=[_summary(row[0], row[1], row[2]) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(
    job_id: UUID,
    principal: JobReader,
    session: Annotated[Session, Depends(_session)],
    response: Response,
) -> JobDetailResponse:
    """Return one tenant-owned job and its submitted request fields."""
    try:
        job, workflow, family = _job_context(
            session, tenant_id=principal.tenant_id, job_id=job_id
        )
    except JobDomainError as error:
        _raise_job_error(error)
    result = _detail(job, workflow, family)
    _set_etag(response, job.version)
    return result


@router.get("/{job_id}/events", response_model=JobEventListResponse)
def list_job_events(
    job_id: UUID,
    principal: JobReader,
    session: Annotated[Session, Depends(_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobEventListResponse:
    """List redacted events for a tenant-owned job."""
    try:
        _job_context(session, tenant_id=principal.tenant_id, job_id=job_id)
    except JobDomainError as error:
        _raise_job_error(error)
    rows = session.scalars(
        select(JobEvent)
        .where(
            JobEvent.tenant_id == principal.tenant_id,
            JobEvent.job_id == job_id,
        )
        .order_by(JobEvent.created_at, JobEvent.id)
        .limit(limit)
        .offset(offset)
    )
    return JobEventListResponse(
        items=[
            JobEventResponse(
                id=event.id,
                event_type=event.event_type,
                source=event.source,
                attempt_id=event.attempt_id,
                payload=_redacted_event_payload(event.payload),
                created_at=event.created_at,
            )
            for event in rows
        ],
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}/attempts", response_model=JobAttemptListResponse)
def list_job_attempts(
    job_id: UUID,
    principal: JobReader,
    session: Annotated[Session, Depends(_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobAttemptListResponse:
    """List safe attempt metadata; queued Phase 2 jobs have no attempts yet."""
    try:
        _job_context(session, tenant_id=principal.tenant_id, job_id=job_id)
    except JobDomainError as error:
        _raise_job_error(error)
    rows = session.scalars(
        select(JobAttempt)
        .where(
            JobAttempt.tenant_id == principal.tenant_id,
            JobAttempt.job_id == job_id,
        )
        .order_by(JobAttempt.attempt_number)
        .limit(limit)
        .offset(offset)
    )
    return JobAttemptListResponse(
        items=[
            JobAttemptResponse(
                id=attempt.id,
                attempt_number=attempt.attempt_number,
                worker_id=attempt.worker_id,
                status=attempt.status,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                error_code=attempt.error_code,
                outcome_unknown=attempt.outcome_unknown,
            )
            for attempt in rows
        ],
        limit=limit,
        offset=offset,
    )


def _mutate_job(
    operation: JobMutation,
    *,
    session: Session,
    tenant_id: UUID,
    job_id: UUID,
    expected_version: int,
) -> JobDetailResponse:
    try:
        with session.begin():
            job = operation(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                expected_version=expected_version,
            )
            workflow, family = _workflow_for_job(session, job)
            result = _detail(job, workflow, family)
    except JobDomainError as error:
        _raise_job_error(error)
    return result


@router.post(
    "/{job_id}/cancel",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_job_route(
    job_id: UUID,
    principal: JobWriter,
    session: Annotated[Session, Depends(_session)],
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JobDetailResponse:
    """Cancel a queued job or durably request worker cancellation."""
    result = _mutate_job(
        cancel_job,
        session=session,
        tenant_id=principal.tenant_id,
        job_id=job_id,
        expected_version=_parse_if_match(if_match),
    )
    _set_etag(response, result.version)
    return result


@router.post(
    "/{job_id}/retry",
    response_model=JobDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job_route(
    job_id: UUID,
    principal: JobWriter,
    session: Annotated[Session, Depends(_session)],
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JobDetailResponse:
    """Requeue one failed job after a current-version precondition."""
    result = _mutate_job(
        retry_failed_job,
        session=session,
        tenant_id=principal.tenant_id,
        job_id=job_id,
        expected_version=_parse_if_match(if_match),
    )
    _set_etag(response, result.version)
    return result
