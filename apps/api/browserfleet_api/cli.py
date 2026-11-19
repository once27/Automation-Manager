"""Local administration commands for the BrowserFleet API."""

import argparse
import hashlib
import re
import secrets
import sys
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select

from browserfleet_api.db.config import get_database_url
from browserfleet_api.db.models import ApiKey, Tenant
from browserfleet_api.db.session import create_engine, create_session_factory

_BOOTSTRAP_SCOPES = ["workflow:read", "workflow:write"]
_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _text_argument(max_length: int) -> Callable[[str], str]:
    def parse(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise argparse.ArgumentTypeError("must not be empty")
        if len(normalized) > max_length:
            raise argparse.ArgumentTypeError(
                f"must be at most {max_length} characters"
            )
        return normalized

    return parse


def _tenant_slug(value: str) -> str:
    slug = _text_argument(100)(value)
    if not _SLUG_PATTERN.fullmatch(slug):
        raise argparse.ArgumentTypeError(
            "must use lowercase letters, numbers, and single hyphens"
        )
    return slug


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browserfleet-api")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap-tenant",
        help="create or reuse a tenant and issue a one-time client API key",
    )
    bootstrap.add_argument("--tenant-name", required=True, type=_text_argument(160))
    bootstrap.add_argument("--tenant-slug", required=True, type=_tenant_slug)
    bootstrap.add_argument(
        "--key-name", default="local-bootstrap", type=_text_argument(120)
    )
    return parser


def _provision_tenant_key(
    *, tenant_name: str, tenant_slug: str, key_name: str
) -> tuple[UUID, str]:
    """Create or reuse an active tenant and issue one secret API key."""
    raw_key = f"bf_{secrets.token_urlsafe(32)}"
    secret_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    engine = create_engine(get_database_url())
    session_factory = create_session_factory(engine)
    try:
        with session_factory.begin() as session:
            tenant = session.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
            if tenant is None:
                tenant = Tenant(name=tenant_name, slug=tenant_slug)
                session.add(tenant)
                session.flush()
            elif tenant.status != "active":
                raise ValueError(
                    f"Tenant '{tenant_slug}' exists but is not active; "
                    "refusing to issue a key"
                )

            api_key = ApiKey(
                tenant_id=tenant.id,
                name=key_name,
                key_prefix=raw_key[:16],
                secret_hash=secret_hash,
                scopes=list(_BOOTSTRAP_SCOPES),
            )
            session.add(api_key)
            session.flush()
            tenant_id = tenant.id
    finally:
        engine.dispose()

    # Return the secret only after the transaction committed successfully.
    return tenant_id, raw_key


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "bootstrap-tenant":
        try:
            tenant_id, raw_key = _provision_tenant_key(
                tenant_name=args.tenant_name,
                tenant_slug=args.tenant_slug,
                key_name=args.key_name,
            )
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

        print(f"Tenant ID: {tenant_id}")
        print("API key (shown once; store it securely):")
        print(raw_key)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
