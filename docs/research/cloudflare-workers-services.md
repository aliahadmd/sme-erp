# Cloudflare Workers and service replacement findings

Researched 12 November 2026 against the repository and current first-party documentation. This file records research and recommendations. Subsequent authorized implementation and provisioning are tracked in the [implementation log](../CLOUDFLARE_IMPLEMENTATION.md). D1 financial-data feasibility is covered separately in `cloudflare-d1-feasibility.md`.

Scope update: the user accepts starting with a fresh demo database. Initial cutover therefore needs deterministic fresh seed data and new credentials, rather than legacy financial-data ETL or hash preservation. Historical migration remains optional future work.

## Recommended service boundaries

Keep the existing React/Vite SPA and business modules. Move runtime code to a small set of Workers: the web/API Worker, an asynchronous jobs consumer, and the scheduler/backup entry points. Use service bindings for private calls and resource bindings for D1, R2, Queues, Durable Objects and Workers AI. Keep the same `/api` origin and existing frontend routes. Cloudflare explicitly supports adapting existing React/Vite applications and serving their build output with Workers Static Assets. [React/Vite guide](https://developers.cloudflare.com/workers/framework-guides/web-apps/react/), [Static Assets](https://developers.cloudflare.com/workers/static-assets/)

| Current dependency | Proposed replacement | Reason and qualification |
|---|---|---|
| nginx serving the SPA and proxying `/api` | Workers Static Assets + API Worker | Existing SPA can remain; configure SPA navigation fallback and explicitly route `/api/*` to application code. No SSR conversion is required. |
| FastAPI/uvicorn API process | Python Worker retaining FastAPI/Pydantic | Preferred feasibility path after the Python/ORM discussion; remove uvicorn and adapt runtime/persistence. |
| Async SQLAlchemy/asyncpg | Synchronous SQLAlchemy 2 + `sqlalchemy-cloudflare-d1` candidate, with direct D1 batch repositories | Adapter remains provisional; its commit/rollback methods do not provide multi-statement transactions. |
| Redis/arq durable jobs | Queues + consumer Worker + dead-letter queue | Suited to email, AI drafts, notifications and bounded export tasks. Consumers must tolerate redelivery and reordering. |
| arq cron | Cron Triggers invoking bounded jobs/queue fan-out | Scheduled work should be idempotent by job + period + organization; calculate business dates in an explicit organization timezone. |
| Redis auth revocation | D1 sessions with atomic conditional refresh rotation, primary reads | Design recommendation: preserve strong correctness at the source of truth. KV is unsuitable for immediate revocation. |
| Redis strict counters | SQLite-backed Durable Objects keyed by login/rate-limit or budget scope | Strongly consistent local counter transactions; do not put all users through one global counter. |
| SeaweedFS/S3 | Private R2 buckets accessed through bindings | Direct object storage; use S3-compatible access only where migration tools or signed browser uploads require it. |
| SMTP provider | Email Service **Email Sending (Beta)** binding | Native Cloudflare outbound transactional email exists now; verify account onboarding, domain DNS and daily quota before depending on it. |
| OpenRouter | Workers AI, optionally AI Gateway | Native models remove the external inference dependency; evaluate output quality and latency before choosing a model. |
| VPS cron deployment script | Workers Builds | Git-connected builds and deploys; configure lint/tests/build gates and isolated environments. |
| `.env` server credentials | Workers secrets; bindings for resources | Prefer scoped bindings rather than broad runtime Cloudflare account API tokens. |
| structlog/container logs | Structured Workers Logs and traces | Operational logs supplement, and do not replace, ERP audit records. |

Sources for queue/cron/auth/storage/runtime recommendations are documented below. Application frameworks and database libraries remain application dependencies. Python Workers can query D1 through its native binding; a Python backend does not need Hono or Drizzle. [D1 from Python](https://developers.cloudflare.com/d1/examples/query-d1-from-python-workers/)

## Python is supported, but the present backend is not a direct lift

Cloudflare currently supports FastAPI in Python Workers using its built-in ASGI server; uvicorn is therefore unnecessary there. Python dependencies can be pure Python, PyEmscripten wheels, or packages already included in Pyodide. This is **not** a claim that Python/FastAPI cannot run on Workers. [FastAPI on Workers](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/), [Python packages](https://developers.cloudflare.com/workers/languages/python/packages/)

The important mismatch is the repository's async SQLAlchemy layer: Cloudflare documents synchronous SQLAlchemy support, while async SQLAlchemy ORM is unsupported because greenlet is unavailable. Current Hyperdrive docs demonstrate asyncpg itself working against PostgreSQL, but that does not make async SQLAlchemy work and does not turn asyncpg into a D1 driver. D1 requires a persistence/transaction redesign regardless of the API language. [Python SQLAlchemy compatibility](https://developers.cloudflare.com/hyperdrive/examples/python-workers/#sqlalchemy-support)

**Updated recommendation:** retain Python/FastAPI/Pydantic, preserving Decimal financial logic and existing contracts. The ORM candidate is synchronous SQLAlchemy 2 with the community `sqlalchemy-cloudflare-d1` adapter's native binding integration. Use explicit native D1 batches for every atomic multi-statement operation. If broader adapter validation fails, direct D1 repositories remain a Python option; TypeScript/Hono is an alternative if the Python runtime itself proves unsuitable. The basic end-to-end Worker proof now passes concurrent ORM reads and single-statement writes with exact 18-digit values. Full ERP suitability remains provisional; see [current evidence](../CLOUDFLARE_IMPLEMENTATION.md). [Adapter documentation](https://github.com/CollierKing/sqlalchemy-cloudflare-d1)

### ORM candidate and its transaction boundary

The inspected release is `sqlalchemy-cloudflare-d1` 0.4.1, marked alpha. `create_engine_from_binding()` supports synchronous SQLAlchemy through Pyodide's `run_sync()` bridge; the package's async SQLAlchemy extra still needs greenlet and is not the Worker path. The repository includes Worker integration tests. Our feasibility phase must independently test FastAPI/ASGI execution, concurrent request/session/cursor isolation, data types, exact money transport, and package compatibility. [Package documentation/status](https://pypi.org/project/sqlalchemy-cloudflare-d1/), [release source](https://github.com/CollierKing/sqlalchemy-cloudflare-d1/blob/7b127f4d14895392151a6eeb2e031f4051598041/src/sqlalchemy_cloudflare_d1/connection.py)

In that source, synchronous binding connection `commit()` and `rollback()` are no-ops, and `executemany()` loops over individual executions. Ordinary ORM sessions therefore cannot supply all-or-nothing document/journal/audit/outbox writes. Native D1 batch repositories must own that commit boundary; a successful CRUD example does not validate financial transactions. Default SQLite `Numeric` mapping also does not establish PostgreSQL-equivalent precision. These limitations apply even if simple reads and individual statements pass. [Inspected connection implementation](https://github.com/CollierKing/sqlalchemy-cloudflare-d1/blob/7b127f4d14895392151a6eeb2e031f4051598041/src/sqlalchemy_cloudflare_d1/connection.py)

The original implementation uses `argon2-cffi` (`api/app/core/security.py`). Subsequent implementation has verified compatible PyEmscripten wheels and retained Argon2id in the Python Worker, using 19 MiB, two passes and one lane for fresh credentials. Native/WASM vector checks and concurrent-login admission tests pass; see [observed evidence](../CLOUDFLARE_IMPLEMENTATION.md#authentication-and-typed-repositories). Legacy hashes are not imported for this demo reset. If preservation is later requested, treat encoded-hash compatibility and rehash as a separate migration. [Python packages](https://developers.cloudflare.com/workers/languages/python/packages/), [OWASP Argon2id parameters](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

## Redis/arq replacement: split by responsibility

### Durable side effects

Use Queues for the repository's `send_email`, bulk description generation and similar independent jobs. Queues delivers **at least once** and does not preserve publication order. Configure retries with backoff, acknowledge messages only after the relevant operation succeeds, and configure a DLQ: messages exhausting retries without one are deleted. Keep job status and replay records in D1, because the queue is a transport with finite retention. [Delivery guarantees](https://developers.cloudflare.com/queues/reference/delivery-guarantees/), [Ordering behavior](https://developers.cloudflare.com/queues/reference/how-queues-works/), [Dead-letter queues](https://developers.cloudflare.com/queues/configuration/dead-letter-queues/)

**Proposed ERP pattern:** commit the financial document, journal, audit entry and an outbox event together in D1. A dispatcher sends pending outbox IDs to Queues; a periodic sweep repairs missed dispatches. Consumers record unique `(event_id, consumer)` receipts and use idempotent database commands. A crash after enqueue but before marking dispatched produces a duplicate, which is expected. Never move authoritative journal posting to an asynchronous queue merely to make migration easier. D1 and Queues do not form one transaction. An email accepted externally followed by a crash before the receipt is recorded can still be resent; end-to-end exactly-once email delivery is not established by the current API documentation or this design. [Email Workers API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/)

At the research date the Queues limits include 128 KB per message, 100 messages per consumer batch, 15 minutes consumer wall time, configurable CPU up to 5 minutes, and paid retention up to 14 days. Store export/file bodies in R2 and pass object references through Queues. [Queues limits](https://developers.cloudflare.com/queues/platform/limits/)

Use Cron Triggers for overdue invoice discovery, quote expiry, retention pruning and outbox recovery, with small batches rather than one full-table scan. Cloudflare cron schedules are expressed in UTC; business due dates should use an explicit organization timezone (for this user, Asia/Shanghai is UTC+8) rather than accidentally inheriting server UTC. [Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)

### Workflows where state spans steps

Workflows is useful for D1 export initiation, polling completion, streaming the export to R2 and recording the backup manifest. Cloudflare publishes this exact architectural example. It can later support multi-step integrations or human approval waits. Keep simple independent jobs in Queues; Workflows is not needed for every invoice or notification and does not make separate systems one ACID transaction. [D1-to-R2 backup Workflow](https://developers.cloudflare.com/workflows/examples/backup-d1/), [Workflows guide](https://developers.cloudflare.com/workflows/get-started/guide/)

### Security state and counters

KV's cross-location visibility can lag 60 seconds or more and it does not supply atomic read/modify/write transactions. Consequently, do not make it authoritative for refresh-token reuse prevention, logout revocation, permissions, inventory locks or strict AI budgets. D1 primary-backed sessions with conditional rotation are a coherent baseline; a Durable Object is appropriate where one strict counter must be shared across locations. [KV consistency](https://developers.cloudflare.com/kv/concepts/how-kv-works/), [Strict rate limits](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)

Workers Rate Limiting bindings are useful for economical abuse throttling, but do not promise a single exact global counter. Use them as a first layer; preserve strict failed-login lockout and monetary/AI-budget accounting in a consistent store. [Rate Limiting accuracy](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)

A Durable Object's local storage transactions do not transact with D1. Requests can interleave while awaiting external I/O; single-threaded execution alone is insufficient for an organization command gate. The proposed financial coordinator must deliberately serialize its command path and keep D1 commit predicates/idempotency authoritative. Broadly wrapping all I/O in `blockConcurrencyWhile` is discouraged and caps throughput. [Durable Object rules](https://developers.cloudflare.com/durable-objects/best-practices/rules-of-durable-objects/), [State API](https://developers.cloudflare.com/durable-objects/api/state/)

## R2 storage and recovery

Use separate private attachments/export and backup buckets or clearly separated access policies. R2's object read-after-write, deletion, metadata and listing are strongly consistent; access policy changes and public CDN caches have different consistency behavior. Serve private ERP documents through authorization checks or appropriately scoped expiring signed URLs. [R2 consistency](https://developers.cloudflare.com/r2/reference/consistency/), [R2 Workers bindings](https://developers.cloudflare.com/r2/get-started/workers-api/)

Use unique content/export keys, lifecycle retention and backup manifests with hashes and the D1 bookmark/time. Bucket locks can prevent overwrites/deletion during a retention period, but administrators can change lock policies: do not describe them as an unchangeable legal compliance guarantee. A D1 backup and R2 files are not captured atomically together; avoid deleting attachment generations until the associated backup retention has expired and test full restores. [Bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/), [Object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/)

## Native outbound email: supported, with Beta qualification

Email Service provides transactional sending through a Workers binding, REST and SMTP. **Email Sending is still labeled Beta** in current documentation. It requires Cloudflare DNS; onboard the sending domain and its authentication/bounce DNS records. Before sending-domain onboarding, recipients must be verified destinations; after onboarding, arbitrary recipients are allowed. Paid Workers is required for arbitrary-recipient outbound mail, and new accounts have a conservative reputation-based daily quota whose numeric value is account-specific. [Get started](https://developers.cloudflare.com/email-service/get-started/send-emails/), [Limits](https://developers.cloudflare.com/email-service/platform/limits/), [Pricing](https://developers.cloudflare.com/email-service/platform/pricing/)

This makes Cloudflare-only invoice mail and overdue reminders feasible, subject to verifying account eligibility, sending volume and deliverability. Queue delivery, suppression/bounce processing and the existing per-user notification preferences remain application responsibilities. Marketing email is outside the service's stated scope. [Email FAQ](https://developers.cloudflare.com/email-service/reference/faq/)

## AI and operational services

For fully Cloudflare-hosted inference, use Workers AI behind a small provider adapter, keeping explicit review of generated product descriptions. AI Gateway can add observability/rate limiting/caching and route to external providers, but external-provider routing does not mean model inference runs inside Cloudflare. Avoid caching private financial prompts across organizations; preserve disabled and quota-exceeded states. Current Workers AI usage is model-dependent, with neuron billing and an account-level free allowance. [Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), [AI Gateway pricing and logging](https://developers.cloudflare.com/ai-gateway/reference/pricing/)

Replace the server polling deploy script with Git-integrated Workers Builds and configure the existing quality gates before deployment. Pin the compatibility date, use separate staging/production resources, treat database migrations separately from code rollback, and keep secrets out of build output and logs. Cloudflare's published React/Vite integration supports this frontend directly. [Workers Builds](https://developers.cloudflare.com/workers/ci-cd/builds/), [React/Vite guide](https://developers.cloudflare.com/workers/framework-guides/web-apps/react/), [Workers secrets](https://developers.cloudflare.com/workers/configuration/secrets/)

Workers Logs supplies operational diagnostics. The financial audit trail belongs in the application database with its own retention and export policy; paid operational log retention is only seven days. [Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/)

## Price and runtime checkpoints

Public USD rates checked on 12 November 2026; allowances are generally account-wide, not a separate allocation for every ERP environment. Usage, existing account consumption, taxes and contracted pricing are unknown. These are rate inputs, not a total-cost promise.

| Service | Relevant public rate or limit | Primary source |
|---|---|---|
| Workers Paid | $5/month minimum; 10m requests and 30m CPU-ms/month included; then $0.30/m requests and $0.02/m CPU-ms | [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/) |
| Workers Static Assets | Requests served directly as static assets are free/unlimited; running Worker code changes billing | [Static asset billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/) |
| Queues Paid | 1m operations/month included, then $0.40/m operations; a normal sub-64KB message usually takes three operations | [Queues pricing](https://developers.cloudflare.com/queues/platform/pricing/) |
| R2 Standard | 10 GB-month, 1m Class A and 10m Class B operations/month free; then $0.015/GB-month, $4.50/m Class A and $0.36/m Class B; egress free | [R2 pricing](https://developers.cloudflare.com/r2/pricing/) |
| Durable Objects Paid compute | 1m requests + 400,000 GB-s/month included; then $0.15/m requests and $12.50/m GB-s; storage is a separate dimension | [DO pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/) |
| Workflows Paid | Shared Workers request/CPU pricing; 1 GB-month state + 500,000 steps/month included, then $0.20/GB-month and $0.80/100,000 steps | [Workflows pricing](https://developers.cloudflare.com/workflows/reference/pricing/) |
| Email Sending Paid | 3,000/month included, then $0.35/1,000; verified-destination sends free | [Email pricing](https://developers.cloudflare.com/email-service/platform/pricing/) |
| Workers Builds Paid | 6,000 build minutes/month included, then $0.005/minute; 20-minute limit per build | [Build limits/pricing](https://developers.cloudflare.com/workers/ci-cd/builds/limits-and-pricing/) |
| Workers Logs Paid | At this date: 20m log events/month included, then $0.60/m, seven-day retention. **Pricing changes announced for 1 December 2026** | [Logs pricing](https://developers.cloudflare.com/workers/observability/logs/workers-logs/#pricing) |
| Workers AI | 10,000 neurons/day free; overage $0.011/1,000 neurons, model consumption varies | [Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/) |
| Worker execution | 128 MB memory per isolate; Paid default 30s CPU, configurable to 5min. Stream backups/files; benchmark password hashing. | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |

## Remaining pre-implementation evidence

- Validate the synchronous SQLAlchemy adapter inside FastAPI on Python Workers, including concurrent request isolation and exact type transport; use native D1 batches for atomic multi-statement commands regardless of the result.
- Verify Email Sending Beta is enabled/onboardable for the target account, actual daily quota and intended domain. No account eligibility was inspected for this research.
- Benchmark the selected password hash for newly seeded credentials and prove login/rehash behavior; importing legacy hashes is outside the initial fresh-demo scope.
- Exercise retries, outbox crash windows, DLQ replay and ambiguous email acknowledgments in a disposable Cloudflare staging environment after implementation is authorized.
- Check production backup sizes, file counts and export streaming duration; prove backup restoration and coordinated attachment retention.
- Measure real requests, CPU, D1 row reads/writes, daily job counts and logging volume before making a savings forecast.
