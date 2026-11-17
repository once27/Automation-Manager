# BrowserFleet V1 --- Technical Architecture & Engineering Blueprint

**Status:** V1 design specification\
**Product direction:** Self-hosted first, SaaS-ready architecture\
**Purpose:** Long-lived reference for implementation and design
decisions.

## 1. Product vision

BrowserFleet is a self-hostable distributed browser automation platform.
Developers submit approved automation jobs through an API or SDK. The
control plane validates requirements, schedules work onto suitable
machines, executes Playwright workflows through workers, and collects
results and artifacts.

V1 supports: - Linux Docker workers. - Linux native/headed workers using
a virtual display. - Windows native workers using an interactive desktop
session. - Headless and headed execution. - Ephemeral and persistent
browser sessions. - Worker registration, health, scheduling, retries,
cancellation, and recovery. - Centralized logs, screenshots, videos,
Playwright traces, metrics, and job history. - Tenant-aware data
separation, quotas, and RBAC. - Self-hosted Docker Compose deployment,
with a path to managed SaaS.

### Engineering principles

1.  The control plane and execution plane are separate.
2.  Workers establish outbound authenticated connections; do not require
    public inbound access to every worker.
3.  PostgreSQL is the source of truth. RabbitMQ transports messages, not
    authoritative job state.
4.  Design for at-least-once delivery and idempotent processing, not
    exactly-once execution.
5.  Retries are not automatically safe: browser actions can have
    irreversible side effects.
6.  Platform-specific behavior lives behind environment adapters.
7.  Start with a modular monolith; scale API, scheduler, and workers
    independently.
8.  Treat workflows, web pages, credentials, browser profiles, and
    artifacts as untrusted or sensitive.

## 2. Scope and non-goals

### V1 scope

-   FastAPI control plane and REST API.
-   PostgreSQL, SQLAlchemy 2.x, Alembic.
-   RabbitMQ dispatch and internal event transport.
-   Redis for ephemeral coordination, rate limits, and presence.
-   Python worker agent and Playwright execution.
-   Linux Docker, Linux headed, and Windows native workers.
-   Ephemeral and persistent sessions.
-   Capability-aware scheduling, leases, retries, cancellation, and
    reconciliation.
-   MinIO/S3-compatible artifact storage.
-   Prometheus, Grafana, OpenTelemetry.
-   Next.js dashboard and Python SDK.
-   Docker Compose self-hosted deployment.

### Non-goals

-   Kubernetes requirement in V1.
-   Arbitrary user-supplied Python execution on shared workers.
-   Guaranteed exactly-once browser side effects.
-   Visual workflow builder.
-   CAPTCHA bypass, anti-bot evasion, or access-control circumvention.
-   Full SaaS billing/subscriptions.
-   Every browser automation framework.

**Execution boundary:** V1 runs administrator-installed or explicitly
approved workflow packages/identifiers. Do not accept arbitrary Python
source in a job payload and execute it on a shared worker. A later
isolated execution product can add signed packages and stronger
sandboxing.

## 3. System architecture

### 3.1 Components

  -----------------------------------------------------------------------
  Component                           Responsibility
  ----------------------------------- -----------------------------------
  API application                     Auth, validation, REST API,
                                      WebSocket endpoint

  Job service                         Job creation, state transitions,
                                      cancellation, retry policy

  Scheduler                           Select eligible workers and
                                      dispatch jobs

  Worker registry                     Registration, capabilities,
                                      heartbeat, health, draining

  Session service                     Session metadata, ownership,
                                      leases, persistence policy

  Worker agent                        Connect, receive jobs, execute,
                                      report events

  Browser runtime                     Launch browser, create
                                      context/page, run workflow

  Artifact service                    Signed upload/download URLs and
                                      metadata

  Event processor                     Validate worker events and apply
                                      transitions

  Observability                       Metrics, logs, traces, alerting

  Dashboard                           Operational UI and administration
  -----------------------------------------------------------------------

### 3.2 Control plane vs execution plane

**Control plane** - Accepts and validates jobs. - Authenticates users
and tenants. - Checks quotas and policies. - Selects workers and issues
leases. - Validates worker events. - Updates authoritative state. -
Exposes job, worker, session, and artifact information.

**Execution plane** - Runs on customer-controlled machines. - Registers
capabilities. - Maintains outbound secure connections. - Receives jobs
and launches the required browser environment. - Executes approved
workflows. - Uploads artifacts and reports results.

Workers must not directly update PostgreSQL. They report events to the
control plane, which validates state, tenant, dispatch, and lease
ownership.

### 3.3 Deployment topology

``` text
SDK / Dashboard / API Clients
            |
        HTTPS REST
            |
    +-------v--------+
    | FastAPI API    |
    | Auth / Jobs    |
    +-------+--------+
            |
    +-------v--------+
    | PostgreSQL     |  <- authoritative state
    +----------------+
            |
    +-------v--------+
    | Scheduler      |
    | Transactional  |
    | Outbox         |
    +-------+--------+
            |
         RabbitMQ
            |
    +-------v--------+
    | Dispatch and   |
    | Connection Mgr |
    +-------+--------+
            |
    Outbound authenticated WebSocket
       /          |            \
 Linux Docker  Linux GUI   Windows native
    worker       worker       worker
       \          |            /
        HTTPS signed artifact uploads
                    |
               MinIO / S3

Redis: ephemeral coordination and rate limits
Prometheus/Grafana: metrics and dashboards
OpenTelemetry: traces
```

### 3.4 Modular monolith

