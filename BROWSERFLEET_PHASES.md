# BrowserFleet V1 --- Phased Implementation Plan

**Purpose:** An execution checklist with milestones, deliverables,
acceptance criteria, and exit gates.\
**Companion:** `BROWSERFLEET_ARCHITECTURE.md`\
**Method:** Vertical slices; prove end-to-end behavior before adding
complexity.

## How to use this plan

-   Complete phases in order unless a dependency is explicitly
    independent.
-   Do not advance just because code compiles; satisfy the acceptance
    criteria.
-   Every phase includes tests, documentation, and a demonstrable
    outcome.
-   Record architecture deviations as ADRs.
-   Use synthetic demo workflows/data. Do not reuse confidential client
    code, credentials, portals, or proprietary workflows.
-   Keep `CHANGELOG.md` and README current.
-   Never invent benchmark numbers; document hardware, workload, and
    methodology.

## Phase overview

  ------------------------------------------------------------------------------------
  Phase            Name                         Main outcome          Estimated effort
  ---------------- ---------------------------- ---------------- ---------------------
  0                Product/repository           Scope, repo,                 1--2 days
                   foundation                   coding           
                                                standards, local 
                                                dev              

  1                Infrastructure/persistence   Local services,              2--3 days
                                                migrations, core 
                                                schema           

  2                Job domain/API               Workflow                     3--4 days
                                                registry, job    
                                                API, state       
                                                machine          

  3                Worker identity/protocol     Registration,                3--4 days
                                                WebSocket,       
                                                heartbeat        

  4                First execution slice        Linux Docker                 3--4 days
                                                worker runs      
                                                headless         
                                                Playwright       

  5                Durable dispatch/scheduler   RabbitMQ,                    4--5 days
                                                outbox, worker   
                                                selection,       
                                                leases           

  6                Multi-worker reliability     Concurrency,                 3--5 days
                                                cancellation,    
                                                recovery         

  7                Linux headed execution       Xvfb/noVNC                   2--4 days
                                                option and       
                                                remote           
                                                inspection       

  8                Windows native execution     Windows agent                3--5 days
                                                and desktop      
                                                capability       
                                                detection        

  9                Artifacts/observability      MinIO/S3, logs,              3--4 days
                                                metrics, traces  

  10               Persistent sessions          Encrypted                    4--5 days
                                                snapshots and    
                                                exclusive leases 

  11               Security/hardening           Tenant                       3--5 days
                                                isolation,       
                                                secrets, quotas, 
                                                threat controls  

  12               Dashboard/SDK                Operational UI               4--6 days
                                                and developer    
                                                experience       

  13               Release/benchmark            Deployment,                  3--5 days
                                                failure tests,   
                                                load tests, demo 
  ------------------------------------------------------------------------------------

Estimates are planning aids for a focused solo build, not delivery
guarantees. Windows desktop testing, persistent sessions, and security
hardening may take longer.

------------------------------------------------------------------------

# Phase 0 --- Product and repository foundation

## Objective

Create a clean development foundation and lock down V1 boundaries.

## Tasks

-   [ ] Create the `browserfleet` Git repository.
-   [ ] Create the monorepo structure from the architecture document.
-   [ ] Add Python project/dependency management.
-   [ ] Add Ruff formatting/linting and mypy or Pyright.
-   [ ] Add pytest conventions.
-   [ ] Add `.env.example`; do not commit secrets.
-   [ ] Add README with product scope and local setup.
-   [ ] Add architecture and phase documents under `docs/`.
-   [ ] Create ADR directory and record initial decisions.
-   [ ] Define ID, timestamp, enum, and API-error conventions.
-   [ ] Pin Python, Playwright, and browser versions.

## Deliverables

-   Repository skeleton.
-   Development standards.
-   Initial README.
-   Architecture and phase documentation.
-   CI that runs lint and unit tests.

## Acceptance criteria

-   [ ] Fresh clone can install dependencies.
-   [ ] Lint and test commands run locally.
-   [ ] CI runs on pull requests.
-   [ ] No credentials are committed.
-   [ ] Repository boundaries match the architecture.

**Exit gate:** A clean environment can clone the repo and run documented
checks.

------------------------------------------------------------------------

# Phase 1 --- Local infrastructure and persistence

## Objective

