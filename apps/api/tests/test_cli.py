"""Tests for local administration command parsing and worker provisioning."""

from uuid import UUID, uuid4

import pytest
from browserfleet_api import cli


def test_provision_worker_command_delegates_and_prints_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tenant_id = uuid4()
    worker_id = uuid4()

    def provision(**kwargs: object) -> tuple[UUID, UUID, str]:
        assert kwargs == {
            "tenant_slug": "example-tenant",
            "worker_name": "linux-worker-01",
            "platform": "linux",
            "environment": "docker",
            "hostname": "worker-host",
            "max_concurrency": 2,
        }
        return tenant_id, worker_id, "bfw_test_machine_credential"

    monkeypatch.setattr(cli, "_provision_worker", provision)

    assert (
        cli.main(
            [
                "provision-worker",
                "--tenant-slug",
                "example-tenant",
                "--worker-name",
                "linux-worker-01",
                "--platform",
                "linux",
                "--environment",
                "docker",
                "--hostname",
                "worker-host",
                "--max-concurrency",
                "2",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert str(tenant_id) in output
    assert str(worker_id) in output
    assert "Worker credential (shown once; store it securely):" in output
    assert "bfw_test_machine_credential" in output


def test_worker_provisioning_rejects_incompatible_platform_before_database_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_database_access(*args: object, **kwargs: object) -> object:
        raise AssertionError("the incompatible input must not access the database")

    monkeypatch.setattr(cli, "create_engine", unexpected_database_access)

    assert (
        cli.main(
            [
                "provision-worker",
                "--tenant-slug",
                "example-tenant",
                "--worker-name",
                "bad-worker",
                "--platform",
                "windows",
                "--environment",
                "docker",
                "--max-concurrency",
                "1",
            ]
        )
        == 1
    )
    assert "platform and environment must be compatible" in capsys.readouterr().err