Keep domain modules in one control-plane codebase, but run separate
processes where useful: - `api`: REST and WebSocket endpoints. -
`scheduler`: claims queued jobs and assigns workers. - `event-consumer`:
consumes events and applies transitions. - `outbox-publisher`: publishes
committed outbox messages. - `worker`: installed independently on
execution nodes.

Do not create a network microservice for every domain module in V1.

## 4. Technology choices

  -----------------------------------------------------------------------
  Layer                   V1 technology           Reason
  ----------------------- ----------------------- -----------------------
  Language                Python 3.12+            Playwright ecosystem
                                                  and existing skills

  API                     FastAPI                 Async, typed
                                                  validation, OpenAPI

  ORM/migrations          SQLAlchemy 2.x /        Relational modeling and
                          Alembic                 versioned schema

  Contracts               Pydantic v2             API and message
                                                  validation

  Database                PostgreSQL              Transactional source of
                                                  truth

  Broker                  RabbitMQ                Durable queues and
                                                  routing

  Cache                   Redis                   Ephemeral coordination
                                                  and rate limiting

  Browser automation      Playwright              Contexts, tracing,
                                                  screenshots, video

  Artifacts               MinIO / S3              Large artifact storage

  Worker transport        Secure WebSocket        Outbound connectivity
                                                  through NAT/firewalls

  Metrics                 Prometheus              Time-series metrics

  Dashboards              Grafana                 Operations

  Tracing                 OpenTelemetry           Cross-component traces

  Frontend                Next.js + TypeScript    Dashboard

  Packaging               Docker Compose + native Self-hosted deployment
                          installers              

  Tests                   pytest, Playwright,     Unit, integration,
                          Locust                  browser, load
  -----------------------------------------------------------------------

Do not introduce Kubernetes initially. The first challenge is reliable
cross-machine orchestration, not container orchestration.

## 5. Worker architecture

### 5.1 Common worker agent

All environments run the same logical worker agent. Platform-specific
behavior is isolated behind adapters.

``` text
browserfleet_worker/
  agent/
    registration.py
    heartbeat.py
    connection.py
    job_consumer.py
    execution_manager.py
    lease_manager.py
    resource_monitor.py
    artifact_uploader.py
    event_reporter.py
  environments/
    base.py
    docker_linux.py
    linux_headed.py
    windows_native.py
  browsers/
    manager.py
    chromium.py
    firefox.py
    webkit.py
  sessions/
    ephemeral.py
    persistent.py
    profile_store.py
  workflows/
    registry.py
    loader.py
  config.py
  main.py
```

### 5.2 Environment adapter

``` python
from abc import ABC, abstractmethod

class BrowserEnvironment(ABC):
    @abstractmethod
    async def launch_browser(self, config):
        raise NotImplementedError

    @abstractmethod
    async def create_context(self, config):
        raise NotImplementedError

    @abstractmethod
    async def close_context(self):
        raise NotImplementedError

    @abstractmethod
    async def close_browser(self):
        raise NotImplementedError

    @abstractmethod
    async def collect_artifacts(self):
        raise NotImplementedError
```

Implement `DockerLinuxEnvironment`, `LinuxHeadedEnvironment`, and
`WindowsNativeEnvironment`. The execution manager depends on the
interface, not concrete OS logic.

### 5.3 Registration payload

``` json
{
  "worker_name": "linux-worker-01",
  "platform": "linux",
  "environment": "docker",
  "worker_version": "0.1.0",
  "protocol_version": "v1",
  "capabilities": {
    "browsers": ["chromium", "firefox"],
    "execution_modes": ["headless", "headed"],
    "session_modes": ["ephemeral", "persistent"],
    "max_concurrency": 4,
    "workflow_tags": ["invoice", "general"]
  },
  "resources": {
    "cpu_cores": 8,
    "memory_mb": 16384
  }
}
```

Server response includes `worker_id`, heartbeat interval, lease
duration, and protocol version.

### 5.4 Worker lifecycle

``` text
START -> LOAD_CONFIG -> CONNECT -> AUTHENTICATE -> REGISTER
  -> READY -> RECEIVE_JOB -> VALIDATE_JOB -> ACQUIRE_LEASE
  -> PREPARE_SESSION -> EXECUTE -> UPLOAD_ARTIFACTS
  -> REPORT_RESULT -> ACKNOWLEDGE -> READY
```

On shutdown: enter `DRAINING`, stop accepting jobs, finish work within
grace period, report remaining leases, close browsers, disconnect.

### 5.5 Worker statuses

  Status          Meaning
  --------------- ------------------------------------------
  `REGISTERING`   Registration in progress
  `ONLINE`        Connected and eligible
  `BUSY`          Has active work; may still have capacity
  `DRAINING`      Finish active jobs; no new assignments
  `OFFLINE`       Connection/heartbeat lease expired
  `DISABLED`      Administratively disabled
  `ERROR`         Fatal worker-level error

Do not equate `BUSY` with unavailable. Track active jobs and max
concurrency separately.

### 5.6 Heartbeats and leases

Initial configurable defaults: - Heartbeat every 15 seconds. - Worker
lease expires after 60 seconds without a valid heartbeat. - Job lease
renewed while execution is healthy. - Use server timestamps for expiry;
worker clocks can drift.

Heartbeat includes worker ID, connection ID, status, active job/lease
IDs, CPU/RAM, free concurrency, worker version, and health information.

### 5.7 Environment specifics

