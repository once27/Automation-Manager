# Repository Guidelines

## Agent Workflow & Work Records

Use the matching specialist defined in `C:\Users\soham\.codex\agents\` for each task. Available specialists cover architecture, backend, database, frontend, worker runtime, DevOps, documentation, QA, security, bug fixing, and code review. Match the work to the specialist description; involve multiple specialists only when the scope crosses specialties or calls for independent review. Keep work sequential and focused on one task at a time.

Before implementation, document a plan with scope, steps, and unresolved questions. Execute against that plan. Verify requirements and behavior from repository sources; do not invent missing facts. Stop to ask for clarification when an unresolved material decision cannot be established from available context. Record work in `work-log/`, using one Markdown file per task (`YYYY-MM-DD-short-task-name.md`). In that task file, document the plan and every meaningful action, including inspections, edits, commands or checks and their outcomes, and unresolved items. Update the record as work proceeds.

## Project Structure & Module Organization

This checkout currently contains BrowserFleet planning documents, not application code. `BROWSERFLEET_ARCHITECTURE.md` describes intended system boundaries and technology choices; `BROWSERFLEET_PHASES.md` tracks delivery phases and acceptance criteria. `work-log/` contains task-by-task action records. No source, tests, or assets directories are present yet. As implementation begins, keep API/control-plane, worker runtime, dashboard, SDK, deployment, and tests in clearly named top-level areas, following the architecture document's ownership boundaries.

## Build, Test, and Development Commands

There are no project manifests or runnable build/test commands in this checkout. Do not assume a command is available until its `pyproject.toml`, `package.json`, `Makefile`, or other defining file is added. When code lands, document reproducible setup, local service startup, and test commands in the relevant README and keep them aligned with CI.

## Coding Style & Naming Conventions

Follow the planned stack: Python 3.12+, FastAPI, SQLAlchemy 2.x, Pydantic v2, Playwright, and a Next.js/TypeScript dashboard. Keep platform-specific worker behavior behind environment adapters. Use descriptive module names that match domain responsibilities (for example, `scheduler` or `artifact_service`). Add formatter and linter configuration with the first implementation rather than relying on undocumented personal settings.

## Testing Guidelines

No test framework or tests are present yet. The architecture calls for pytest, Playwright, and Locust for unit, browser/integration, and load testing. Add tests alongside each implementation phase, name Python tests `test_*.py`, and cover acceptance criteria and failure paths. Do not claim coverage thresholds until the repository defines and enforces them.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout, so no established commit convention can be verified. Use short imperative commit subjects (for example, `Add worker heartbeat handling`). Pull requests should explain the change, link the relevant phase or issue and work-log record, list validation commands and results, and include screenshots for dashboard changes. Call out configuration or migration impacts.

## Security & Configuration

Treat workflows, credentials, browser profiles, and artifacts as sensitive. Never commit secrets or execute arbitrary job-supplied Python on shared workers. Use synthetic data in examples and demos, and document required environment variables without including live values.