Bring up shared infrastructure and establish PostgreSQL as source of
truth.

## Tasks

-   [ ] Add Compose services: PostgreSQL, RabbitMQ, Redis, MinIO.
-   [ ] Add health checks and persistent volumes.
-   [ ] Configure SQLAlchemy 2.x.
-   [ ] Configure Alembic.
-   [ ] Implement tenants, users, API keys.
-   [ ] Implement worker pools and workers.
-   [ ] Implement workflows.
-   [ ] Implement jobs, attempts, events, artifacts, sessions, leases,
    audit logs, outbox.
-   [ ] Add repository/data-access layer.
-   [ ] Add indexes and constraints.
-   [ ] Add database integration tests.
-   [ ] Document backup and local reset commands.

## Deliverables

-   Local infrastructure.
-   Initial migration set.
-   Database model/relationship diagram.
-   Repository layer and tests.

## Acceptance criteria

-   [ ] `docker compose up` starts required services.
-   [ ] Migrations run from an empty database.
-   [ ] Tenant-owned records carry tenant identity.
-   [ ] Idempotency and active-session-lease constraints are tested.
-   [ ] Large artifacts are not stored in PostgreSQL.

**Exit gate:** A clean database can be built entirely through migrations
and integration tests pass.

------------------------------------------------------------------------

# Phase 2 --- Job domain, workflow registry, and REST API

## Objective

Implement core job behavior without remote execution yet.

## Tasks

-   [ ] Define Pydantic request/response contracts.
-   [ ] Implement API-key authentication.
-   [ ] Implement tenant context and authorization.
-   [ ] Implement workflow registration and immutable versions.
-   [ ] Implement `POST /api/v1/jobs`.
-   [ ] Implement job list/filter and detail endpoints.
-   [ ] Implement attempts and event timeline endpoints.
-   [ ] Implement idempotency-key handling.
-   [ ] Implement state machine independently of FastAPI.
-   [ ] Implement transition validation and optimistic versioning.
-   [ ] Implement cancellation and manual retry behavior.
-   [ ] Implement consistent error responses.
-   [ ] Add OpenAPI examples and API tests.

## Deliverables

Job submission API, workflow registry, state machine, API docs, tests.

## Acceptance criteria

-   [ ] Valid job is created as `QUEUED`.
-   [ ] Duplicate tenant/idempotency key returns existing job.
-   [ ] Invalid workflow versions are rejected.
-   [ ] Invalid transitions are rejected.
-   [ ] Tenant A cannot read Tenant B jobs.
-   [ ] Errors do not reveal stack traces or secrets.
-   [ ] Every state transition creates an event.

**Exit gate:** A client can submit and inspect jobs; domain transitions
are enforced and tested.

------------------------------------------------------------------------

# Phase 3 --- Worker identity, registration, and protocol

## Objective

Allow a worker on another machine to securely connect and report health.

## Tasks

-   [ ] Define versioned message envelopes.
-   [ ] Define Pydantic schemas for registration, heartbeat, events,
    commands.
-   [ ] Implement worker provisioning/credential creation.
-   [ ] Implement worker authentication.
-   [ ] Implement `wss://.../api/v1/workers/connect`.
-   [ ] Implement registration and capability reporting.
-   [ ] Implement heartbeat handling.
-   [ ] Implement server-side worker lease expiry.
-   [ ] Implement enable, disable, and drain.
-   [ ] Implement reconnect with exponential backoff and jitter.
-   [ ] Implement protocol version validation.
-   [ ] Add connection/protocol tests.

## Deliverables

Worker agent skeleton, WebSocket endpoint, registry, heartbeat/lease
handling, protocol docs.

## Acceptance criteria

-   [ ] Remote worker registers through outbound connection.
-   [ ] Worker credentials are separate from user API keys.
-   [ ] Revoked credentials are rejected.
-   [ ] Heartbeats update worker health.
-   [ ] Missed heartbeats expire worker lease.
-   [ ] Worker reconnects and reports current state.
-   [ ] Draining worker receives no new jobs.
-   [ ] Worker cannot subscribe to another tenant's work.

**Exit gate:** A remote worker connects, registers, heartbeats,
disconnects, and reconnects without database access.

------------------------------------------------------------------------

# Phase 4 --- First end-to-end execution slice