**Linux Docker** - Pinned Playwright image/version. - Headless
default. - Headed option via Xvfb and optional noVNC. - Dedicated job
workspace, bounded temporary storage, CPU/RAM/PID limits. - Non-root
user and read-only filesystem where practical. - Never mount Docker
socket into untrusted job containers.

**Linux headed** - Native agent or managed process. - Real desktop or
Xvfb virtual display. - Optional noVNC for authorized debugging. -
Dedicated OS user. - Keep remote desktop access separate from worker
command channel.

**Windows native** - Headless/background execution where supported. -
Headed execution requires a healthy interactive desktop session. - A
Windows service in Session 0 cannot be assumed to control an interactive
user desktop. - Detect locked, disconnected, logged-out, or unavailable
desktop sessions. - Do not silently downgrade headed jobs to headless.

## 6. Browser execution and sessions

### 6.1 Execution modes

  Mode         Description             Use
  ------------ ----------------------- --------------------------------
  `headless`   No visible browser UI   Background work and tests
  `headed`     Visible browser UI      Visual workflows and debugging

### 6.2 Session modes

  -----------------------------------------------------------------------
  Mode                    Lifecycle               Use
  ----------------------- ----------------------- -----------------------
  `ephemeral`             Created for one job and Isolation and one-off
                          destroyed               tasks

  `persistent`            Profile/state reused    Approved authenticated
                          across jobs             workflows
  -----------------------------------------------------------------------

A session is a logical browser identity/profile, not necessarily a
browser process kept alive forever.

### 6.3 Persistent-session rules

-   Store browser profile/state in encrypted or access-controlled
    storage.
-   Associate session with tenant and allowed worker pool/region.
-   Acquire an exclusive lease before use.
-   Prevent concurrent writes to one profile.
-   Enforce idle expiry and maximum lifetime.
-   Record last job and audit usage.
-   Support reset and revocation.
-   Never return raw cookies/storage state in ordinary API responses.

Playwright storage state can contain cookies and tokens. Treat it as a
credential. Encrypt it at rest and restrict access.

### 6.4 Session placement

Prefer remote object storage for persistent session snapshots. Restore
to a compatible worker at job start, upload a new version after a
successful run, and use one active writer per session. Start with one
job per persistent session at a time.

### 6.5 Browser process lifecycle

A job owns one browser context. A worker may reuse a browser process
only when contexts remain isolated and resource limits are enforced.
Persistent state is explicit and separate from process reuse.

## 7. Job model and state machine

### 7.1 State diagram

``` text
                 QUEUED
                    |
                scheduler
                    v
                DISPATCHED
                    |
              worker accepts
                    v
                 RUNNING
             /      |       \
        success   retry    cancel/timeout
           |        |           |
           v        v           v
       SUCCEEDED RETRY_PENDING CANCEL_REQUESTED
                      |             |
                   requeue       confirmed stop
                      |             |
                   QUEUED        CANCELLED

Other terminal state: FAILED
Other terminal state: TIMED_OUT
```

### 7.2 States

  State                Meaning                                 Terminal
  -------------------- --------------------------------------- ----------
  `QUEUED`             Waiting for scheduling                  No
  `DISPATCHED`         Assigned; awaiting acceptance           No
  `RUNNING`            Accepted and executing                  No
  `CANCEL_REQUESTED`   Cancellation sent                       No
  `RETRY_PENDING`      Retryable failure and attempts remain   No
  `SUCCEEDED`          Successful completion                   Yes
  `FAILED`             Final failure                           Yes
  `CANCELLED`          Cancellation confirmed                  Yes
  `TIMED_OUT`          Deadline exceeded                       Yes

### 7.3 Transition rules

-   `QUEUED -> DISPATCHED`: scheduler assigns eligible worker.
-   `DISPATCHED -> RUNNING`: worker accepts valid lease.
-   `DISPATCHED -> QUEUED`: assignment expires before acceptance.
-   `RUNNING -> SUCCEEDED`: valid completion event.
-   `RUNNING -> RETRY_PENDING`: retryable failure and attempts remain.
-   `RETRY_PENDING -> QUEUED`: backoff elapsed.
-   `RUNNING -> FAILED`: non-retryable failure or attempts exhausted.
-   `QUEUED/DISPATCHED -> CANCELLED`: cancelled before execution.
-   `RUNNING -> CANCEL_REQUESTED -> CANCELLED`: worker confirms stop.
-   Non-terminal state may become `TIMED_OUT`, subject to reconciliation
    of uncertain side effects.

Every transition validates state, actor, tenant, and lease; runs in a
transaction; increments version; writes an event; and writes an outbox
record.

### 7.4 Delivery semantics and idempotency

RabbitMQ and WebSocket reconnection can cause duplicate delivery. Each
job/attempt should have: - `job_id` - `attempt_number` - `dispatch_id` -
`lease_id` - `idempotency_key` - `version` - `execution_id`

Workers deduplicate dispatch IDs, reject expired leases, and report
events with unique IDs. The control plane ignores duplicate event IDs
and rejects stale leases.

**Do not promise exactly-once execution.** A worker can crash after
submitting a form but before reporting success. The external side effect
may have happened. Workflow authors must provide idempotency or
reconciliation logic for side-effecting actions.

### 7.5 Retry policy

Support max attempts, exponential backoff, maximum backoff, jitter,
retryable error categories, deadline, and explicit retry-safety policy.
For uncertain outcomes after a browser crash, record `outcome_unknown`
and require workflow-specific reconciliation before replaying dangerous
operations.

## 8. Worker communication protocol

