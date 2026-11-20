"""Tenant-scoped workflow family and immutable version registry API."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from browserfleet_api.auth import APIKeyPrincipal, require_api_key
from browserfleet_api.db.models import Workflow, WorkflowFamily
from browserfleet_api.errors import APIError

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

WorkflowReader = Annotated[
    APIKeyPrincipal, Depends(require_api_key("workflow:read"))
]
WorkflowWriter = Annotated[
    APIKeyPrincipal, Depends(require_api_key("workflow:write"))
]


class WorkflowVersionInput(BaseModel):
    """Validated immutable version metadata accepted from a client."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(max_length=40)
    package_ref: str = Field(min_length=1)
    entrypoint: str = Field(max_length=255)
    checksum: str = Field(max_length=128)
    config_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version", "package_ref", "entrypoint", "checksum")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        return normalized


class WorkflowCreate(WorkflowVersionInput):
    """Create a workflow family with its first version atomically."""

    name: str = Field(max_length=160)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be empty")
        return normalized


class WorkflowVersionResponse(BaseModel):
    """Public representation of one immutable workflow version row."""

    id: UUID
    version: str
    package_ref: str
    entrypoint: str
    checksum: str
    config_schema: dict[str, Any]
    enabled: bool
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WorkflowSummary(BaseModel):
    """Public family identity and administrative state."""

    id: UUID
    name: str
    enabled: bool
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WorkflowListResponse(BaseModel):
    """A tenant-scoped page of workflow families."""

    items: list[WorkflowSummary]
    limit: int
    offset: int


class WorkflowDetail(WorkflowSummary):
    """Workflow family and its immutable version rows."""

    versions: list[WorkflowVersionResponse]


def _session(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


DatabaseSession = Annotated[Session, Depends(_session)]


def _family_summary(family: WorkflowFamily) -> WorkflowSummary:
    return WorkflowSummary(
        id=family.id,
        name=family.name,
        enabled=family.enabled,
        created_at=family.created_at,
    )


def _version_response(version: Workflow) -> WorkflowVersionResponse:
    return WorkflowVersionResponse(
        id=version.id,
        version=version.version,
        package_ref=version.package_ref,
        entrypoint=version.entrypoint,
        checksum=version.checksum,
        config_schema=version.config_schema,
        enabled=version.enabled,
        created_at=version.created_at,
    )


def _workflow_detail(
    family: WorkflowFamily, versions: list[Workflow]
) -> WorkflowDetail:
    return WorkflowDetail(
        **_family_summary(family).model_dump(),
        versions=[_version_response(version) for version in versions],
    )


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _raise_integrity_conflict(
    error: IntegrityError, *, name_conflict: bool = False
) -> NoReturn:
    constraint = _constraint_name(error)
    if constraint == "uq_workflow_families_tenant_name":
        raise APIError(
            status_code=409,
            code="WORKFLOW_NAME_CONFLICT",
            message="A workflow with this name already exists for the tenant.",
        ) from error
    if constraint in {
        "uq_workflows_tenant_family_version",
        "uq_workflows_tenant_name_version",
    }:
        raise APIError(
            status_code=409,
            code="WORKFLOW_VERSION_CONFLICT",
            message="This workflow version already exists.",
        ) from error
    if name_conflict:
        raise APIError(
            status_code=409,
            code="WORKFLOW_NAME_CONFLICT",
            message="A workflow with this name already exists for the tenant.",
        ) from error
    raise error


def _get_family(
    session: Session, tenant_id: UUID, workflow_id: UUID, *, lock: bool = False
) -> WorkflowFamily:
    statement = select(WorkflowFamily).where(
        WorkflowFamily.tenant_id == tenant_id,
        WorkflowFamily.id == workflow_id,
    )
    if lock:
        statement = statement.with_for_update()
    family = session.scalar(statement)
    if family is None:
        raise APIError(
            status_code=404,
            code="WORKFLOW_NOT_FOUND",
            message="The workflow was not found.",
        )
    return family


@router.post("", response_model=WorkflowDetail, status_code=status.HTTP_201_CREATED)
def create_workflow(
    body: WorkflowCreate,
    principal: WorkflowWriter,
    session: DatabaseSession,
) -> WorkflowDetail:
    """Create a tenant-owned workflow family and its first immutable version."""
    family = WorkflowFamily(tenant_id=principal.tenant_id, name=body.name)
    try:
        with session.begin():
            session.add(family)
            session.flush()
            version = Workflow(
                tenant_id=principal.tenant_id,
                workflow_family_id=family.id,
                name=family.name,
                version=body.version,
                package_ref=body.package_ref,
                entrypoint=body.entrypoint,
                checksum=body.checksum,
                config_schema=body.config_schema,
            )
            session.add(version)
            session.flush()
            session.refresh(family)
            session.refresh(version)
            result = _workflow_detail(family, [version])
    except IntegrityError as error:
        _raise_integrity_conflict(error)
    return result


@router.get("", response_model=WorkflowListResponse)
def list_workflows(
    principal: WorkflowReader,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkflowListResponse:
    """List only workflow families belonging to the authenticated tenant."""
    statement = (
        select(WorkflowFamily)
        .where(WorkflowFamily.tenant_id == principal.tenant_id)
        .order_by(WorkflowFamily.id)
        .limit(limit)
        .offset(offset)
    )
    families = list(session.scalars(statement))
    return WorkflowListResponse(
        items=[_family_summary(family) for family in families],
        limit=limit,
        offset=offset,
    )


@router.get("/{workflow_id}", response_model=WorkflowDetail)
def get_workflow(
    workflow_id: UUID,
    principal: WorkflowReader,
    session: DatabaseSession,
) -> WorkflowDetail:
    """Get a family and its versions without revealing cross-tenant records."""
    family = _get_family(session, principal.tenant_id, workflow_id)
    versions = list(
        session.scalars(
            select(Workflow)
            .where(
                Workflow.tenant_id == principal.tenant_id,
                Workflow.workflow_family_id == family.id,
            )
            .order_by(Workflow.created_at, Workflow.id)
        )
    )
    return _workflow_detail(family, versions)


@router.post(
    "/{workflow_id}/versions",
    response_model=WorkflowVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_version(
    workflow_id: UUID,
    body: WorkflowVersionInput,
    principal: WorkflowWriter,
    session: DatabaseSession,
) -> WorkflowVersionResponse:
    """Add an immutable version to an active tenant-owned family."""
    try:
        with session.begin():
            family = _get_family(
                session, principal.tenant_id, workflow_id, lock=True
            )
            if not family.enabled:
                raise APIError(
                    status_code=409,
                    code="WORKFLOW_DISABLED",
                    message="A disabled workflow cannot receive new versions.",
                )
            version = Workflow(
                tenant_id=principal.tenant_id,
                workflow_family_id=family.id,
                name=family.name,
                version=body.version,
                package_ref=body.package_ref,
                entrypoint=body.entrypoint,
                checksum=body.checksum,
                config_schema=body.config_schema,
            )
            session.add(version)
            session.flush()
            session.refresh(version)
            result = _version_response(version)
    except IntegrityError as error:
        _raise_integrity_conflict(error)
    return result


@router.post("/{workflow_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
def disable_workflow(
    workflow_id: UUID,
    principal: WorkflowWriter,
    session: DatabaseSession,
) -> Response:
    """Disable a family without mutating historical versions or jobs."""
    with session.begin():
        family = _get_family(session, principal.tenant_id, workflow_id, lock=True)
        family.enabled = False
    return Response(status_code=status.HTTP_204_NO_CONTENT)