## Objective

Prove the complete job lifecycle with one Linux Docker worker and
headless Chromium.

## Tasks

-   [ ] Implement common `BrowserEnvironment` interface.
-   [ ] Implement Linux Docker adapter.
-   [ ] Implement browser launch and context creation.
-   [ ] Implement approved workflow registry/loader.
-   [ ] Create synthetic demo website/test fixture.
-   [ ] Create demo workflow that visits fixture and extracts test data.
-   [ ] Implement execution manager.
-   [ ] Implement acceptance, start, progress, completion, failure
    events.
-   [ ] Implement timeout handling.
-   [ ] Capture screenshot on failure.
-   [ ] Return structured result.
-   [ ] Add Playwright smoke tests.

## Deliverables

One Linux Docker worker, one demo workflow, headless execution,
API-visible result, basic artifact handling.

## Acceptance criteria

-   [ ] Submit job through API.
-   [ ] Worker receives and validates it.
-   [ ] Worker runs approved workflow.
-   [ ] Ephemeral context closes after execution.
-   [ ] Worker reports success/failure.
-   [ ] Control plane updates job state.
-   [ ] Failed workflow produces useful error.
-   [ ] Job payload cannot contain arbitrary Python source.

**Exit gate:** API-to-remote-worker-to-durable-result vertical slice
works. Record a short demo.

------------------------------------------------------------------------

# Phase 5 --- RabbitMQ, transactional outbox, and scheduler

## Objective

Make dispatch durable and support capability-aware worker selection.

## Tasks

-   [ ] Define RabbitMQ exchanges, queues, routing keys, dead-letter
    strategy.
-   [ ] Implement transactional outbox writes.
-   [ ] Implement outbox publisher.
-   [ ] Implement idempotent consumers.
-   [ ] Implement scheduler process.
-   [ ] Claim queued jobs with database locking.
-   [ ] Implement worker eligibility filtering.
-   [ ] Implement scheduling score.
-   [ ] Implement capacity reservations.
-   [ ] Implement dispatch IDs and execution leases.
-   [ ] Implement acceptance/rejection.
-   [ ] Implement lease renewal.
-   [ ] Implement expired-dispatch reconciliation.
-   [ ] Add broker outage and duplicate delivery tests.

## Deliverables

RabbitMQ dispatch pipeline, scheduler, worker selection, durable
dispatch/leases.

## Acceptance criteria

-   [ ] Two schedulers cannot claim the same job.
-   [ ] Job dispatches only to compatible worker.
-   [ ] Worker capacity is reserved atomically.
-   [ ] Stale dispatch cannot start execution.
-   [ ] Duplicate dispatch does not create duplicate execution.
-   [ ] Broker outage does not lose committed job state.
-   [ ] Outbox publishes after broker recovery.

**Exit gate:** Dispatch is durable and idempotent under multiple
schedulers and workers.

------------------------------------------------------------------------

# Phase 6 --- Multi-worker execution and reliability

## Objective

Scale to a fleet and handle common failures.

## Tasks

-   [ ] Run multiple Linux Docker workers.
-   [ ] Add worker pools and labels.
-   [ ] Add capacity/resource reporting.
-   [ ] Implement concurrency limits.
-   [ ] Implement priority-aware scheduling.
-   [ ] Add per-tenant concurrency caps.
-   [ ] Implement cancellation commands.
-   [ ] Implement retry policy and backoff.
-   [ ] Implement worker-loss reconciliation.
-   [ ] Implement attempt status/error categories.
-   [ ] Implement uncertain-outcome metadata.
-   [ ] Add deadline handling.
-   [ ] Add fairness and load tests.

## Deliverables

Multi-worker fleet, retry/cancellation, worker-loss recovery, scheduling
metrics.

## Acceptance criteria

-   [ ] Jobs distribute across eligible workers.
-   [ ] Worker concurrency is not exceeded.
-   [ ] Worker loss is detected within lease window.
-   [ ] Safe jobs can retry.
-   [ ] Uncertain side effects are not blindly replayed.
-   [ ] Cancellation reaches running worker.
-   [ ] Queue/worker metrics are available.
-   [ ] Tenant limits are enforced.

**Exit gate:** Demonstrate worker crash and explain which jobs can
retry, which are uncertain, and why.