### 8.1 Transport

-   Worker opens an outbound authenticated WebSocket to the control
    plane.
-   WebSocket carries commands and small event payloads.
-   Large artifacts upload directly to object storage over HTTPS with
    short-lived signed URLs.
-   RabbitMQ is internal to the control plane in V1.
-   Workers do not need direct PostgreSQL or Redis access.

### 8.2 Connection lifecycle

1.  Load provisioned worker identity.
2.  Connect over TLS.
3.  Authenticate.
4.  Send `worker.register`.
5.  Receive configuration.
6.  Heartbeat at configured interval.
7.  Receive `job.dispatch`.
8.  Accept/reject dispatch.
9.  Send lifecycle/progress events.
10. Upload artifacts using signed URLs.
11. Send final result.
12. Receive event-processing confirmation.

### 8.3 Message envelope

``` json
{
  "protocol_version": "v1",
  "message_id": "uuid",
  "message_type": "job.accepted",
  "timestamp": "2026-11-17T12:00:00Z",
  "worker_id": "uuid",
  "correlation_id": "job-uuid",
  "payload": {}
}
```

Use UTC timestamps, Pydantic schemas, message size limits, and explicit
protocol-version validation.

### 8.4 Message types

**Worker to server** - `worker.register` - `worker.heartbeat` -
`worker.capabilities.update` - `job.accepted` - `job.rejected` -
`job.started` - `job.progress` - `job.log` - `job.completed` -
`job.failed` - `job.cancelled` - `worker.draining` - `worker.error`

**Server to worker** - `worker.registered` - `worker.config` -
`job.dispatch` - `job.cancel` - `job.lease.renew` -
`job.artifact.upload_url` - `worker.drain` - `worker.shutdown` -
`server.ping`

### 8.5 Dispatch message

``` json
{
  "protocol_version": "v1",
  "message_id": "uuid",
  "message_type": "job.dispatch",
  "correlation_id": "job-uuid",
  "payload": {
    "job_id": "uuid",
    "attempt_number": 1,
    "dispatch_id": "uuid",
    "lease_id": "uuid",
    "lease_expires_at": "2026-11-17T12:01:00Z",
    "workflow": {
      "name": "invoice_extraction",
      "version": "1.2.0"
    },
    "execution": {
      "browser": "chromium",
      "mode": "headless",
      "session_mode": "ephemeral",
      "timeout_seconds": 300
    },
    "input": {
      "record_id": "example-123"
    },
    "artifact_policy": {
      "screenshots": "on_failure",
      "trace": "on_failure",
      "video": false
    }
  }
}
```

Never put passwords, API keys, raw cookies, or storage-state contents in
dispatch messages. Use secret references or scoped short-lived retrieval
grants.

### 8.6 Acceptance and completion

Acceptance includes `dispatch_id`, `lease_id`, `execution_id`, and
accepted boolean. Completion includes those IDs, status, start/end
times, duration, structured result, and artifact IDs. The server
validates the lease before applying completion.

### 8.7 Reconnection

Reconnect with exponential backoff and jitter. Re-authenticate, report
active execution IDs, and reconcile leases. Never assume an old lease
remains valid after reconnect; server may instruct continue, stop, or
report uncertain execution. Do not blindly restart jobs.

### 8.8 Security

-   TLS for all traffic.
-   Per-worker identity and revocable credentials.
-   Short-lived connection tokens or certificate identity.
-   Credential rotation.
-   Tenant/job/dispatch/lease validation on every event.
-   Message size and rate limits.
-   No secrets in logs.
-   Signed artifact URLs scoped to one object and operation.
-   Worker identity does not imply access to other tenants' jobs.

## 9. Scheduling design

### 9.1 Job requirements

A job may specify browser, mode, session mode, platform, environment,
workflow tags, minimum memory, worker pool, region, priority, timeout,
deadline, and tenant quota.

### 9.2 Eligibility filtering

A worker is eligible only when: 1. Connected and not disabled/draining.
2. Worker lease is valid. 3. Browser, mode, and session mode are
supported. 4. Capacity is available. 5. Tenant/pool scope matches. 6.
Resource requirements are met. 7. Persistent-session placement is
compatible.

### 9.3 Initial scheduling score

``` text
score =
  0.35 * available_capacity
+ 0.25 * memory_headroom
+ 0.20 * cpu_headroom
+ 0.10 * queue_affinity
+ 0.10 * session_affinity
```

Normalize each component to 0--1. Make weights configurable; this is a
starting heuristic, not an optimality claim.

Add priority-aware dispatch, tenant concurrency caps, fairness,
worker-pool labels, session affinity, maximum queue wait, and deadlines.

### 9.4 Concurrency-safe dispatch

-   Claim queued jobs with `FOR UPDATE SKIP LOCKED`.
-   Reserve worker capacity transactionally.
-   Create dispatch and lease records.
-   Change job state to `DISPATCHED`.
-   Insert outbox event in the same transaction.
-   Commit.
-   Outbox publisher sends to RabbitMQ/connection manager.

Never publish dispatch before the database transaction commits.

## 10. Database schema

Use PostgreSQL, UUID primary keys, `timestamptz` UTC timestamps, JSONB
for extensible configuration, and explicit columns for frequently
filtered fields. Every tenant-owned record includes `tenant_id`.

### 10.1 Relationships

``` text
tenants
  |-- users
  |-- api_keys
  |-- worker_pools
  |      |-- workers
  |-- workflows
  |-- jobs
  |      |-- job_attempts
  |      |-- job_events
  |      |-- artifacts
  |-- browser_sessions
  |      |-- session_leases
  |-- audit_logs

outbox_events
```

