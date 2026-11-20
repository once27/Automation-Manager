"""API-key authentication and tenant-scoped authorization dependencies."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select

from browserfleet_api.db.models import ApiKey, Tenant
from browserfleet_api.errors import APIError

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class APIKeyPrincipal:
    """Tenant and key identity derived only from a valid API key."""

    tenant_id: UUID
    api_key_id: UUID
    scopes: frozenset[str]


def require_api_key(required_scope: str) -> Callable[..., APIKeyPrincipal]:
    """Build a dependency that validates a bearer key and requires one scope."""

    def dependency(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ],
    ) -> APIKeyPrincipal:
        if credentials is None or credentials.scheme.casefold() != "bearer":
            raise APIError(
                status_code=401,
                code="AUTHENTICATION_REQUIRED",
                message="A bearer API key is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        raw_key = credentials.credentials
        if not raw_key or len(raw_key) > 4096:
            raise _invalid_api_key()

        session_factory = request.app.state.session_factory
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        statement = (
            select(ApiKey, Tenant.status)
            .join(Tenant, Tenant.id == ApiKey.tenant_id)
            .where(
                ApiKey.key_prefix == raw_key[:16],
                ApiKey.secret_hash == key_hash,
                ApiKey.revoked_at.is_(None),
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
            )
        )

        with session_factory() as session:
            row = session.execute(statement).one_or_none()

        if row is None or row[1] != "active":
            raise _invalid_api_key()

        api_key = row[0]
        scopes = frozenset(api_key.scopes)
        if required_scope not in scopes:
            raise APIError(
                status_code=403,
                code="INSUFFICIENT_SCOPE",
                message="The API key does not have the required permission.",
            )

        return APIKeyPrincipal(
            tenant_id=api_key.tenant_id,
            api_key_id=api_key.id,
            scopes=scopes,
        )

    return dependency


def _invalid_api_key() -> APIError:
    return APIError(
        status_code=401,
        code="INVALID_API_KEY",
        message="The API key is invalid or inactive.",
        headers={"WWW-Authenticate": "Bearer"},
    )