------------------------------------------------------------------------

# Phase 7 --- Linux headed execution

## Objective

Support visible browser execution on Linux without changing the worker
protocol.

## Tasks

-   [ ] Implement `LinuxHeadedEnvironment`.
-   [ ] Configure Xvfb or supported desktop display.
-   [ ] Add headed browser health checks.
-   [ ] Add optional noVNC for authorized debugging.
-   [ ] Report headed capability.
-   [ ] Detect display availability and clean up processes.
-   [ ] Add resource limits.
-   [ ] Add headed smoke tests.
-   [ ] Document secure remote viewing/network restrictions.

## Deliverables

Linux headed worker, visible browser session, optional protected viewer,
capability detection.

## Acceptance criteria

-   [ ] Scheduler routes headed jobs only to capable workers.
-   [ ] Browser renders in configured display.
-   [ ] Screenshots/traces work.
-   [ ] Browser/display processes clean up.
-   [ ] Viewer is not publicly exposed without authentication.
-   [ ] Headless behavior remains unchanged.

**Exit gate:** Record headed job running on Linux separate from control
plane.

------------------------------------------------------------------------

# Phase 8 --- Windows native worker

## Objective

Support Windows as a first-class worker, including interactive desktop
requirements.

## Tasks

-   [ ] Implement Windows environment adapter.
-   [ ] Package worker with pinned Python/Playwright dependencies.
-   [ ] Implement Windows configuration.
-   [ ] Implement process/service installation.
-   [ ] Implement resource monitoring.
-   [ ] Detect interactive desktop availability.
-   [ ] Detect locked/disconnected/logged-out conditions.
-   [ ] Report headed capability accurately.
-   [ ] Implement drain and graceful shutdown.
-   [ ] Add Windows headless smoke tests.
-   [ ] Add headed smoke tests on real interactive desktop.
-   [ ] Document RDP/session limitations.

## Deliverables

Windows installer, registration/heartbeat, headless execution, headed
execution when desktop is available.

## Acceptance criteria

-   [ ] Windows worker reports correct platform.
-   [ ] Headless job executes.
-   [ ] Headed job is rejected or unscheduled without healthy desktop.
-   [ ] No silent headed-to-headless fallback.
-   [ ] Worker drains and shuts down cleanly.
-   [ ] Worker reconnects after network interruption.

**Exit gate:** Demonstrate Windows worker from separate machine and
document tested RDP/session conditions.

------------------------------------------------------------------------

# Phase 9 --- Artifact storage and observability

## Objective

Make every execution inspectable and diagnosable.

## Tasks

-   [ ] Add MinIO/S3 storage adapter.
-   [ ] Implement signed upload/download URLs.
-   [ ] Add artifact metadata and checksums.
-   [ ] Upload screenshots, traces, logs, optional videos.
-   [ ] Add retention policies.
-   [ ] Add structured JSON logging.
-   [ ] Add OpenTelemetry traces.
-   [ ] Add Prometheus metrics.
-   [ ] Add Grafana dashboards.
-   [ ] Correlate request, job, attempt, worker, trace IDs.
-   [ ] Redact secrets.

## Deliverables

Central artifact storage, job timeline, metrics/dashboards, trace
correlation.

## Acceptance criteria

-   [ ] Artifacts stored outside PostgreSQL.
-   [ ] Only authorized tenant users can download.
-   [ ] Signed URLs expire.
-   [ ] Checksums verified.
-   [ ] Logs contain correlation identifiers.
-   [ ] Metrics avoid unbounded labels.
-   [ ] Retention policy deletes expired artifacts.

**Exit gate:** An operator can inspect timeline, worker, attempt, logs,
trace, and screenshot.

------------------------------------------------------------------------

# Phase 10 --- Persistent browser sessions

## Objective

Reuse authenticated browser state safely.

## Tasks

-   [ ] Implement session creation and metadata APIs.
-   [ ] Implement session snapshot abstraction.
-   [ ] Encrypt snapshots before storage.
-   [ ] Implement exclusive session leases.
-   [ ] Implement session-to-pool affinity.
-   [ ] Restore state on compatible worker.
-   [ ] Upload new state after successful execution.
-   [ ] Implement expiry and revocation.
-   [ ] Implement reset.
-   [ ] Implement session history and audit.
-   [ ] Add concurrent-use tests.
-   [ ] Add cleanup/retention jobs.