### 10.2 Tables and fields

**tenants** - `id UUID PK` - `name VARCHAR(160)` -
`slug VARCHAR(100) UNIQUE` - `status VARCHAR(30)` - `settings JSONB` -
`created_at`, `updated_at TIMESTAMPTZ`

**users** - `id UUID PK` - `tenant_id UUID FK` -
`email CITEXT/VARCHAR` - `password_hash TEXT` (if local auth) -
`role VARCHAR(30)` (owner/admin/operator/viewer) -
`status VARCHAR(30)` - `created_at TIMESTAMPTZ` - Unique email within
tenant.

**api_keys** - `id UUID PK` - `tenant_id UUID FK` -
`name VARCHAR(120)` - `key_prefix VARCHAR(16)` - `secret_hash TEXT` -
`scopes TEXT[]` - `last_used_at`, `expires_at`, `revoked_at` -
`created_at` - Show full key once; store hash and non-secret prefix
only.

**worker_pools** - `id UUID PK` - `tenant_id UUID FK` -
`name VARCHAR(120)` - `description TEXT` - `labels JSONB` -
`max_concurrency INT` - `created_at`

**workers** - `id UUID PK` - `tenant_id UUID FK` -
`pool_id UUID FK NULL` - `name VARCHAR(120)` - `hostname VARCHAR(255)` -
`platform VARCHAR(30)` (linux/windows) - `environment VARCHAR(40)`
(docker/linux_headed/windows_native) - `status VARCHAR(30)` -
`capabilities JSONB` - `resource_capacity JSONB` -
`resource_usage JSONB` - `max_concurrency INT` - `active_jobs INT` -
`worker_version VARCHAR(30)` - `protocol_version VARCHAR(20)` -
`last_heartbeat_at TIMESTAMPTZ` - `lease_expires_at TIMESTAMPTZ` -
`disabled_at TIMESTAMPTZ NULL` - `created_at`, `updated_at` - Index
tenant/status, pool/status, lease expiry.

**workflows** - `id UUID PK` - `tenant_id UUID FK` -
`name VARCHAR(160)` - `version VARCHAR(40)` - `package_ref TEXT` -
`entrypoint VARCHAR(255)` - `checksum VARCHAR(128)` -
`config_schema JSONB` - `enabled BOOLEAN` - `created_at` - Workflow
versions are immutable.

**jobs** - `id UUID PK` - `tenant_id UUID FK` - `workflow_id UUID FK` -
`workflow_version VARCHAR(40)` - `idempotency_key VARCHAR(200) NULL` -
`status VARCHAR(30)` - `priority SMALLINT` - `requirements JSONB` -
`input_payload JSONB` - `result_payload JSONB NULL` -
`retry_policy JSONB` - `attempt_count INT` - `max_attempts INT` -
`timeout_seconds INT` - `deadline_at TIMESTAMPTZ NULL` -
`assigned_worker_id UUID FK NULL` - `current_lease_id UUID NULL` -
`version BIGINT` - `created_by UUID FK NULL` - `created_at`,
`started_at`, `finished_at`, `updated_at` - Unique
`(tenant_id, idempotency_key)` when key is non-null. - Index
`(tenant_id, status, priority DESC, created_at)`, assigned
worker/status, created time, and partial non-terminal index.

**job_attempts** - `id UUID PK` - `tenant_id UUID FK` -
`job_id UUID FK` - `attempt_number INT` - `worker_id UUID FK NULL` -
`dispatch_id UUID UNIQUE` - `lease_id UUID` - `execution_id UUID NULL` -
`status VARCHAR(30)` - `started_at`, `finished_at` -
`error_code VARCHAR(80) NULL` - `error_message TEXT NULL` (redacted) -
`outcome_unknown BOOLEAN` - `metrics JSONB` - `created_at` - Unique
`(job_id, attempt_number)`.

**job_events** - `id UUID PK` - `tenant_id UUID FK` - `job_id UUID FK` -
`attempt_id UUID FK NULL` - `event_type VARCHAR(80)` -
`source VARCHAR(30)` (api/scheduler/worker/system) -
`message_id UUID NULL` - `payload JSONB` (redacted) - `created_at` -
Unique `(tenant_id, message_id)` when present.

**browser_sessions** - `id UUID PK` - `tenant_id UUID FK` -
`name VARCHAR(160)` - `browser VARCHAR(30)` -
`session_mode VARCHAR(30)` - `storage_ref TEXT NULL` -
`storage_version VARCHAR(100) NULL` - `worker_pool_id UUID FK NULL` -
`pinned_worker_id UUID FK NULL` - `status VARCHAR(30)`
(available/in_use/expired/revoked) - `last_used_at`, `expires_at` -
`created_at`, `updated_at` - Raw cookies/storage state do not belong in
JSONB.

**session_leases** - `id UUID PK` - `tenant_id UUID FK` -
`session_id UUID FK` - `job_id UUID FK` - `worker_id UUID FK` -
`status VARCHAR(30)` (active/released/expired) -
`expires_at TIMESTAMPTZ` - `created_at`, `released_at` - Partial unique
index allows only one active lease per session.

**artifacts** - `id UUID PK` - `tenant_id UUID FK` - `job_id UUID FK` -
`attempt_id UUID FK NULL` - `artifact_type VARCHAR(40)`
(screenshot/video/trace/log) - `object_key TEXT` -
`content_type VARCHAR(120)` - `size_bytes BIGINT` -
`checksum VARCHAR(128)` - `retention_until TIMESTAMPTZ NULL` -
`created_at`

