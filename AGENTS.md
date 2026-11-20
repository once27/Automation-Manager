# Repository Guidelines

## Agent Workflow & Work Records

Use the matching specialist from `C:\Users\soham\.codex\agents\` for each task. Available roles cover architecture, backend, database, frontend, worker runtime, DevOps, documentation, QA, security, bug fixing, and code review. Work on one focused task at a time. Before implementation, record its plan, scope, steps, and unresolved questions in one Markdown file under `work-log/`; update it with meaningful actions, files changed, checks and results, and unresolved items. Verify facts in the repository and ask when a material requirement cannot be established.

When completing a phase or a documented feature, update this file in the same change with its completion state, the relevant verification results, and any follow-up work. Keep the state precise: planned, implemented, tested, and released are different milestones. Because `work-log/` and other Markdown documentation are intentionally ignored, treat work-log notes as local records; put any status or handoff needed by other contributors in this tracked file (or explicitly force-add the relevant document when the owner intends to version it).

Whenever a change adds or changes anything needed to install, configure, migrate, run, test, or operate BrowserFleet locally, including dependencies, environment variables, infrastructure, CLI commands, service startup, API authentication, or developer workflow, update LOCAL_DEVELOPMENT.md in the same change. Keep it as the owner copy-and-run guide: document exact commands in order, required terminal and environment context, expected endpoints or results, and cleanup or data-preservation notes where relevant. Verify each command against the current manifests and implementation; mark commands that could not be run as unverified. If the change does not affect local setup or workflow, no update is needed. LOCAL_DEVELOPMENT.md, docs, and work-log are Git-ignored by owner preference, so keep the local run guide current in this checkout and do not force-add it unless the owner explicitly asks to version it. Keep tracked instruction files such as AGENTS.md updated normally.

## Root-Cause-First Troubleshooting

Every specialist must establish the root cause before changing code or configuration. Reproduce the reported failure when possible, then trace it through the relevant source of truth: runtime/interpreter selection, workspace membership and commands, dependency declarations and lockfiles, import paths, configuration, or the failing application path. Separate observed symptoms from hypotheses and record the evidence that confirms the cause.

Do not apply speculative fixes, suppress the symptom, or stack workarounds on top of earlier patches. Fix the responsible source or configuration at its root. Before editing, check related workflows and consumers that could be affected; add or update a regression test/check where appropriate. After editing, rerun the original failing command and relevant neighboring checks. A fix is complete only when the cause is addressed and these checks pass, or when a remaining external blocker is clearly documented.

If evidence does not confirm a root cause, stop before making a fix. Report what was reproduced, what was inspected, the leading hypotheses, and what evidence or access is still needed. Do not label a hypothesis as the cause or present a workaround as a root-level repair.

Recent example: `backend-engineer` was invoked for the API dependency issue and again for the API test import issue; `devops-engineer` was also invoked for workspace dependency diagnosis. This repeated troubleshooting did not identify all causes on the first pass. All agents must follow the root-cause and regression-check requirements above, regardless of specialty.

## Project Structure

- `apps/api/` contains the FastAPI application and pytest unit/integration tests.
- `apps/dashboard/` contains the Next.js/TypeScript skeleton and Node route smoke test.
- `workers/agent/` is the worker package boundary; Playwright is pinned there, but worker behavior is not implemented.
- `docs/architecture/` contains the architecture and phase plans; `work-log/` contains task records.

Markdown documentation, `docs/`, and `work-log/` are intentionally Git-ignored at the owner's request and remain local to this checkout. The application and package manifests are at the repository root and under `apps/` and `workers/`.

## Development Commands

Use Python 3.12 with uv and Node 22 with pnpm 10.29.3. Follow `LOCAL_DEVELOPMENT.md` for the full Windows setup, PostgreSQL migration, tenant/API-key bootstrap, test database configuration, and API/dashboard run flow. Start the API with `uv run --project apps/api uvicorn browserfleet_api.main:app --app-dir apps/api --reload --host 127.0.0.1 --port 8000`; start the dashboard with `pnpm --filter @browserfleet/dashboard dev`.

Run `pnpm run lint`, `pnpm run typecheck`, `pnpm run test`, and `pnpm run build` from the root. `pnpm run browsers:install` installs the worker's pinned Chromium browser. PostgreSQL is required for authenticated workflow and job routes. RabbitMQ, Redis, and MinIO are available in local Compose, but Phase 2 does not publish jobs to a queue or use those services for job execution.

## Coding Style & Testing

Python targets 3.12, uses Ruff with import sorting, and is checked by strict Pyright. Python tests live under each package's `tests/` directory and use `test_*.py`; the API health check uses pytest. The dashboard uses TypeScript, ESLint, and Node's built-in test runner. Add tests for new behavior and failure paths; no coverage threshold is configured.

## Commits, Pull Requests & Security

The available Git history currently contains only `test-commit`, which does not establish a commit convention. The repository owner manages commits, pushes, and pull requests. Pull requests should explain the change, identify its phase or task, report checks, and call out configuration impacts. Never commit credentials; `.env` and local build/cache artifacts are ignored. Keep `.env.example` free of secrets. Treat workflows, browser profiles, and artifacts as sensitive, and never execute arbitrary job-supplied Python on shared workers.

### Completing and synchronizing a change

For each completed phase or documented feature, update the status and verification notes in this file as part of the same change. Before syncing, inspect `git status --short --branch` and `git diff`; stage only the intended files. Ignored docs and work-log files are not included by ordinary `git add`; use `git add -f <path>` only when that document is intended to be versioned, and review the staged diff so local records are not accidentally committed.

Use this sequence from the repository root, replacing the example message and paths with the change being delivered:

```powershell
git status --short --branch
git add <intended-paths>
git diff --cached
git commit -m "Describe the completed phase or feature"
git fetch origin
git rebase origin/main
git status --short --branch
git log --oneline --graph --decorate -10
git push origin main
```

Fetch before rebasing so the rebase uses the current remote tip. If the rebase reports conflicts, resolve each reported file, inspect it, run the relevant checks, then use `git add <resolved-path>` and `git rebase --continue`; use `git rebase --abort` to return to the pre-rebase branch. Do not push until the rebase completes and the branch/status and recent log have been reviewed. Keep unrelated local changes out of the commit; if they must be stashed, inspect `git stash list` first and use a descriptive message so the correct stash can be identified and restored later.