## Deliverables

Persistent session lifecycle, encrypted storage, exclusive leases,
history and revocation.

## Acceptance criteria

-   [ ] Later job can reuse persistent session.
-   [ ] Two jobs cannot hold active write leases for one session.
-   [ ] Raw cookies/storage state never returned by ordinary API.
-   [ ] Revoked/expired session cannot be used.
-   [ ] Snapshots encrypted at rest.
-   [ ] Session usage audited.
-   [ ] Worker failure does not silently corrupt last known good
    snapshot.

**Exit gate:** Demonstrate sequential reuse and safe handling of
concurrent use.

------------------------------------------------------------------------

# Phase 11 --- Security and production hardening

## Objective

Reduce risk from browser execution, credentials, tenant boundaries, and
operational failures.

## Tasks

-   [ ] Threat-model control plane, worker, browser, artifact, and
    session boundaries.
-   [ ] Add RBAC.
-   [ ] Add API key scopes and rotation.
-   [ ] Add worker credential rotation/revocation.
-   [ ] Add tenant isolation tests for every resource.
-   [ ] Add secret-reference integration.
-   [ ] Configure encryption key management.
-   [ ] Add browser network egress restrictions/documented policy.
-   [ ] Block cloud metadata/internal control-plane access from
    untrusted contexts where feasible.
-   [ ] Apply CPU/RAM/PID/disk/time limits.
-   [ ] Run browser processes as non-root where possible.
-   [ ] Restrict workflow sources and verify checksums.
-   [ ] Add audit logs.
-   [ ] Add retention/deletion.
-   [ ] Add dependency/container scanning.
-   [ ] Add backup/restore documentation and test recovery.

## Deliverables

Threat model, hardened worker images, RBAC/audit coverage,
backup/recovery guide.

## Acceptance criteria

-   [ ] Cross-tenant tests pass.
-   [ ] Revoked credentials stop working.
-   [ ] Secrets absent from logs and dispatch messages.
-   [ ] Persistent session data encrypted.
-   [ ] Worker network/resource restrictions documented.
-   [ ] Artifact access authorized and audited.
-   [ ] Backup restore tested.

**Exit gate:** Publish security and limitations docs. Do not claim
protection against arbitrary hostile code unless designed and tested.

------------------------------------------------------------------------

# Phase 12 --- Dashboard, SDK, and developer experience

## Objective

Make BrowserFleet usable without manual API calls or infrastructure log
inspection.

## Dashboard pages

-   [ ] Overview: fleet health, queue depth, throughput.
-   [ ] Jobs: list, filters, state, attempts, duration.
-   [ ] Job detail: timeline, events, result, artifacts.
-   [ ] Workers: status, platform, environment, capacity, heartbeat.
-   [ ] Worker detail: capabilities, active jobs, resource history.
-   [ ] Worker actions: drain, enable, disable.
-   [ ] Sessions: metadata, expiry, status, history, revoke/reset.
-   [ ] Workflows: versions and enabled state.
-   [ ] Settings: API keys, pools, quotas, retention.

## Python SDK

-   [ ] Create SDK package.
-   [ ] Implement typed API client/models.
-   [ ] Implement job submission and polling.
-   [ ] Implement wait helper.
-   [ ] Implement artifact listing/download.
-   [ ] Add safe request retries.
-   [ ] Document idempotency usage.
-   [ ] Add example client.

Target usage:

``` python
from browserfleet import BrowserFleet

client = BrowserFleet(api_key="...")

job = client.jobs.create(
    workflow="invoice_extraction",
    execution={
        "browser": "chromium",
        "mode": "headless",
        "session": {"mode": "ephemeral"}
    },
    input={"record_id": "demo-001"}
)

result = client.jobs.wait(job.id, timeout=600)
print(result.status)
```

## Acceptance criteria

-   [ ] Dashboard communicates fleet health.
-   [ ] Job detail shows meaningful execution history.
-   [ ] SDK submits and waits for jobs.
-   [ ] Examples match actual API contracts.
-   [ ] Dashboard respects tenant roles.

**Exit gate:** An unfamiliar developer can follow quickstart and run a
demo workflow.

------------------------------------------------------------------------

