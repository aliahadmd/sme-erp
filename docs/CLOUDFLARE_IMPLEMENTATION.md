# Cloudflare migration implementation

Status: in progress. The active objective is the complete migration described in
[the architecture report](CLOUDFLARE_MIGRATION_REPORT.md), preserving the ERP's
functionality. A fresh demo database is authorized. This file records evidence
and outstanding work; the feasibility slice is a gate, not the final deliverable.

Last updated: 13 November 2026. The architecture remains Python Workers with
FastAPI/Pydantic, a provisional synchronous SQLAlchemy D1 adapter, and native D1
batches for atomic multi-statement commands. No complete architecture rewrite is
needed following the language/ORM discussion.

## Execution stages

- [x] Prove basic Python/FastAPI/Pydantic and SQLAlchemy D1 adapter execution in Workers.
- [ ] Prove exact 18-digit storage/transport, guarded atomic commands, concurrency,
      idempotency, and exact backup/restore on D1.
- [ ] Adapt the full API and business modules to the new persistence/runtime.
- [ ] Replace Redis/arq with D1 sessions, Durable Objects, Queues and Cron.
- [ ] Connect private R2, Email Sending and Workers AI with disabled/test modes.
- [ ] Preserve frontend contracts, roles, workflows and browser behavior.
- [ ] Configure isolated environments, builds, secrets, observability and recovery.
- [ ] Deploy and verify the fresh Cloudflare demo before domain cutover.
- [ ] Verify cutover and retire dependencies on the old runtime safely.

## Tooling evidence

- The installed `cf` CLI is 0.15.0; its Python development helper
  `cloudflare-py-dev-server` is not published on PyPI at investigation time.
- The published Cloudflare Python tooling is `workers-py` with
  `workers-runtime-sdk`. The prebuilt bundle now deploys successfully with a
  pinned pnpm patch to `cf@0.15.0`: additional Python module names must retain
  their vendor paths without the CLI's `./` prefix. The vendor build uses the
  committed Pyodide-target lock rather than host-native wheels.
- The target Cloudflare account is accessible through the connected plugin.
- An isolated D1 database, `erp-feasibility-20261113`, has been created and the
  runtime/proof schema applied. It contains no migrated production records.
- The proof and full schema foundation have passed 28 local tests, with lint,
  formatting, target-wheel installation and Build Output validation passing.
- No existing deployment or production database has been modified.

## Observed remote evidence

The authenticated feasibility Worker is deployed at
`https://erp-feasibility.aliahad.workers.dev`. The latest verified runtime
version is `b80c34c0-5db5-4794-bdec-055719249990`.

- FastAPI/Pydantic executes through the Workers ASGI entrypoint; D1 health,
  Decimal half-even rounding and proof authorization pass.
- Twenty-four concurrent requests preserve distinct positive/negative 18-digit
  coefficients at scales 0, 1, 2, 4, 6 and 8. Native writes, ORM reads returning
  Decimal, single-statement ORM writes and native text verification all pass.
- Injecting an assertion failure at each financial command stage leaves stock,
  command receipts, invoices, journal lines, audit and outbox unchanged.
- Two competing direct D1 deliveries against insufficient stock produce one
  complete commit and one conflict. Six simultaneous duplicate commands and
  response replay return one outcome; changed input with the same key conflicts.
- A SQLite-backed Python Durable Object uses an explicit per-object asyncio
  mutex across D1 awaits. Fifteen concurrent eligible commands all commit once.
  In the latest run, their HTTP median was 2,262 ms and maximum was 4,225 ms,
  including time queued behind the other commands. This is a synthetic
  contention observation, not a production capacity acceptance result.
- An earlier recovery run captured 185 rows in eight proof tables in one D1
  read batch, stored the exact JSON in private R2, read it back and restored it
  into a separate empty D1 database. Schema and values matched SHA-256
  `de947eb519c4b5705d460e33e466aa0b3348baad2662d9533f808e302f174d04`.
  This bounded proof does not establish production backup scale or business
  reconciliation.

The verifier is `cloudflare/scripts/verify_remote.py`; its `--recovery` option
requires an empty recovery target and rejects an occupied database. The local
lost-response test injects an exception after commit; the remote test checks
response replay. Forced Worker/DO restart and network-loss tests remain open.

## Business persistence foundation

All 45 original modeled tables have been translated into reviewed, generated
D1 STRICT table definitions. PostgreSQL schema namespaces become table
prefixes; decimal precision/scales become bounded INTEGER coefficients. Foreign
keys, uniqueness, indexes and model defaults are retained, with a revision
column added for guarded commands. Source hashes and tests detect stale model
coverage. Proof-only tables live in fixtures outside application migrations.

The generated schema has been applied to the isolated
`erp-staging-20261113` D1 database. D1 reports all 45 business tables and no
foreign-key-check violations; migration names/hashes are recorded there.
This database has no demo business records or application traffic yet.

| Resource | Identifier |
|---|---|
| Feasibility D1 | `601bfd49-90f1-430e-8e09-5a2973c9134f` |
| Recovery proof D1 | `052390d0-c0d7-43ca-95bc-9bb3b54a0b2f` |
| Full-schema staging D1 | `0f278204-3190-49e1-96d2-a9312fa57e3d` |
| Private proof R2 | `erp-feasibility-backups-20261113` |

## Work still required

The full ERP migration remains incomplete. Next, connect typed repositories and
the existing Python business rules to the new schema, port authentication and
all module handlers, and prove buy/sell/partial/credit/refund/FX workflows. Then
complete Queues/Cron, R2 production backup/recovery, Email Sending, Workers AI,
demo seeding, frontend and role parity, isolated application deployment,
capacity acceptance, cutover and safe old-runtime retirement. The synthetic
proof is not the migrated ERP.