**audit_logs** - Record actor, action, target, timestamp, and redacted
metadata for worker changes, API-key operations, session changes, job
cancellation/retry, role changes, artifact access, and tenant settings.

**outbox_events** - `id UUID PK` - `aggregate_type VARCHAR(50)` -
`aggregate_id UUID` - `event_type VARCHAR(80)` - `payload JSONB` -
`status VARCHAR(20)` (pending/published/failed) - `created_at` -
`published_at TIMESTAMPTZ NULL`

Insert state change and outbox event in the same transaction.

## 11. API contracts

Base path: `/api/v1`.

### 11.1 Authentication

-   Dashboard: secure session or OAuth/OIDC integration.
-   API: tenant-scoped API keys initially.
-   Worker: separate worker identity/credential, never user API keys.
-   Every request enforces tenant context and role/scope.

### 11.2 Job endpoints

  Method   Endpoint                     Purpose
  -------- ---------------------------- ---------------------------
  POST     `/jobs`                      Submit job
  GET      `/jobs`                      List/filter jobs
  GET      `/jobs/{job_id}`             Job detail
  POST     `/jobs/{job_id}/cancel`      Request cancellation
  POST     `/jobs/{job_id}/retry`       Retry eligible failed job
  GET      `/jobs/{job_id}/events`      Timeline
  GET      `/jobs/{job_id}/artifacts`   Artifact list
  GET      `/jobs/{job_id}/attempts`    Attempt history

### 11.3 Submit job request

`POST /api/v1/jobs`

``` json
{
  "workflow": {"name": "invoice_extraction", "version": "1.2.0"},
  "execution": {
    "browser": "chromium",
    "mode": "headless",
    "session": {"mode": "ephemeral"},
    "timeout_seconds": 300
  },
  "requirements": {
    "platforms": ["linux"],
    "environments": ["docker"],
    "worker_pool": "linux-general",
    "min_memory_mb": 2048
  },
  "input": {"record_id": "invoice-123"},
  "retry_policy": {
    "max_attempts": 2,
    "initial_backoff_seconds": 10,
    "max_backoff_seconds": 120
  },
  "artifacts": {
    "screenshots": "on_failure",
    "trace": "on_failure",
    "video": false
  }
}
```

Headers: - `Authorization: Bearer <API_KEY>` -
`Idempotency-Key: <client-generated-key>`

Response: `202 Accepted`

``` json
{
  "id": "job-uuid",
  "status": "queued",
  "created_at": "2026-11-17T12:00:00Z",
  "links": {
    "self": "/api/v1/jobs/job-uuid",
    "events": "/api/v1/jobs/job-uuid/events"
  }
}
```

### 11.4 Worker endpoints

  Method   Endpoint                                   Purpose
  -------- ------------------------------------------ ---------------------------
  POST     `/workers/register`                        Provision/register worker
  GET      `/workers`                                 List workers
  GET      `/workers/{worker_id}`                     Worker detail
  POST     `/workers/{worker_id}/disable`             Disable
  POST     `/workers/{worker_id}/enable`              Enable
  POST     `/workers/{worker_id}/drain`               Drain
  POST     `/workers/{worker_id}/rotate-credential`   Rotate identity

Runtime connection: `wss://<host>/api/v1/workers/connect`. Use a
short-lived connection token or secure handshake; avoid long-lived
secrets in query strings.

### 11.5 Session endpoints

  Method   Endpoint                           Purpose
  -------- ---------------------------------- ---------------------------
  POST     `/sessions`                        Create persistent session
  GET      `/sessions`                        List sessions
  GET      `/sessions/{session_id}`           Metadata
  POST     `/sessions/{session_id}/revoke`    Revoke
  POST     `/sessions/{session_id}/reset`     Reset browser state
  GET      `/sessions/{session_id}/history`   Usage history

Never return raw browser storage state from metadata endpoints.

### 11.6 Workflow endpoints

  -------------------------------------------------------------------------------------
  Method                  Endpoint                              Purpose
  ----------------------- ------------------------------------- -----------------------
  POST                    `/workflows`                          Register approved
                                                                workflow

  GET                     `/workflows`                          List workflows

  GET                     `/workflows/{workflow_id}`            Workflow detail

  POST                    `/workflows/{workflow_id}/versions`   Register immutable
                                                                version

  POST                    `/workflows/{workflow_id}/disable`    Disable
  -------------------------------------------------------------------------------------

### 11.7 Artifact endpoints

  -----------------------------------------------------------------------------------------
  Method                  Endpoint                                  Purpose
  ----------------------- ----------------------------------------- -----------------------
  POST                    `/artifacts/upload-url`                   Request signed upload
                                                                    URL

  POST                    `/artifacts/{artifact_id}/complete`       Confirm upload/checksum

  GET                     `/artifacts/{artifact_id}/download-url`   Request signed download
                                                                    URL
  -----------------------------------------------------------------------------------------

Large artifacts should not be proxied through FastAPI.

### 11.8 Error envelope

``` json
{
  "error": {
    "code": "WORKER_CAPABILITY_MISMATCH",
    "message": "No eligible worker supports the requested execution mode.",
    "request_id": "request-uuid",
    "details": {"required_mode": "headed"}
  }
}
```

Do not expose stack traces, secrets, internal hostnames, or sensitive
browser data.

## 12. Artifacts, logs, and metrics

