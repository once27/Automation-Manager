from pathlib import Path
from uuid import uuid4

import pytest
from browserfleet_worker.config import WorkerConfig, load_config
from pydantic import ValidationError


def config_payload() -> dict[str, object]:
    return {
        "endpoint": "wss://control.example/api/v1/workers/connect",
        "credential": "a" * 32,
        "worker_id": str(uuid4()),
        "worker_name": "linux-01",
        "hostname": "linux-01.example",
        "platform": "linux",
        "environment": "docker",
        "worker_version": "0.1.0",
        "capabilities": {
            "browsers": ["chromium"],
            "execution_modes": ["headless"],
            "session_modes": ["ephemeral"],
            "max_concurrency": 1,
        },
        "resources": {"cpu_cores": 2, "memory_mb": 2048},
    }


def test_config_requires_secure_websocket() -> None:
    payload = config_payload()
    payload["endpoint"] = "ws://localhost:8000/api/v1/workers/connect"

    with pytest.raises(ValidationError, match="wss://"):
        WorkerConfig.model_validate(payload)


def test_load_config_validates_json(tmp_path: Path) -> None:
    config_path = tmp_path / "worker.json"
    config_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(config_path)
