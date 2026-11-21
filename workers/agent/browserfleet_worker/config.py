"""Validated, provisioned worker-agent configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


class WorkerCapabilities(BaseModel):
    """Capabilities advertised during registration, not scheduler authority."""

    model_config = ConfigDict(extra="forbid")

    browsers: list[str] = Field(min_length=1, max_length=8)
    execution_modes: list[Literal["headless", "headed"]] = Field(
        min_length=1, max_length=2
    )
    session_modes: list[Literal["ephemeral", "persistent"]] = Field(
        min_length=1, max_length=2
    )
    max_concurrency: int = Field(ge=1, le=256)
    workflow_tags: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("browsers", "workflow_tags")
    @classmethod
    def unique_nonempty_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(
            set(normalized)
        ):
            raise ValueError("values must be non-empty and unique")
        return normalized


class WorkerResources(BaseModel):
    """Static resource capacity advertised at registration."""

    model_config = ConfigDict(extra="forbid")

    cpu_cores: int = Field(ge=1, le=4096)
    memory_mb: int = Field(ge=128, le=16_777_216)


class WorkerConfig(BaseModel):
    """Machine-local configuration produced by worker provisioning."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=2048)
    credential: SecretStr = Field(min_length=20, max_length=4096, repr=False)
    worker_id: UUID
    worker_name: str = Field(min_length=1, max_length=120)
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    platform: Literal["linux", "windows"]
    environment: Literal["docker", "linux_headed", "windows_native"]
    worker_version: str = Field(min_length=1, max_length=30)
    protocol_version: Literal["v1"] = "v1"
    capabilities: WorkerCapabilities
    resources: WorkerResources

    @field_validator("endpoint")
    @classmethod
    def require_secure_websocket(cls, value: str) -> str:
        if not value.startswith("wss://"):
            raise ValueError("endpoint must use wss://")
        return value.rstrip("/")

    @field_validator("worker_name", "hostname", "worker_version")
    @classmethod
    def strip_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_concurrency_match(self) -> WorkerConfig:
        if self.capabilities.max_concurrency < 1:
            raise ValueError("capabilities.max_concurrency must be positive")
        return self


def load_config(path: Path) -> WorkerConfig:
    """Load a local JSON provisioned-worker configuration without logging it."""

    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except OSError as error:
        raise ValueError(f"unable to read config file: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"config file is not valid JSON: {path}") from error
    return WorkerConfig.model_validate(payload)