### Artifacts

Store screenshots, Playwright traces, optional videos, workflow output
files, sanitized logs, and approved diagnostics.

Object key:

``` text
tenants/{tenant_id}/jobs/{job_id}/attempts/{attempt_id}/{artifact_id}
```

Use MinIO locally and S3-compatible storage in production. Use
short-lived signed URLs, checksums, retention policies, and tenant
authorization.

### Structured logs

Recommended fields: - timestamp, level, service - tenant_id, job_id,
attempt_id, worker_id, execution_id - trace_id, event, message

Redact passwords, API keys, cookies, authorization headers, and
configured personal data. Avoid logging full page contents by default.

### Metrics

-   `browserfleet_jobs_submitted_total`
-   `browserfleet_jobs_completed_total`
-   `browserfleet_jobs_failed_total`
-   `browserfleet_job_duration_seconds`
-   `browserfleet_queue_depth`
-   `browserfleet_queue_wait_seconds`
-   `browserfleet_workers_online`
-   `browserfleet_worker_active_jobs`
-   `browserfleet_worker_cpu_usage`
-   `browserfleet_worker_memory_usage`
-   `browserfleet_browser_launch_duration_seconds`
-   `browserfleet_artifact_upload_bytes`
-   `browserfleet_session_lease_conflicts_total`

Avoid high-cardinality Prometheus labels such as job/user IDs. Put those
in logs and traces.

## 13. Security and isolation

V1 requirements: 1. No arbitrary source-code execution from public job
payloads. 2. Approved workflow registry with immutable
versions/checksums. 3. Tenant-scoped authorization on every access. 4.
Per-worker credentials and revocation. 5. TLS for API, WebSocket, and
artifacts. 6. Encrypted persistent session snapshots. 7. Short-lived
signed artifact URLs. 8. Secret references, not plaintext secrets in
jobs. 9. Browser worker filesystem isolation. 10. CPU, RAM, process,
disk, and time limits. 11. Restrict worker egress where possible. 12.
Prevent untrusted browser contexts from reaching cloud metadata and
internal control-plane resources where feasible. 13. Audit sensitive
operations and artifact access. 14. Retention/deletion policies. 15.
Pinned and patched browser/Playwright versions.

Headed desktop access can expose authenticated pages. Require explicit
authorization, short-lived access, audit logs, and network protection.
Never expose noVNC/RDP directly to the public internet.

## 14. Repository structure

``` text
browserfleet/
├── apps/
│   ├── api/
│   │   ├── browserfleet_api/
│   │   │   ├── main.py
│   │   │   ├── dependencies/
│   │   │   ├── middleware/
│   │   │   └── routes/
│   │   └── tests/
│   ├── scheduler/
│   ├── event_consumer/
│   └── dashboard/
├── packages/
│   ├── common/
│   ├── contracts/
│   │   ├── api/
│   │   ├── worker_protocol/
│   │   └── events/
│   ├── database/
│   │   ├── models/
│   │   ├── repositories/
│   │   └── migrations/
│   ├── scheduler_core/
│   ├── job_engine/
│   ├── session_manager/
│   ├── artifact_manager/
│   └── observability/
├── workers/
│   ├── agent/
│   │   ├── browserfleet_worker/
│   │   │   ├── agent/
│   │   │   ├── environments/
│   │   │   ├── browsers/
│   │   │   ├── sessions/
│   │   │   ├── workflows/
│   │   │   └── main.py
│   │   └── tests/
│   ├── docker/
│   ├── linux/
│   └── windows/
├── workflows/examples/
├── infra/
│   ├── docker-compose.yml
│   ├── rabbitmq/
│   ├── postgres/
│   ├── prometheus/
│   ├── grafana/
│   └── minio/
├── docs/
│   ├── architecture/
│   ├── protocol/
│   ├── api/
│   ├── operations/
│   ├── security/
│   └── decisions/
├── scripts/
├── .github/workflows/
├── pyproject.toml
├── Makefile
├── .env.example
└── README.md
```

Keep contracts independent of frameworks and database code. Keep
business logic out of FastAPI route handlers.

## 15. Testing strategy

### Unit

-   State transitions.
-   Scheduler eligibility and scoring.
-   Retry calculations.
-   Lease validation.
-   Session exclusivity.
-   Tenant authorization.
-   Message schema validation.

### Integration

-   PostgreSQL repositories/migrations.
-   RabbitMQ outbox publishing.
-   Worker registration and heartbeat expiry.
-   WebSocket authentication/reconnect.
-   Artifact upload/download.
-   Concurrent scheduler claims.
-   Duplicate message/event handling.

### Browser

-   Chromium headless.
-   Chromium headed on Linux virtual display.
-   Windows headed smoke test on interactive desktop.
-   Ephemeral cleanup.
-   Persistent session reuse.
-   Browser crash and timeout.

### Failure injection

-   Kill worker during execution.
-   Disconnect WebSocket.
-   Stop RabbitMQ temporarily.
-   Restart API.
-   Expire a lease.
-   Duplicate completion event.
-   Submit duplicate idempotency key.
-   Use stale lease.
-   Attempt cross-tenant access.
-   Lose completion acknowledgement after artifact upload.

### Load

Use Locust or an async load client to measure submission throughput,
queue wait, dispatch latency, worker utilization, completion throughput,
API p95/p99, and recovery time. Document hardware and workload.

## 16. Deployment model

### Control plane Docker Compose services

