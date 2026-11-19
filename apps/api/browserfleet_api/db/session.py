"""SQLAlchemy engine and session factory helpers."""

from collections.abc import Mapping

from sqlalchemy import Engine
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_engine(
    database_url: str,
    *,
    engine_options: Mapping[str, object] | None = None,
) -> Engine:
    """Create a SQLAlchemy engine without connecting until first use."""
    return sqlalchemy_create_engine(database_url, **(dict(engine_options or {})))


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a factory that leaves transaction boundaries to callers."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
