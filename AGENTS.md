# Repository Guidelines

## Agent Workflow & Work Records

Use the matching specialist from `C:\Users\soham\.codex\agents\` for each task. Available roles cover architecture, backend, database, frontend, worker runtime, DevOps, documentation, QA, security, bug fixing, and code review. Work on one focused task at a time. Before implementation, record its plan, scope, steps, and unresolved questions in one Markdown file under `work-log/`; update it with meaningful actions, files changed, checks and results, and unresolved items. Verify facts in the repository and ask when a material requirement cannot be established.

## Root-Cause-First Troubleshooting

Every specialist must establish the root cause before changing code or configuration. Reproduce the reported failure when possible, then trace it through the relevant source of truth: runtime/interpreter selection, workspace membership and commands, dependency declarations and lockfiles, import paths, configuration, or the failing application path. Separate observed symptoms from hypotheses and record the evidence that confirms the cause.

Do not apply speculative fixes, suppress the symptom, or stack workarounds on top of earlier patches. Fix the responsible source or configuration at its root. Before editing, check related workflows and consumers that could be affected; add or update a regression test/check where appropriate. After editing, rerun the original failing command and relevant neighboring checks. A fix is complete only when the cause is addressed and these checks pass, or when a remaining external blocker is clearly documented.

If evidence does not confirm a root cause, stop before making a fix. Report what was reproduced, what was inspected, the leading hypotheses, and what evidence or access is still needed. Do not label a hypothesis as the cause or present a workaround as a root-level repair.

Recent example: `backend-engineer` was invoked for the API dependency issue and again for the API test import issue; `devops-engineer` was also invoked for workspace dependency diagnosis. This repeated troubleshooting did not identify all causes on the first pass. All agents must follow the root-cause and regression-check requirements above, regardless of specialty.

## Project Structure

- `apps/api/` contains the FastAPI application and pytest smoke test.
- `apps/dashboard/` contains the Next.js/TypeScript skeleton and Node route smoke test.
- `workers/agent/` is the worker package boundary; Playwright is pinned there, but worker behavior is not implemented.
- `docs/architecture/` contains the architecture and phase plans; `work-log/` contains task records.

Markdown documentation, `docs/`, and `work-log/` are intentionally Git-ignored at the owner's request and remain local to this checkout. The application and package manifests are at the repository root and under `apps/` and `workers/`.

## Development Commands

Use Python 3.12 with uv and Node 22 with pnpm 10.29.3. Run `uv sync` and `pnpm install` from the root to install dependencies. Start the API with `uv run --project apps/api uvicorn browserfleet_api.main:app --app-dir apps/api --reload --host 127.0.0.1 --port 8000`; start the dashboard with `pnpm --filter @browserfleet/dashboard dev`.

Run `pnpm run lint`, `pnpm run typecheck`, `pnpm run test`, and `pnpm run build` from the root. `pnpm run browsers:install` installs the worker's pinned Chromium browser. PostgreSQL, RabbitMQ, Redis, MinIO, and related local services belong to the later infrastructure phase and are not required by the current skeleton.

## Coding Style & Testing

Python targets 3.12, uses Ruff with import sorting, and is checked by strict Pyright. Python tests live under each package's `tests/` directory and use `test_*.py`; the API health check uses pytest. The dashboard uses TypeScript, ESLint, and Node's built-in test runner. Add tests for new behavior and failure paths; no coverage threshold is configured.

## Commits, Pull Requests & Security

The available Git history currently contains only `test-commit`, which does not establish a commit convention. The repository owner manages commits, pushes, and pull requests. Pull requests should explain the change, identify its phase or task, report checks, and call out configuration impacts. Never commit credentials; `.env` and local build/cache artifacts are ignored. Keep `.env.example` free of secrets. Treat workflows, browser profiles, and artifacts as sensitive, and never execute arbitrary job-supplied Python on shared workers.
