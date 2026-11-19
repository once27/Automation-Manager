"""Database connection configuration helpers."""

import os

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://browserfleet:browserfleet-local@127.0.0.1:5432/browserfleet"
)


def get_database_url() -> str:
    """Return the configured PostgreSQL URL without logging credentials."""
    return os.environ.get("BROWSERFLEET_DATABASE_URL", DEFAULT_DATABASE_URL)
