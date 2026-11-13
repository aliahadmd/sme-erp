# Cloudflare migration implementation

Status: in progress. The active objective is the complete migration described in
[the architecture report](CLOUDFLARE_MIGRATION_REPORT.md), preserving the ERP's
functionality. A fresh demo database is authorized. This file records evidence
and outstanding work; the feasibility slice is a gate, not the final deliverable.

Last updated: 13 November 2026. The architecture remains Python Workers with
FastAPI/Pydantic and D1. The synchronous SQLAlchemy adapter passed the isolated
runtime proof; the application currently uses typed native D1 repositories and
batches for explicit persistence and atomic multi-statement commands. No
complete architecture rewrite is needed following the language/ORM discussion.

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
- The proof, persistence foundation, authentication, management, accounting and
  order/quotation handlers have passed 97 local backend tests. The frontend has
  passed 13 tests, its TypeScript/
  Vite build and lint. Backend lint, formatting, target-wheel installation,
  generated-schema checks and Build Output validation also pass.
- The existing ERP production deployment and production database are unchanged.

## Observed remote evidence

The authenticated feasibility Worker is deployed at
`https://erp-feasibility.aliahad.workers.dev`. The financial command suite below
was verified on version `b80c34c0-5db5-4794-bdec-055719249990`; the later password
proof is recorded separately below.

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
This database now serves the isolated staging application. It
contains the fresh organization, branch, administrator, 84 permissions and
seven system roles, 16 chart-of-account entries, five settings and a base
currency, plus synthetic verification records. Core management and master-data
handlers, manual accounting and order/quotation APIs now use it; stock/invoice/
payment workflows remain unported. Seven application migrations are recorded
by name/hash, including Unicode search keys and immutable journal history.

| Resource | Identifier |
|---|---|
| Feasibility D1 | `601bfd49-90f1-430e-8e09-5a2973c9134f` |
| Recovery proof D1 | `052390d0-c0d7-43ca-95bc-9bb3b54a0b2f` |
| Full-schema staging D1 | `0f278204-3190-49e1-96d2-a9312fa57e3d` |
| Private proof R2 | `erp-feasibility-backups-20261113` |

## Authentication and typed repositories

The separate application Worker is deployed at
`https://erp-migration-staging-20261113.aliahad.workers.dev`. The first verified
application version is `1dfd4970-5dfb-462d-af03-f3470d61a204`.
The latest authentication verifier also passes on version
`5d0cdae7-0d0f-47ed-8801-fcd6bb514e95`.

- Typed repositories cover all 45 modeled tables. Integer/decimal projections
  use text; writes use integer casts and original precision/scale bounds.
  Native batch plans include revisions, organization/parent ownership guards,
  audit and idempotency. Local tests exercise stale writes, exact extremes and
  cross-organization document-line access. Core authentication exercises the
  UUID, boolean, JSON and datetime paths on actual D1.
- The existing Pydantic schemas and permission catalog are reused in the Worker
  bundle. Only portable source files are included. The original pagination
  helper now imports AsyncSession for type checking only, avoiding an otherwise
  eager greenlet dependency; its original pagination/schema interface was
  verified with the original API environment.
