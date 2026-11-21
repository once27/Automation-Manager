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
from browserfleet_api.db.models import ApiKey, Tenant, Worker, WorkerCredential
from browserfleet_api.db.session import create_engine, create_session_factory

_BOOTSTRAP_SCOPES = ("workflow:read", "workflow:write")
_AVAILABLE_SCOPES = (
    "workflow:read",
    "workflow:write",
    "job:read",
    "job:write",
    "worker:read",
    "worker:write",
)
_WORKER_PLATFORMS = ("linux", "windows")
_WORKER_ENVIRONMENTS = ("docker", "linux_headed", "windows_native")
_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _text_argument(max_length: int) -> Callable[[str], str]:
    def parse(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise argparse.ArgumentTypeError("must not be empty")
        if len(normalized) > max_length:
            raise argparse.ArgumentTypeError(f"must be at most {max_length} characters")
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
    bootstrap.add_argument(
        "--scope",
        dest="scopes",
        action="append",
        choices=_AVAILABLE_SCOPES,
        default=None,
        help=(
            "scope to grant (repeat for multiple scopes; "
            "defaults to workflow read/write)"
        ),
    )
    provision_worker = commands.add_parser(
        "provision-worker",
        help="create a worker identity and issue a one-time worker credential",
    )
    provision_worker.add_argument("--tenant-slug", required=True, type=_tenant_slug)
    provision_worker.add_argument(
        "--worker-name", required=True, type=_text_argument(120)
    )
    provision_worker.add_argument(
        "--platform", required=True, choices=_WORKER_PLATFORMS
    )
    provision_worker.add_argument(
        "--environment", required=True, choices=_WORKER_ENVIRONMENTS
    )
    provision_worker.add_argument("--hostname", type=_text_argument(255))
    provision_worker.add_argument(
        "--max-concurrency", required=True, type=_positive_int
    )
    return parser


def _provision_tenant_key(
    *,
    tenant_name: str,
    tenant_slug: str,
    key_name: str,
    scopes: list[str] | None = None,
) -> tuple[UUID, str]:
    """Create or reuse an active tenant and issue one secret API key."""
    granted_scopes = _select_scopes(scopes)
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
                scopes=granted_scopes,
            )
            session.add(api_key)
            session.flush()
            tenant_id = tenant.id
    finally:
        engine.dispose()

    # Return the secret only after the transaction committed successfully.
    return tenant_id, raw_key


def _select_scopes(scopes: list[str] | None) -> list[str]:
    if scopes is None:
        return list(_BOOTSTRAP_SCOPES)
    if len(scopes) != len(set(scopes)):
        raise ValueError("scope values must not be repeated")
    if any(scope not in _AVAILABLE_SCOPES for scope in scopes):
        raise ValueError("one or more scope values are not supported")
    return list(scopes)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _provision_worker(
    *,
    tenant_slug: str,
    worker_name: str,
    platform: str,
    environment: str,
    hostname: str | None,
    max_concurrency: int,
) -> tuple[UUID, UUID, str]:
    """Create an offline worker and return its one-time machine credential."""
    if (platform == "linux") != (environment in {"docker", "linux_headed"}):
        raise ValueError("platform and environment must be compatible")

    raw_credential = f"bfw_{secrets.token_urlsafe(32)}"
    secret_hash = hashlib.sha256(raw_credential.encode("utf-8")).hexdigest()
    engine = create_engine(get_database_url())
    session_factory = create_session_factory(engine)
    try:
        with session_factory.begin() as session:
            tenant = session.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
            if tenant is None:
                raise ValueError(
                    f"Tenant '{tenant_slug}' does not exist; bootstrap it before provisioning a worker"
                )
            if tenant.status != "active":
                raise ValueError(
                    f"Tenant '{tenant_slug}' exists but is not active; refusing to provision a worker"
                )

            worker = Worker(
                tenant_id=tenant.id,
                name=worker_name,
                hostname=hostname,
                platform=platform,
                environment=environment,
                status="OFFLINE",
                max_concurrency=max_concurrency,
            )
            session.add(worker)
            session.flush()
            session.add(
                WorkerCredential(
                    tenant_id=tenant.id,
                    worker_id=worker.id,
                    key_prefix=raw_credential[:16],
                    secret_hash=secret_hash,
                )
            )
            tenant_id = tenant.id
            worker_id = worker.id
    finally:
        engine.dispose()

    # Return the machine secret only after its transaction commits.
    return tenant_id, worker_id, raw_credential


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "bootstrap-tenant":
        try:
            tenant_id, raw_key = _provision_tenant_key(
                tenant_name=args.tenant_name,
                tenant_slug=args.tenant_slug,
                key_name=args.key_name,
                scopes=args.scopes,
            )
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

        print(f"Tenant ID: {tenant_id}")
        print("API key (shown once; store it securely):")
        print(raw_key)
        return 0

    if args.command == "provision-worker":
        try:
            tenant_id, worker_id, raw_credential = _provision_worker(
                tenant_slug=args.tenant_slug,
                worker_name=args.worker_name,
                platform=args.platform,
                environment=args.environment,
                hostname=args.hostname,
                max_concurrency=args.max_concurrency,
            )
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

        print(f"Tenant ID: {tenant_id}")
        print(f"Worker ID: {worker_id}")
        print("Worker credential (shown once; store it securely):")
        print(raw_credential)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
