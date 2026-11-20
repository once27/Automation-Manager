"""Validated request and response contracts for the job API."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)

from browserfleet_api.jobs.state_machine import JobState


def _utc_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("must not be empty")
    return normalized


class WorkflowReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=160), AfterValidator(_required_text)]
    version: Annotated[str, Field(min_length=1, max_length=40), AfterValidator(_required_text)]


class JobSubmission(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "workflow": {
                        "name": "invoice_extraction",
                        "version": "1.2.0",
                    },
                    "execution": {
                        "browser": "chromium",
                        "mode": "headless",
                        "session": {"mode": "ephemeral"},
                        "timeout_seconds": 300,
                    },
                    "requirements": {
                        "platforms": ["linux"],
                        "environments": ["docker"],
                        "worker_pool": "linux-general",
                        "min_memory_mb": 2048,
                    },
                    "input": {"record_id": "invoice-123"},
                    "retry_policy": {
                        "max_attempts": 2,
                        "initial_backoff_seconds": 10,
                        "max_backoff_seconds": 120,
                    },
                    "artifacts": {
                        "screenshots": "on_failure",
                        "trace": "on_failure",
                        "video": False,
                    },
                }
            ]
        },
    )

    workflow: WorkflowReference
    execution: dict[str, Any]
    requirements: dict[str, Any]
    input: dict[str, Any]
    retry_policy: dict[str, Any]
    artifacts: dict[str, Any] = Field(default_factory=dict)

    @field_validator("execution")
    @classmethod
    def validate_execution(cls, value: dict[str, Any]) -> dict[str, Any]:
        timeout = value.get("timeout_seconds", 300)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 2_147_483_647
        ):
            raise ValueError(
                "timeout_seconds must be a positive 32-bit integer"
            )
        return value

    @field_validator("retry_policy")
    @classmethod
    def validate_retry_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        max_attempts = value.get("max_attempts", 1)
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 2_147_483_647
        ):
            raise ValueError("max_attempts must be a positive 32-bit integer")
        return value


class JobLinks(BaseModel):
    self: str
    events: str


class JobAccepted(BaseModel):
    id: UUID
    status: JobState
    created_at: datetime
    version: int
    links: JobLinks

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return _utc_string(value)


class JobSummary(BaseModel):
    id: UUID
    workflow_family_id: UUID
    workflow_name: str
    workflow_version_id: UUID
    workflow_version: str
    status: JobState
    attempt_count: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    version: int
    links: JobLinks

    @field_serializer("created_at", "updated_at", when_used="json")
    def serialize_timestamps(self, value: datetime) -> str:
        return _utc_string(value)


class JobListResponse(BaseModel):
    items: list[JobSummary]
    limit: int
    offset: int


class JobDetailResponse(JobSummary):
    execution: dict[str, Any]
    requirements: dict[str, Any]
    input_payload: dict[str, Any] = Field(serialization_alias="input")
    retry_policy: dict[str, Any]
    artifacts: dict[str, Any]
    result_payload: dict[str, Any] | None = Field(serialization_alias="result")


class JobEventResponse(BaseModel):
    id: UUID
    event_type: str
    source: str
    attempt_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        return _utc_string(value)


class JobEventListResponse(BaseModel):
    items: list[JobEventResponse]
    limit: int
    offset: int


class JobAttemptResponse(BaseModel):
    id: UUID
    attempt_number: int
    worker_id: UUID | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    outcome_unknown: bool

    @field_serializer("started_at", "finished_at", when_used="json")
    def serialize_timestamps(self, value: datetime | None) -> str | None:
        return None if value is None else _utc_string(value)


class JobAttemptListResponse(BaseModel):
    items: list[JobAttemptResponse]
    limit: int
    offset: int