- Python `argon2-cffi==25.1.0` works with PyEmscripten binding/CFFI wheels.
  Argon2id uses 19 MiB, two passes and one lane with independent salts. The
  real Worker matches a native known vector and verifies correct/incorrect
  passwords. One proof request containing four KDF operations recorded 505 ms
  CPU and 515 ms wall time in Workers observability, on proof version
  `10da67ca-df1d-4dbb-97ec-9dc6f5ea98db`. In-Worker clock readings do not measure
  synchronous CPU time. [OWASP parameters](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- The 15-minute access-token and HTTP-only refresh-cookie contract is retained.
  D1 stores hashed refresh credentials and authoritative sessions with absolute
  seven-day expiry. Session rotation claims a token conditionally in one batch;
  confirmed reuse revokes the family. Existing access authorization stops on
  logout, reuse or account deactivation.
- Native remote checks pass bootstrap, login/current-user permissions, cookie
  flags, refresh rotation/reuse, competing refresh claims and logout. Fifteen
  simultaneous unknown-account logins yield ten 401s and five 429s through the
  persistent Durable Object failure budget. Local tests also force two refresh
  readers to see the old token before either claim commits, and inject account
  deactivation between validation and commit.
- Login sessions, last-login timestamps and audit commit together. A D1 check
  after the first verifier run showed three login audits for three sessions,
  all revoked as expected, and no foreign-key violations.

Staging credentials are generated in ignored files with mode 0600. The
one-time bootstrap is secret-gated, staging-only and refuses an initialized
database; it is not a replacement for the full business demo seed. The repeatable
checks are `cloudflare/scripts/verify_auth_remote.py` and
`cloudflare/scripts/verify_password_remote.py`.

## Core management and master data

Application version `d936d542-39f9-41b6-bc6d-b06b32f8cbd6` passes the remote
management verifier against the isolated staging D1 database.

- Organization, branches, users, roles, settings, numbering, email preferences,
  audit-log reads and notification read/count APIs retain the original routes
  and Pydantic contracts. System roles are protected; role edits take effect on
  subsequent authorization, and password resets/deactivation revoke sessions.
- CRM contacts, catalog products/categories/units/taxes and currencies/FX rates
  now have native D1 CRUD handlers. Merged partial updates enforce contact and
  service-product invariants. Decimal inputs use their original scales and
  half-even rounding. Unicode casefold search keys are maintained atomically
  rather than relying on SQLite's ASCII-only lowercasing.
- A private organization-scoped `ERPCommands` Durable Object serializes command
  planning with an asyncio mutex. The D1 batch remains the commit boundary:
  authorization/revision assertions, numbering, records, audit and idempotency
  receipts commit together. Every command rechecks the caller's authorization.
- Request fingerprints use a separate secret HMAC key, including commands with
  passwords. Public responses/audits omit credentials. The frontend generates
  one idempotency key per mutation and preserves it across authentication retry.
- Local checks cover concurrent duplicate creation, distinct numbering,
  failure rollback after audit, exact 18-digit values, half-even ties, reference
  constraints, authorization revocation and replay of 204 responses. Eighteen
  existing client-only API scenarios run unchanged against the new application;
  this is a subset of the original suite, not full workflow parity.
- The remote verifier confirms duplicate suppression, numbering, Unicode/tag
  search, exact stored product prices, rounding, role restrictions, password
  reset, preferences and audits. It uses synthetic staging records; no
  production data or traffic is changed.

Run `cloudflare/scripts/verify_management_remote.py` to repeat the remote checks.
Catalog AI endpoints and overdue-notification generation remain pending with
the AI/background-job phase. Notification count/read APIs alone do not provide
that behavior. The minimal foundation seed is not the complete business demo.

## Accounting journal and exact reports

Application version `762f2b21-c83f-49db-be40-0064b233f86f` passes
`cloudflare/scripts/verify_accounting_remote.py --failure-probe` on staging.

- Chart-of-account creation/update, balanced manual journals, journal browsing,
  the account ledger and dated trial balance retain the original API contracts.
  Portable accounting schemas are shared with the old server. Two original
  manual-entry/ledger scenarios run unchanged against the Worker application.
- The reusable `Ledger` plans journal headers/lines in its caller's D1 batch.
  Numbering, current account/authorization guards and manual-entry audit share
  that commit. Explicit per-command account caches carry newly planned system
  accounts without pretending pending writes are visible to D1 reads.
- Source reversal mirrors every original line and account, independent of
  subsequent mappings/rates. Local tests check multiple original entries,
  duplicate reversal rejection and exact return to the original balances. This
  helper still needs integration with the remaining document/payment modules.
- Journal UPDATE/DELETE operations are rejected by database triggers. Exact
  report accumulation uses Python integer cents, with immutable header/line
  high-water marks and 500-row reads so a later backdated posting cannot enter
  an in-flight report. No monetary SQL SUM or floating-point aggregation is used.
- Local checks inject a failure at every manual-journal batch boundary, race
  duplicate/distinct commands, force account deactivation before commit, reject
  invalid/empty/overflow journals and verify snapshot paging and half-even ties.
- On real D1, a temporary staging trigger forced failure after journal-line
  insertion. Header, lines and receipt rolled back; a retry with that key
  committed. Six concurrent identical requests then produced one 20-line entry
  with maximum 18-digit coefficients, and its exact totals exceeded SQLite's
  integer-SUM range. A separate 600-line entry committed atomically, and ledger,
  date-filtered trial balance and stored journal detail matched the exact values.

The temporary fault trigger was removed after verification. This verification
added four synthetic journal entries and 624 lines in dedicated proof accounts; the
production database is unchanged. Journal immutability and foreign-key checks
also pass on D1. The failure-probe option requires
`cloudflare/fixtures/accounting_fault_probe.sql` on isolated staging;
normal verifier runs omit it. These checks do not establish production capacity
or complete buy/sell workflow parity.

## Sales, purchasing and quotations

Application version `124d60e4-2d2c-413b-a91d-349d6210947c` passes
`cloudflare/scripts/verify_documents_remote.py` on isolated staging.

- Sales/purchase orders support list/filter/search/detail, create/draft edit,
  confirm/cancel and per-product outstanding quantities. Quotations support
  draft edit, send/accept/reject/cancel and accepted-quote conversion. The
  original contracts, state tables and pure progress calculations are shared.
- Catalog name/unit/tax/price snapshots and effective-date FX rates are guarded
  in the same D1 command. Base conversion divides by the stored rate. Numeric
  inputs round to declared scales before line/header calculations; zero-after-
  rounding quantities and overflowing totals reject the entire command.
- Header, matching lines and pagination totals are fetched in one D1 read
  batch. Unicode casefold keys are updated atomically for number/party search.
- Conversion creates the order, copies agreed line snapshots, marks the quote
  converted, allocates the number and writes both audits in one guarded batch.
  Staging races with six different keys produce one order; replay with the
  winning key returns that result. Catalog changes do not reprice the offer.
- `OrderFlow` carries planned line/header revisions across repeated changes in
  one command. Local tests cover duplicate-product FIFO, free-text billing,
  partial movement/invoicing, closure, releases, stale competing commands and
  cancellation guards for both sales and purchasing. These helpers still need
  integration with real receipt/delivery/invoice/payment endpoints.
- Confirmation events are persisted in the transactional outbox. Staging has
  one event per confirmed sales/purchase order despite retries. Queue dispatch
  is pending. The bounded quotation-expiry planner is tested; Cron wiring is
  also pending, so automatic expiry is not yet an operational staging feature.
- Eight additional existing order/quotation API scenarios run unchanged with
  the new fixtures. New checks force rollback during conversion and event
  creation, reject foreign-organization references and detect a newly inserted
  effective FX rate before commit. The remote verifier also checks draft
  replacement, lifecycle guards and exact 18-digit prices. A direct D1 check
  confirms the converted order's rate of 4 and base total of 31.46.

The historical quotation gross-rounding rule remains explicit in the shared
totals engine; orders retain their per-line gross rounding. Migration `0007`
precedes the first document writes in this fresh D1 database. Synthetic staging
documents are not the full business demo, and production remains unchanged.

## Work still required

The full ERP migration remains incomplete. Next, port inventory, invoicing,
HR and the remaining reporting handlers onto the typed
repositories and atomic command path, and prove buy/sell/partial/credit/refund/FX
workflows. Then
complete Queues/Cron, R2 production backup/recovery, Email Sending, Workers AI,
overdue notifications, demo seeding, frontend assets and browser/role parity,
production environment and CI configuration, capacity acceptance, cutover and
safe old-runtime retirement. Forced Worker/DO restart and network-loss proofs
also remain open. The synthetic proof is not the migrated ERP.