-   `api`
-   `scheduler`
-   `event-consumer`
-   `outbox-publisher`
-   `postgres`
-   `rabbitmq`
-   `redis`
-   `minio`
-   `prometheus`
-   `grafana`
-   `dashboard`

### Linux Docker worker

Pull a pinned worker image, configure control-plane URL and worker
credential, set concurrency/resource limits, and connect outbound over
TLS.

### Linux headed worker

Install the agent, configure dedicated user and display, install browser
dependencies, optionally configure noVNC behind secure access, and
register the worker.

### Windows native worker

Install pinned Python/Playwright dependencies and the worker package.
Configure worker identity. Run background execution appropriately. For
headed mode, require an interactive desktop and perform a startup health
check.

Use typed environment configuration and placeholders in `.env.example`;
never commit credentials.

## 17. Architecture decision records

  -----------------------------------------------------------------------
  ADR                     Decision                Rationale
  ----------------------- ----------------------- -----------------------
  ADR-001                 Modular monolith        Simpler V1 operations
                          control plane           

  ADR-002                 PostgreSQL is source of Transactional state
                          truth                   

  ADR-003                 RabbitMQ for internal   Durable routing
                          dispatch                

  ADR-004                 Worker-initiated        NAT/firewall-friendly
                          WebSocket               

  ADR-005                 Signed object-storage   Large artifacts bypass
                          URLs                    API

  ADR-006                 Common worker +         Cross-platform
                          adapters                consistency

  ADR-007                 At-least-once +         Realistic distributed
                          idempotent events       semantics

  ADR-008                 Registered workflow     Avoid unsafe arbitrary
                          packages                code

  ADR-009                 Exclusive               Prevent profile
                          persistent-session      corruption
                          lease                   

  ADR-010                 Tenant-aware schema,    SaaS migration path
                          self-hosted deployment  
  -----------------------------------------------------------------------

## 18. V1 acceptance criteria

-   [ ] Tenant can authenticate and submit a job.
-   [ ] Only registered workflows can execute.
-   [ ] Linux Docker worker executes headless job.
-   [ ] Linux headed worker executes headed job.
-   [ ] Windows worker executes headless job.
-   [ ] Windows headed job requires healthy interactive desktop.
-   [ ] Capability filtering prevents incompatible dispatch.
-   [ ] Dispatch leases are durable.
-   [ ] Duplicate idempotency submissions do not create duplicate jobs.
-   [ ] Worker heartbeat and expiry work.
-   [ ] Worker loss is detected and attempts reconciled.
-   [ ] Safe retries work; uncertain side effects are not blindly
    replayed.
-   [ ] Ephemeral context is destroyed after execution.
-   [ ] Persistent session is reused under exclusive lease.
-   [ ] Artifacts are stored externally and authorized.
-   [ ] Job timeline exposes attempts, transitions, and errors.
-   [ ] Logs/traces correlate request, job, attempt, and worker.
-   [ ] Tenant isolation tests pass.
-   [ ] Self-hosted Docker Compose deployment works.
-   [ ] Installation, worker registration, limitations, and recovery are
    documented.

## 19. Recommended implementation order

1.  Repository foundation and local infrastructure.
2.  Database models, migrations, and job state machine.
3.  Job submission API and workflow registry.
4.  Worker registration, heartbeat, and WebSocket protocol.
5.  One Linux Docker worker and headless execution.
6.  RabbitMQ outbox and scheduler.
7.  Linux headed execution.
8.  Windows native worker.
9.  Artifact storage and observability.
10. Persistent sessions and exclusive leases.
11. Recovery and security hardening.
12. Dashboard, deployment, docs, and release.

Do not start with the dashboard. First prove that a job moves reliably
from API submission to a remote worker and back to a durable result.

## 20. First end-to-end vertical slice

1.  Start PostgreSQL, RabbitMQ, Redis, and MinIO with Compose.
2.  Start FastAPI.
3.  Register one approved demo workflow.
4.  Start one Linux Docker worker.
5.  Worker registers.
6.  Submit a headless Chromium job.
7.  Scheduler assigns it.
8.  Worker runs against a controlled demo website.
9.  Worker uploads screenshot and trace.
10. Control plane marks job `SUCCEEDED`.
11. API returns result and artifact metadata.

Only after this works reliably add multiple workers, headed execution,
Windows, persistent sessions, retries, and advanced scheduling.

## 21. Reference documentation

-   FastAPI: https://fastapi.tiangolo.com/
-   Playwright Python: https://playwright.dev/python/
-   RabbitMQ: https://www.rabbitmq.com/tutorials
-   PostgreSQL: https://www.postgresql.org/docs/
-   SQLAlchemy: https://docs.sqlalchemy.org/
-   Alembic: https://alembic.sqlalchemy.org/
-   Redis: https://redis.io/docs/
-   OpenTelemetry Python:
    https://opentelemetry.io/docs/languages/python/
-   Prometheus: https://prometheus.io/docs/
-   MinIO: https://min.io/docs/
-   noVNC: https://novnc.com/

## 22. Final engineering guidance

Playwright already launches browsers very well. BrowserFleet's
differentiator is the infrastructure around it: heterogeneous
scheduling, headed/headless execution, worker failure handling, safe
session reuse, artifacts, observability, and a developer-friendly API.

Build the first reliable vertical slice before expanding scope. Keep
measured benchmarks, architecture decisions, and failure demonstrations
as first-class deliverables.

**Standard to aim for:** another developer can clone the repository,
start the control plane, register a worker on a separate machine, submit
a job, and understand exactly what happened from the job timeline and
artifacts.
