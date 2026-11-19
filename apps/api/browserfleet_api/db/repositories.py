"""Tenant-scoped data access helpers for SQLAlchemy models."""

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute

from browserfleet_api.db.base import Base

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000


class TenantScopedRepository[ModelT: Base]:
    """Read and stage records for one tenant without owning transactions.

    Every read includes the repository's tenant ID. Writes are added to the
    supplied session; callers decide when to flush, commit, or roll back.
    """

    def __init__(self, session: Session, model: type[ModelT], tenant_id: UUID) -> None:
        tenant_column = getattr(model, "tenant_id", None)
        id_column = getattr(model, "id", None)
        if tenant_column is None or id_column is None:
            raise TypeError(f"{model.__name__} is not a tenant-scoped ORM model")

        self._session = session
        self._model = model
        self._tenant_id = tenant_id
        self._tenant_column = cast(InstrumentedAttribute[UUID], tenant_column)
        self._id_column = cast(InstrumentedAttribute[UUID], id_column)

    @property
    def tenant_id(self) -> UUID:
        """Tenant identity this repository applies to each operation."""
        return self._tenant_id

    def get(self, record_id: UUID) -> ModelT | None:
        """Return a record only when both its ID and tenant match."""
        statement = select(self._model).where(
            self._tenant_column == self._tenant_id,
            self._id_column == record_id,
        )
        return self._session.scalars(statement).one_or_none()

    def list(self, *, limit: int = _DEFAULT_LIMIT, offset: int = 0) -> list[ModelT]:
        """List this tenant's records in stable ID order with bounded paging."""
        if not 1 <= limit <= _MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        statement = (
            select(self._model)
            .where(self._tenant_column == self._tenant_id)
            .order_by(self._id_column)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(statement))

    def add(self, record: ModelT) -> ModelT:
        """Stage a record belonging to this tenant in the caller's session."""
        record_tenant_id = getattr(record, "tenant_id", None)
        if record_tenant_id != self._tenant_id:
            raise ValueError("record tenant_id does not match repository tenant_id")
        self._session.add(record)
        return record