# Phase 13 --- Release, benchmark, and portfolio demo

## Objective

Make the implementation reproducible and credible as an engineering
product.

## Tasks

-   [ ] Complete installation guide.
-   [ ] Write Linux Docker worker guide.
-   [ ] Write Linux headed worker guide.
-   [ ] Write Windows worker guide.
-   [ ] Write workflow authoring guide.
-   [ ] Write API/SDK reference.
-   [ ] Write security/limitations guide.
-   [ ] Write operations/troubleshooting guide.
-   [ ] Add release tags and versioning.
-   [ ] Add CI build/test pipeline.
-   [ ] Build versioned worker images.
-   [ ] Run failure-injection tests.
-   [ ] Run documented load tests.
-   [ ] Measure queue wait, dispatch latency, throughput, recovery.
-   [ ] Create architecture diagram.
-   [ ] Record 2--3 minute product demo.
-   [ ] Write technical case study with trade-offs and measured results.

## Demo script

1.  Start self-hosted control plane.
2.  Show Linux Docker, Linux headed, and Windows workers registered.
3.  Submit batch of demo jobs.
4.  Show capability-aware distribution.
5.  Open headed Linux session through authorized viewer.
6.  Inspect timeline, logs, screenshot, and trace.
7.  Stop a test worker during a controlled workflow.
8.  Show lease expiry and reconciliation.
9.  Demonstrate safe retry and explain uncertain side effects.
10. Run sequential jobs against persistent demo session.
11. Show fleet metrics and measured throughput.

## Acceptance criteria

-   [ ] Fresh installation works from docs.
-   [ ] All worker environments have smoke tests.
-   [ ] Failure scenarios documented.
-   [ ] Benchmarks include hardware/workload/methodology.
-   [ ] Demo uses synthetic data.
-   [ ] No confidential client assets or credentials.
-   [ ] Release has version and changelog.

**Exit gate:** V1 can be installed, demonstrated, tested, and explained
by someone other than its author.

------------------------------------------------------------------------

## Suggested weekly cadence

**Day 1 --- Design:** Read architecture section, define
interfaces/acceptance criteria, record open decisions.\
**Days 2--3 --- Implementation:** Build smallest vertical slice, keep
changes focused, add unit tests.\
**Day 4 --- Integration:** Test real dependencies and failure paths;
inspect logs/database.\
**Day 5 --- Documentation/demo:** Update docs, record limitations,
capture demo, update checklist/changelog.

Adjust cadence to actual complexity. Do not force a phase to fit one
week if its acceptance criteria are unmet.

## Risk register

  -----------------------------------------------------------------------
  Risk                    Why it matters          Mitigation
  ----------------------- ----------------------- -----------------------
  Windows headed session  Services and RDP        Interactive-session
  behavior                sessions have different health checks and
                          desktop access          real-machine tests

  Duplicate dispatch      Broker redelivery and   Dispatch IDs, leases,
                          reconnects happen       idempotent consumers

  Worker crash after side External result may be  Workflow reconciliation
  effect                  unknown                 and conservative retry

  Persistent profile      Concurrent writes       Exclusive lease and
  corruption              conflict                versioned snapshots

  Browser resource        Browsers consume        Concurrency and
  exhaustion              substantial CPU/RAM     resource budgets

  Untrusted workflow      Browser has             Registered workflows,
  execution               network/filesystem      isolation, egress
                          access                  controls

  Artifact storage growth Traces/videos are large Retention, quotas,
                                                  lifecycle cleanup

  Tenant data leakage     Shared control plane    Tenant-scoped queries,
                          stores multiple tenants RBAC, isolation tests

  WebSocket disconnect    Worker loses command    Reconnect and lease
                          channel                 reconciliation

  Early overengineering   Delays first usable     Modular monolith and
                          product                 staged rollout
  -----------------------------------------------------------------------

## Definition of V1

V1 is defined by a reliable end-to-end system:

-   Developer submits an approved workflow.
-   Control plane validates and queues it.
-   Scheduler selects a compatible worker.
-   Worker executes the browser job.
-   Result and artifacts are persisted.
-   User can inspect what happened.
-   Worker failure is detected and reconciled.
-   Tenant and session boundaries are enforced.

**Build this core loop first. Everything else should strengthen it.**
