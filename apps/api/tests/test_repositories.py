"""Tenant-scoped repository integration tests."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from browserfleet_api.db.models import Tenant, User
from browserfleet_api.db.repositories import TenantScopedRepository
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    """Apply the migration in an isolated temporary PostgreSQL schema."""
    database_url = os.environ.get("BROWSERFLEET_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip(
            "set BROWSERFLEET_TEST_DATABASE_URL to run PostgreSQL integration tests"
        )

    admin_engine = create_engine(database_url)
    schema = f"test_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    config = Config(str(ALEMBIC_INI))
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    config.attributes["version_table_schema"] = schema
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def _create_tenant_users(engine: Engine) -> tuple[Tenant, Tenant, User, User]:
    tenant_a = Tenant(name="Tenant A", slug="tenant-a")
    tenant_b = Tenant(name="Tenant B", slug="tenant-b")
    with Session(engine, expire_on_commit=False) as session:
        session.add_all([tenant_a, tenant_b])
        session.flush()
        user_a = User(tenant_id=tenant_a.id, email="a@example.test", role="owner")
        user_b = User(tenant_id=tenant_b.id, email="b@example.test", role="owner")
        session.add_all([user_a, user_b])
        session.commit()
        return tenant_a, tenant_b, user_a, user_b


def test_repository_reads_are_tenant_scoped_and_paged(migrated_engine: Engine) -> None:
    tenant_a, _, user_a, user_b = _create_tenant_users(migrated_engine)

    with Session(migrated_engine, expire_on_commit=False) as session:
        repository = TenantScopedRepository(session, User, tenant_a.id)

        assert repository.tenant_id == tenant_a.id
        found = repository.get(user_a.id)
        assert found is not None
        assert found.id == user_a.id
        assert repository.get(user_b.id) is None
        assert [user.id for user in repository.list()] == [user_a.id]
        assert repository.list(limit=1, offset=1) == []


def test_repository_rejects_records_from_other_tenants(migrated_engine: Engine) -> None:
    tenant_a, _, _, user_b = _create_tenant_users(migrated_engine)

    with Session(migrated_engine, expire_on_commit=False) as session:
        repository = TenantScopedRepository(session, User, tenant_a.id)

        with pytest.raises(ValueError, match="tenant_id"):
            repository.add(user_b)
        assert user_b not in session

        with pytest.raises(TypeError, match="not a tenant-scoped"):
            TenantScopedRepository(session, Tenant, tenant_a.id)


def test_repository_leaves_commit_boundary_to_caller(migrated_engine: Engine) -> None:
    tenant = Tenant(name="Tenant", slug="tenant")
    with Session(migrated_engine, expire_on_commit=False) as setup_session:
        setup_session.add(tenant)
        setup_session.commit()

    user = User(tenant_id=tenant.id, email="pending@example.test", role="operator")
    committed: list[bool] = []
    with Session(migrated_engine, expire_on_commit=False) as writer:
        def record_commit(_session: Session) -> None:
            committed.append(True)

        event.listen(writer, "after_commit", record_commit)
        repository = TenantScopedRepository(writer, User, tenant.id)

        assert repository.add(user) is user
        assert committed == []
        writer.flush()
        assert committed == []

        with Session(migrated_engine, expire_on_commit=False) as observer:
            observer_repository = TenantScopedRepository(observer, User, tenant.id)
            assert observer_repository.get(user.id) is None

        writer.commit()
        assert committed == [True]

    with Session(migrated_engine, expire_on_commit=False) as observer:
        observer_repository = TenantScopedRepository(observer, User, tenant.id)
        assert observer_repository.get(user.id) is not None
