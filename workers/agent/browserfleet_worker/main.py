"""Command-line entry point for the BrowserFleet worker connection agent."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from browserfleet_worker.client import WorkerClient
from browserfleet_worker.config import load_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browserfleet-worker")
    parser.add_argument(
        "--config", required=True, type=Path, help="provisioned worker JSON"
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    return parser


async def _run(config_path: Path) -> None:
    config = load_config(config_path)
    await WorkerClient(config).run()


def main(argv: list[str] | None = None) -> int:
    """Load provisioned configuration and run until the server requests shutdown."""

    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run(args.config))
    except (OSError, ValueError, RuntimeError) as error:
        logging.getLogger(__name__).error("worker stopped: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
