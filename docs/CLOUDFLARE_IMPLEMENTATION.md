# Cloudflare migration implementation

Status: in progress. The active objective is the complete migration described in
[the architecture report](CLOUDFLARE_MIGRATION_REPORT.md), preserving the ERP's
functionality. A fresh demo database is authorized. This file records evidence
and outstanding work; the feasibility slice is a gate, not the final deliverable.

Last updated: 14 November 2026 (UTC+08:00). The architecture remains Python Workers with
FastAPI/Pydantic and D1. The synchronous SQLAlchemy adapter passed the isolated
runtime proof; the application currently uses typed native D1 repositories and
batches for explicit persistence and atomic multi-statement commands. No
complete architecture rewrite is needed following the language/ORM discussion.

## Execution stages

- [x] Prove basic Python/FastAPI/Pydantic and SQLAlchemy D1 adapter execution in Workers.
- [x] Prove exact 18-digit storage/transport, guarded atomic commands, concurrency,
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
- The proof, persistence foundation, authentication, management, accounting,
  orders/quotations, inventory, invoicing/payments, HR, reporting, background delivery
  AI, mail delivery and full backup/recovery have passed 306 local backend
  tests. The frontend has passed 14 tests, its TypeScript/
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
handlers, accounting, orders/quotations, inventory, invoicing/payments, HR and reporting now use it.
Thirteen application migrations are recorded
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
  cancellation guards for both sales and purchasing. Inventory now integrates
  them with receipt/delivery endpoints; invoices also use this guarded progress path.
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

## Inventory postings and reversals

Application version `d9007edb-0c03-41b1-89e7-d541049be46a` passes
`cloudflare/scripts/verify_inventory_remote.py --failure-probe` on staging.

- Warehouse defaults, stock/low-stock reads, movement history, receipt/delivery
  create/post/void and adjustment create/post preserve the existing API shapes.
  Order-sourced receipts copy outstanding goods quantities and discounted net
  costs divided by the PO's FX snapshot. Services/free-text do not move stock.
- `StockBook` carries pending product/warehouse balances across repeated lines.
  Stock, immutable moves, cost snapshots, source-order progress, balanced journal,
  numbering, audit and outbox share one native D1 batch. The database also
  enforces one default warehouse and one reversal per original move.
- Migration `0008` adds immutable exact-decimal before/after cost snapshots.
  Reversals walk original moves in reverse order, restore prior costs when the
  state matches and otherwise remove/restore the original movement cost. This
  fixes the old receipt-void behavior that could retain today's average while
  reversing a different original journal value. A consumed receipt that would
  leave residual value at zero stock rejects until dependent moves are voided.
- Local tests run seven original inventory HTTP scenarios unchanged in their
  reference sequence. Additional checks cover discounted FX/FIFO partial
  receipts, zero/negative policy, scale/overflow bounds, lost-race rollback,
  failed voids and immutable history. A 360-distinct-product receipt exercises
  bounded reference/stock reads rather than one query per line; the test binding
  enforces D1's per-statement binding bound.
- On real D1, a temporary trigger rejected a receipt during journal-line
  insertion. Stock, source progress, journal and idempotency claim rolled back;
  the original receipt stayed draft. A subsequent valid receipt using that key
  committed. The script also races deliveries against insufficient stock and
  verifies moving-average costs, exact ledger reconciliation, interleaved
  post/void progress, mixed-sign adjustments and an 18-digit unit cost.
- A real 360-line post/void cycle succeeds with exact cost snapshots. This run
  added 739 moves with 739 snapshots and no missing snapshots or foreign-key
  violations. D1 rejects movement, snapshot and posted-document mutation. The
  temporary failure trigger has been removed. OpenAPI also serves successfully
  after these checks; the complete API is still being ported.

The first inventory deployment was rejected by the Python startup memory gate.
The application was unnecessarily importing SQLAlchemy through shared money
and pagination helpers. Those imports are now lazy for the legacy server;
application startup imports no ORM modules, while the isolated SQLAlchemy proof
remains available. A fresh-process regression check covers that boundary and
the legacy numeric aliases. The corrected Worker deployed successfully with a
reported 4,298 ms startup. Cloudflare's isolate memory bound still applies, so
realistic capacity acceptance remains open. [Workers memory limits](https://developers.cloudflare.com/workers/platform/limits/#memory)

The failure-probe option requires
`cloudflare/fixtures/inventory_fault_probe.sql` on isolated staging and removal
afterward; normal runs omit it. Inventory notification events are durable in
the outbox, but queue delivery/low-stock notifications are not operational yet.
The integrated buy/sell cycle is recorded below. No production traffic or
database was changed.

## Invoices, payments and settlement

Application version `3c607e2b-8c38-46bd-a462-eae04f5552ea` passes the invoicing
acceptance scenarios on the isolated staging Worker/D1. Cloudflare reported
4,387 ms startup for this version.

- Invoice CRUD, source-order billing, AR/AP posting and voids, credit notes,
  incoming/outgoing payments, on-account allocations, refunds and statements
  preserve the original contracts and permission checks. Nine original invoice
  scenarios and additional original accounting/audit cases run unchanged locally.
- Numbering, invoice/payment balances, source progress, ledger entries, audit,
  outbox and command receipts share one guarded D1 batch. Stale competing payment
  plans and credit-cap plans have one complete winner, including credits of an
  already-paid invoice. Explicit source-line IDs preserve free-text and duplicate
  product order progress through draft edits and voids.
- Migration `0009` records immutable base-currency clearing effects. Partial
  settlements retain their original amounts; the last settlement clears the
  remaining base cents. Credit application and late allocation book FX differences;
  voids reverse stored journals rather than today's rates. Database guards protect
  posted financial content, invoice lines, payments and allocation/effect history.
- Statements expose document currency, base balance and per-currency totals rather
  than adding unlike currencies. The payment UI selects currency and limits FIFO
  allocation to matching invoices; refunds use their credit note's currency.
- Local checks inject failures at each invoice/payment commit stage and verify
  unchanged numbering, source progress, balances, books, audit, outbox and receipts.
  Additional cases cover residual cents after arbitrary payment voids, credit FX,
  125-invoice allocations under the binding limit and 360-line invoices.
- Real D1 journal-line faults roll back invoice progress and payment settlements.
  Reusing each failed key for the corrected operation commits once. Six concurrent
  payment retries return the same result, while different keys cannot overallocate.
  Credit refunds, late allocations, changed FX and their exact reversals pass.
- The maximum valid six-place unit price with a quantity of 10,000 produces
  `9999999999999999.99`; invoice, payment and reversal preserve it exactly. The
  initial verifier incorrectly supplied that money amount as a unit price, which
  correctly rejected its precision; the valid price/quantity check now passes.
- A complete real buy/sell/pay cycle exercises billing before receipt and billing
  after delivery. Both orders close, AR/AP/GRNI clear, inventory and COGS reconcile,
  paid-invoice voids reject, and dependent voids restore original stock/books and
  reopen source progress. No foreign-key violations or residual assertions remain.

The verifier is `cloudflare/scripts/verify_invoicing_remote.py`. The initial
`--failure-probe` run passed rollback, settlement, FX, refund and concurrency cases;
the corrected `--precision-only` and `--trade-only` runs passed on the same version.
The temporary `cf_invoicing_rollback_probe` trigger was removed and its absence
verified. Future complete runs use the corrected default suite; failure injection
requires `cloudflare/fixtures/invoicing_fault_probe.sql` only on isolated staging.
Customer email requests currently persist in the outbox; queue dispatch and actual
delivery remain pending. The frontend is built/tested locally, with full browser
and role acceptance still open. This milestone does not complete the migration.

## HR and leave ownership

Application version `c92d36d9-8362-4501-a461-3be3c3f7e091` passes
`cloudflare/scripts/verify_hr_remote.py --failure-probe` on isolated staging.
This deployment reported an 8,311 ms startup; capacity/startup headroom acceptance
remains open as the remaining modules are added.

- Departments, employees, login-to-employee links, leave types, annual allowances,
  requests and approve/reject/cancel actions preserve the original API shapes.
  The original three HR scenarios and two self-service/audit regressions pass
  unchanged against the native D1 application.
- Employees see and request their own leave; approvers can manage other employees'
  requests, and cannot decide their own. Decisions notify the linked employee.
  Request creation notifies active approvers, including superusers. These in-app
  notifications, employee/request revisions, audit and receipt commit together;
  this path sends no external email.
- Every request creation or decision touches its employee revision in the same
  batch. Direct competing overlap/allowance plans and final decisions have one
  winner independently of the coordinator lock. Pending and
  approved commitments count against each calendar year's allowance.
- The shared Monday-Friday date helper now computes by weeks/year rather than
  iterating every day; leap-year and date-boundary oracle checks agree. Public
  holidays remain outside the existing model. Leave-type accrual configuration
  is retained, and days use exact one-place storage/transport.
- Migration `0010` adds supporting indexes and protects request content/final
  decisions from mutation/deletion. Real D1 rejects even a no-op update to a
  finalized request. Local failures at each request commit stage leave employee
  state, requests, notifications, audits and receipts unchanged.
- Real D1 notification-insertion failure rolls back the request and its command
  claim; a corrected request with that key succeeds once. Concurrent retries,
  competing decisions, overlap/weekend guards, allowance exhaustion, cross-year
  requests, cancellation, role-scoped reads and no-self-approval all pass.

The temporary `cf_hr_rollback_probe` trigger has been removed and its absence
verified. Synthetic test users were deactivated and their sessions revoked;
foreign-key checks and residual-assertion checks are clean. The shared schemas
and date helper remain importable through the legacy HR router. HR browser/role
acceptance remains part of the overall frontend milestone. Readiness and the
91-path OpenAPI document both serve successfully after the remote checks.

## Reporting and consistent paged reads

Application version `e25b15c6-4be7-41db-b297-aed78231a6f4` passes the complete
`cloudflare/scripts/verify_reporting_remote.py` on isolated staging. The later
version `5c88a82f-b8b3-452a-8117-009909aad0af` adds a guarded interleaving proof
and passes `cloudflare/scripts/verify_report_interleaving_remote.py`. Its reported
startup is 2,684 ms; this is an individual deployment observation, not capacity
acceptance or a comparison of cold-start performance across environments.

- Dashboard, sales by customer, purchases by supplier, stock valuation, aging and
  tax summary preserve their existing response fields, base-currency rules and
  `reports.view` checks. Drafts/voids are excluded; credits net against their side;
  normal-invoice counts and inclusive date filters are retained. The original tax
  direction regression now also runs unchanged against the native application.
- SQL projects coefficients/cursors as text. Paged reads return at most 500 rows;
  Python integers accumulate money/quantity totals without SQLite `SUM` overflow.
  Tax conversion groups by stored FX rate and uses exact rational cents, rounding
  half-even once after aggregation. Stock quantity/cost multiplication is exact
  and rounds once per warehouse, preserving the existing report's rule.
- Open base balances use immutable settlement effects, including residual cents.
  For a 0.05 invoice at rate 3, a 0.01 partial payment clears zero base cents;
  aging correctly retains 0.02 instead of re-dividing 0.04 and reporting 0.01.
  Aging excludes unused credits/on-account payments as before; the dashboard nets
  open credit notes and excludes on-account payments from its open-document sum.
- The dashboard emits twelve complete weeks plus the current week, correcting
  the original unused future point. Low-stock counts include never-stocked
  product/warehouse pairs, with count arithmetic rather than a Cartesian scan.
- Migration `0011` adds the business commit epoch and report-date index.
  `UnitOfWork` advances the epoch in the same atomic batch as each business
  command; bootstrap does too. Replays/lost commit responses advance it once;
  failed batches leave it unchanged. Reports retry if it changes between pages,
  and return a retryable `503 report_busy` after three unstable attempts.
- This is application-level validation on primary reads, not a D1 snapshot API.
  Future business/job writers must use the same unit of work. Direct maintenance
  and restore writes must quiesce readers/writers or advance the epoch in their
  atomic batch. Authentication/session-only writes do not alter report inputs.
  D1 Sessions provide sequential consistency, which alone does not pin every page
  to one database version. [D1 Sessions](https://developers.cloudflare.com/d1/best-practices/read-replication/)
- Local checks force a void and a final payment between report pages, verify
  complete retries, exercise busy responses and composite settlement pagination,
  and check role/organization scope, FX/tax snapshots, exact rounding and aggregate
  overflow. The complete remote verifier is also exercised against the native
  test binding.
- On real D1, a customer total of `100000000000000111.90` returns exactly. A direct
  SQL `SUM` over the corresponding valid stored coefficients fails with integer
  overflow. Remote checks also cover all six reports, dated/credit totals,
  original tax-rate groups, never-stocked counts, 601-line paging and permissions.
  Reversing the proof documents restores the starting monetary reports and stock.
- Concurrent report/void calls returned the complete pre-void value in that run.
  The additional staging-only proof deliberately commits a real private-command
  void after reading part of a 501-line document. The epoch changes, the partial
  result is discarded, and the complete retry excludes the voided document. This
  path requires staging mode, the bootstrap secret and a current superuser session;
  production/missing-secret/non-admin cases reject without voiding the document.

There are no active proof invoices, leftover assertions, failure triggers or
foreign-key violations after verification. The business epoch is 76 at this
checkpoint. Readiness and the 98-path OpenAPI document serve successfully.
Shared schemas remain importable through the legacy report router. The frontend's
report contracts are retained; deployed browser acceptance is pending.

## Queues, notifications and scheduled maintenance

Application version `26e50f76-f492-497c-bf2f-3336fc343dca` is deployed with
the staging event queue `erp-staging-events-20261113`, its dead-letter queue
`erp-staging-events-dlq-20261113`, and an every-minute Cron Trigger. The event
consumer uses batches of ten, concurrency two and three retries; the DLQ
consumer uses concurrency one. Both are registered against the same isolated
application Worker. No production domain, VPS or production database changed.

- The dispatcher sends only versioned organization/event IDs and a delivery
  generation. It marks dispatch after Queue acceptance; an ambiguous send can
  repeat the same IDs. Successful API writes attach a bounded dispatch to
  `waitUntil`; Cron repairs pending or fifteen-minute-old uncompleted deliveries.
- Private system commands share the organization coordinator and guarded native
  D1 batch boundary. In-app notices, deduplication keys, email intents, inbox
  receipt, source completion and the business epoch commit together. Transport
  attempt/quarantine metadata does not change financial records.
- Invoice, payment and low-stock notices use current records and permissions,
  including active superusers. Staff email preferences are preserved. Late
  events for voided documents are completed as stale sources. Overdue notices
  exclude credits, and customer reminders have a separate stable key.
- Failed messages receive bounded backoff and go to the DLQ after retries.
  D1 stores quarantined event metadata without copying message bodies into logs.
  Authenticated settings operators can inspect status and replay recoverable
  events. Replay increments the delivery generation, so stale failures or DLQ
  copies cannot mark a later attempt dead. Cron also reconstructs missing
  quarantine records from durable dead outbox rows if DLQ handling fails.
- Quote expiry and overdue discovery process 25 records per page; audit pruning
  processes 100. Each invocation runs at most five pages per job, with D1
  cursor/version checkpoints. Daily job keys prevent repeated effects. Staging
  explicitly uses Shanghai's fixed UTC+08:00 business calendar; this is not a
  general daylight-saving timezone implementation.
- At this milestone, `EMAIL_MODE=disabled` recorded completed disabled intents
  and sent no mail. The later delivery implementation below adds rendering,
  attempt receipts and operator reconciliation. Disabled historical intents
  remain disabled when the staging mode changes.

Observed staging checks:

- Real Queue delivery created exactly four administrator notices for an invoice,
  its overdue balance, a payment and low stock, plus six disabled mail intents.
  Repeated source messages left those notice IDs and mail counts unchanged.
- A synthetic unsupported event recorded four failed attempts, reached the real
  DLQ consumer and became quarantined. After repairing only that test event's
  subscriber type, administrator replay completed generation one. Repeating the
  admin command and delivering both current/old generation copies produced one
  inbox receipt. A late old-generation DLQ copy left it resolved.
- Workers observability records successful automatic `scheduled` invocations,
  independently of the protected manual staging proof. Quote-expiry and audit
  pruning were also exercised with one deliberately backdated staging quote and
  audit record; that fixture update advanced the business epoch in the same batch.
- The synthetic invoice, payment, delivery and receipt are voided. Their 16
  journal lines reconcile to zero on every affected account; stock quantity and
  cost are zero. The final check found 136 completed outbox events and 136 inbox
  receipts, no pending/dead source events, one resolved dead letter, no leaked
  SQL assertions and no foreign-key violations.

Local checks cover rollback after every statement of an event batch, duplicate
delivery and competing plans, ambiguous Queue acceptance, repeated failed
replays, stale generations, permission boundaries, inactive users/preferences,
malformed/foreign messages, DLQ/database failure, resumable overdue scans,
business-date boundaries, quote expiry and retention. Original notification
HTTP scenarios run unchanged after explicit queue delivery; their former
synchronous timing is not asserted for the deployed asynchronous transport.

The deployed Python runtime required two entrypoint corrections discovered by
staging checks: a direct `WorkerEntrypoint` subclass exposes Queue handlers,
and `queue(self, batch, env=None, ctx=None)` accepts the extra arguments actually
passed by this target runtime despite the current two-argument documentation
example. HTTP continues through `asgi.fetch`; the scheduled handler accepts all
four arguments. Revalidate these boundaries on SDK/runtime upgrades.

`cloudflare/scripts/verify_jobs_remote.py` exercises real delivery, maintenance,
automatic Cron and reversals; `--resume` continues its saved staging fixture,
and `--replay-event` verifies an already-quarantined, repaired test event.
This evidence does not complete live mail, backup scheduling/recovery, login
counter expiry, AI jobs, realistic queue/fan-out capacity or full-system parity.

## Workers AI and persistent counter expiry

Version `90dcdd45-75b8-4b3e-9473-c871ac0a6384` adds all eight original AI
operations, the native Workers AI binding and the SQLite-backed `AIBudget`
Durable Object. The staging model is
`@cf/meta/llama-3.3-70b-instruct-fp8-fast`, a Cloudflare-hosted text model.
Its documented input/output shape and availability were checked before use;
model upgrades require fresh quality/runtime checks.
[Model documentation](https://developers.cloudflare.com/workers-ai/models/llama-3.3-70b-instruct-fp8-fast/)

- Echo remains superuser-only; summaries require `reports.view`, and product
  generation/review requires `catalog.product.update`. Disabled requests return
  `ai_disabled` without reserving quota. Provider failure, empty output and
  oversized input have explicit error envelopes without logging prompts.
- Echo, summary, individual suggestions and bulk generation share a daily quota
  per organization/environment. Each actual inference attempt is charged before
  model I/O, including failed or ambiguous attempts. The default limit is 200,
  with 32,000-byte input, 512 output tokens and a 60-second provider wait bound.
  The selected model identifier must be Cloudflare-hosted. No external provider
  API key or shared financial-prompt cache is used.
- Bulk requests use bounded 25-product planning pages and one durable event per
  product. Network inference runs outside the financial command lock. Product
  and category revisions, active requester permissions and missing descriptions
  are rechecked before committing a result. A human edit or permission revocation
  suppresses stale output. Unique pending-task/draft constraints and one inbox
  receipt prevent duplicate drafts. At-least-once delivery can repeat billed
  inference after a crash; each repeated attempt still consumes quota.
- Exhausted bulk quota is persisted as an outbox due time after the next business
  midnight. The Queue message is acknowledged only after that D1 defer write;
  Cron redispatches it when due. Waiting for quota does not exhaust transport
  retries. Actual provider failures still follow retries and DLQ handling.
- Single-product generation remains an unapplied suggestion. Bulk generation
  creates review drafts; accept/discard, product updates and audit use the native
  atomic command boundary. Keyword search remains a keyword feature, including
  Unicode-folded name/SKU/description search; no semantic index is claimed.
- The drafts page now refreshes every five seconds while open, rather than
  relying on one four-second refresh after enqueue. Its toast uses the queued
  count, and duplicate clicks are disabled while submitting. Frontend tests,
  build and lint pass; deployed browser acceptance remains unfinished.

Observed real-runtime checks:

- Cloudflare-hosted echo retained a synthetic sentinel. The report summary
  retained the supplied `123.45 USD` and three invoices. The model returned
  generic factual descriptions from the limited supplied product fields;
  this is a basic quality check, not a general financial-accuracy guarantee.
- Nine bulk tasks completed, with drafts kept unapplied until explicit review.
  One acceptance and its repeated command returned the same outcome; discard
  left the other product unchanged. Unicode description search passed.
- The counter's SQL storage recorded twelve attempts after the initial proof.
  A controlled staging counter fixture then placed it at 199: the next real
  inference succeeded, followed by 429s for both echo and summary. The synthetic
  increment was removed, preserving thirteen actual attempts.
- A second controlled fixture exhausted the quota for one regenerable test
  product. Its event became pending with `ai_budget_exceeded` and a next-day
  due time, without a dead letter. After restoring the quota and advancing only
  that test event's due time, automatic Cron resumed generation. The regenerated
  draft was explicitly discarded; actual attempt count is fourteen.
- Authentication, refresh/reuse/logout and fifteen concurrent unknown-user
  logins still pass after the counter changes. `LoginGuard` now schedules expiry
  before persisting admission state, preserves in-flight permits at window
  rollover and removes expired state in its alarm handler. Workers observability
  records a successful native `LoginGuard` alarm on this version. Tests cover
  early/repeated alarms, restart persistence and rollover safety. AI counter
  alarms retain the active day's quota and remove older rows; their calendar
  rollover has been exercised locally, not yet observed at live midnight.

`cloudflare/scripts/verify_ai_remote.py` exercises the real model, HTTP contracts,
bulk Queue delivery, review and shared quota. Local cases also cover disabled
and forbidden paths, concurrent quota admission, stale source/actor changes,
rollback of draft/inbox writes, repeated generation and next-day deferral.
The last backend check passes 222 tests. The changed frontend passes fourteen
tests, TypeScript/Vite build and lint. Live Email Sending, full recovery,
realistic capacity and complete browser/cutover acceptance are still open.

## Email delivery and reconciliation

The application now includes a Cloudflare Email Sending binding adapter and
durable delivery ledger. Migration `0014_mail_delivery.sql` is applied in staging
with SHA-256 `62214384fb6b7767d3a3624740777ee64c452ccbb45cbada7520fe4a57c6dcec`.
The staging Worker version is `bb2cbab3-32a5-4fe7-ae99-52facb37a02b`, with a
reported startup time of 2,140 ms. Its binding restricts senders to
`erp@aliahad.com`; `EMAIL_MODE=test` prevents outbound provider calls.

- `disabled` stores a completed disabled intent. `test` runs an intent through
  the real Queue, source checks, rendering and D1 attempt receipt, without
  invoking `EMAIL.send`. Historical disabled/test intents are not automatically
  delivered when a deployment enables live mode.
- Live delivery commits a guarded pre-send claim and payload hash before network
  I/O. The call runs outside the financial coordinator lock, with a 60-second
  wait and a 120-second claim lease. Concurrent duplicates cannot claim the same
  intent. A crashed/expired claim becomes uncertain; it is never automatically
  resent. Existing outbox recovery can take fifteen minutes to revisit it.
- Recipient addresses, active staff, notification preferences, permissions and
  invoice/payment status are rechecked before claiming. SQL guards reject
  changes between planning reads and commit. Overdue reminders use the current
  exact open balance and are cancelled after payment. Rendering includes plain
  text and escaped HTML; headers exclude recipient-controlled line breaks.
- A provider message ID records acceptance as `sent`; it is not an inbox-delivery
  receipt. Known validation/configuration rejections become `dead`. Explicit
  quota/rate rejections defer in D1 before the Queue message is acknowledged.
  Unknown responses, timeouts and internal/delivery errors become `uncertain`.
  A late provider receipt is preserved even after an operator replaces/cancels
  that attempt; it cannot overwrite the operator's newer decision.
- `GET /api/jobs/mail` and `GET /api/jobs/mail/{id}/attempts` require
  `core.settings.read`. Responses are bounded and omit addresses, subjects and
  bodies. `POST /api/jobs/mail/{id}/resolve` requires `core.settings.update` and
  a reason. Operators can cancel, confirm provider acceptance with its ID, or
  retry. Retrying uncertain mail requires `allow_possible_duplicate: true`.
  Decisions are audited and share the guarded D1 batch boundary.

The account/domain prerequisite check reports the existing `aliahad.com` sender
domain enabled, arbitrary recipients and a 1,000/day account quota. No DNS or
domain onboarding changes were made. The current Workers API documents no
idempotency key, and `Message-ID` is platform-controlled; `X-ERP-Mail-ID` is a
correlation header, not provider deduplication.
[Workers API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/),
[header rules](https://developers.cloudflare.com/email-service/reference/headers/)

Local checks cover provider acceptance/rejection, quotas, malformed/ambiguous
responses, lost claim/outcome responses, late receipts, source/permission changes,
concurrent plans, operator permissions and rollback at every claim/outcome batch
statement. The full backend suite passes 274 tests; lint, formatting, schema and
Build Output checks pass. `scripts/verify_mail_remote.py` exercises real Queue/D1
test delivery and reverses its synthetic invoice. Live provider acceptance,
inbox receipt and asynchronous suppression/bounce feedback remain unverified.
Observed native test-mode proof on this version: four intents (staff invoice,
staff overdue, customer invoice and manual invoice) each produced one test
attempt and completed event receipt. Repeating the manual email command returned
the same queued response and produced one intent. Operational responses omitted
mail content, and the synthetic invoice was voided after verification.
Eight duplicate copies of these completed event envelopes were then pushed to
the native queue. D1 recorded eight `already_processed` prepare receipts; the
four intents and four test attempts were unchanged. Foreign-key checks were
clean, SQL assertions were empty, and the account still reported zero sends.
No real email has been sent by this migration work.

## Full backup write-pause foundation

Migration `0015_maintenance.sql` is applied in staging with SHA-256
`1c60742155e6ed8356e5786783da207e7baeae24137cde55a614eeb5df80f96e`.
Worker `e6a5ffe5-4b94-4d2a-9153-64a13011e9a6` reports a 2,348 ms startup.
The database contains 186 DML guards: insert/update/delete on all 62 business,
authentication and operational tables. Only the maintenance control row and
ephemeral SQL assertions are exempt. This is a foundation for full backup,
not a completed backup/recovery service.

- A private, database-wide lease carries an owner, increasing fence and bounded
  10-900 second lifetime. Each protected read/renew/release must prove the
  current owner/fence and unexpired lease inside its D1 batch. A stale controller
  cannot renew/release a replacement lease or publish a valid continued snapshot.
  Process/DO restarts do not reopen writes; lease expiry does so without requiring
  a live alarm/controller. A resumed expired backup must discard its partial run.
- Database triggers block every guarded writer, including code bypassing the
  Python adapter and commands planned before the pause. Reads remain available.
  The public error is `503 maintenance`. `/api/jobs/status` exposes active state,
  expiry and fence to settings readers, without exposing the owner token.
- Acquisition waits for active mail claims to settle; new mail cannot commit a
  pre-send claim while paused. Queue transport may already have accepted event
  IDs before a blocked dispatch-metadata update. D1 effects remain paused and
  durable outbox recovery handles those duplicate IDs after release.
- Backup acquisition checks the actual schema's trigger definitions. A new or
  modified table/guard causes explicit failure until its protection is installed.
  Future snapshot pages must exclude ephemeral assertions/control state, project
  integers as text and validate the same lease through final manifest creation.

Ten local cases exercise competing lease claims, stale fences, expiry/restarts,
lost committed/uncommitted responses, active mail draining, blocked sends,
unguarded schema rejection and auth/command/transport rollback. The complete
backend suite passes 284 tests, with lint, formatting and schema checks.
The staging-native pause proof verifies read availability and maintenance
responses for business updates, login, refresh and logout. Its temporary lease
has been released; normal updates and authentication then passed, and the proof
sessions were revoked. All 186 stored trigger definitions match the expected
guards, foreign-key checks are clean and no SQL assertions remain.
The complete native invoicing/payment regression also passed on this version:
FX residuals and reversals, credits/refunds, duplicate/concurrent payments,
18-digit values and the full buy/sell/pay cycle returned to its original
stock and books.
`scripts/verify_maintenance_remote.py` prepares/checks/revokes
an isolated proof session around an operator-held staging lease.

The following phase integrates these guards with full capture, isolated
recovery and operational reconciliation.

## Full R2 backup and isolated D1 recovery

Migration `0016_full_backups.sql` is applied in staging with SHA-256
`bfe6f5cbb6e1f653bb5fe9945cc7bc5e36b690b6eb62c23117ee87ea74775311`.
Worker version `b24d4c24-d39d-4bcd-9a28-2af8ed78f9ca` reports 3,512 ms startup.
The private `erp-staging-backups-20261113` R2 bucket has its managed public
domain disabled. Recovery targets the separate D1 database
`d7bb688c-8009-4eb0-a21f-993310c23fcc`; an occupied target is never reset.

- A separate `ERPBackups` Durable Object advances bounded work. It performs R2
  I/O outside the financial command coordinator. D1 fences, version guards and
  durable checkpoints remain authoritative across controller recreation.
- Snapshots cover the complete schema and all 62 business/authentication/job
  data tables. Seven control/assertion tables are excluded from captured rows,
  including the backup's own progress and restore receipts. The schema contains
  69 tables and retains all 186 maintenance DML guards.
- Data pages preserve row IDs and exact INTEGER coefficients as text. The
  source query bounds page size before crossing the native binding boundary.
  Pages and manifests carry SHA-256 checksums. D1 seals the final consistent
  manifest and releases writers atomically; only then does R2 receive a commit
  marker and private discovery index. A provisional manifest is insufficient
  for recovery.
- Recovery requires an empty target or its own matching anchor. Atomic target
  receipts make DDL/data steps resumable after a lost commit response. Rows load
  in dependency order; nullable self references are repaired in a second pass.
  Every captured page, table count, schema definition and foreign key is checked
  before operational reconciliation. Financial immutability triggers are then
  active in the restored database.
- Reconciliation revokes captured sessions, holds external delivery and AI,
  marks ambiguous mail for operator review, and gives pending restored events a
  sticky recovery flag. Old overdue reminders remain held after global policy
  release until deliberately reviewed. Database restoration does not retract
  external effects delivered after the capture point.
- Minute Cron advances jobs. Daily requests use a business-date idempotency key.
  Retention defaults to 35 days, protects active restores, removes discovery
  markers before data, and deletes through paginated native R2 listings.
  Administrators can cancel an unsealed capture; failed/expired captures cannot
  publish a valid archive.

Twenty-two local backup cases bring the complete suite to 306 passing tests.
They cover full schema/value recovery, 18-digit amounts and int64 row IDs,
nullable self references, lost R2/target/seal responses, controller recreation,
lease expiry, maximum pause duration, corruption, provisional manifests,
occupied/changed targets, cancellation, retention, archive import and roles.
Native R2 metadata contains opaque JavaScript objects inside the list result;
the adapter handles those attributes explicitly. RPC preserves structured
domain errors across Python/JavaScript boundaries.

The first native archive committed 6,591 rows in 176 pages (3,589,998 data bytes).
Capture/publication took 387 seconds in this staging dataset. This measurement
does not establish production-scale recovery or a maximum-data operating limit.
The full native restore completed in 17 minutes 37 seconds, including client
interruption and continued Cron-driven progress. Its result verifies all 62
data tables/176 pages, exact rows, schema and foreign keys before reconciliation.
Independent target reads confirmed the 18-digit `999999999999999999` coefficient,
one cent paid, twelve correctly ordered invoice lines, zero live captured
sessions and the external recovery hold. The source payment and invoice were
then voided, with no residual SQL assertions. The target remains isolated.
These are staging measurements, not accepted production RPO/RTO guarantees.
An additional native capture was expired deliberately after seven pages/298
rows. It failed with `maintenance_lease_lost`, published no discovery entry,
and released normal authentication/writes. Only that failed probe's timestamp
was aged to exercise retention. Native Cron then requested one
`daily:2026-11-14` backup during a temporary current-hour schedule window;
capture continued across the redeploy. That archive completed 6,634 rows in 178
pages/3,606,445 bytes in 383 seconds. Cron then deleted the expired probe's R2
files and page ledger; its terminal metadata remains for diagnosis. The final
deployed schedule is enabled at 02:00 UTC+08:00.

For native lost-metadata acceptance, only the scheduled archive's control row
and 178 page receipts were removed under a SQL assertion after verifying zero
restore references. Its private R2 contents stayed intact. Archive discovery
and import rebuilt all receipts with the same SHA-256
`f65dce0d4b1ba44fb1dea6f7055514c81943f8e7dd2a4558c0f1f2da6ecbe977`,
row/byte totals and manifest. Repeating import returned the completed archive.
Source foreign keys remain clean and SQL assertions are empty. This probe did
not reset or reuse the occupied recovery D1. See the
[operator runbook](CLOUDFLARE_RECOVERY.md) and the ignored verification checkpoints
before resuming an interrupted proof.

## Existing React frontend on Workers Static Assets

The application build now compiles the existing React/Vite SPA and copies its
fresh output into the versioned Build Output assets directory. It explicitly
sets `VITE_API_URL` to an empty string so browser API calls and refresh cookies
use the same Worker origin. The feasibility mode retains its API-only build.
The application config sends `/api`, `/api/*`, `/internal`, `/internal/*`, health,
OpenAPI and ReDoc requests to Python before asset fallback. Other deep links
use Workers' single-page-application handling. The existing UI and routing
framework are retained.

Version `9cc053d1-c2a3-48b3-9c23-284437fe8abc` reports 3,778 ms startup and serves
the frontend at the existing isolated staging Worker URL. All 14 frontend tests,
lint and TypeScript/Vite compilation pass. The build retains its existing
large-bundle and Vite-config deprecation notices; this migration does not claim
a frontend performance optimization.

`scripts/verify_frontend_remote.py` verifies the deployed HTML, JavaScript/CSS,
same-origin configuration and deep-link fallback. Unknown API/internal routes
and protected requests remain JSON even with browser-navigation headers, with
no-store on API/internal paths. Health and OpenAPI stay reachable. Asset output
was scanned for the known private Worker secret values.

A real Chrome browser used the existing sign-in form successfully, received a
secure HTTP-only refresh cookie, and restored the session after page reload.
CRM, products, sales and purchasing navigation loaded their real APIs without
JavaScript errors or failed API calls. A synthetic contact was created, searched,
edited and archived through the existing UI. Sign-out revoked the test session
and removed its refresh cookie. This is initial browser integration evidence,
not complete role/workflow parity.
The new native SDK mail-payload conversion also passed the Queue test-mode proof:
four intents/attempt receipts, idempotent manual request, and source invoice
reversal, without an outbound email call.

## HTTP surface coverage

The original application's current OpenAPI inventory contains 139 method/path
pairs. The native Worker now contains all 139, six job-operations endpoints,
nine backup/recovery operations and four staging-only operations: 158 operations
across 121 OpenAPI paths.
The captured inventory is `cloudflare/fixtures/reference_routes.json`; the
route-presence regression test requires no missing original operation. Route
presence is not proof of full workflow, role or operational parity.

## Work still required

The full ERP migration remains incomplete. Next, complete
realistic backup/recovery capacity and operational alerts, live email acceptance/delivery,
fresh demo seeding and complete browser/role parity,
production environment and CI configuration, capacity acceptance, cutover and
safe old-runtime retirement. Forced Worker/DO restart and network-loss proofs
also remain open. The synthetic proof is not the migrated ERP.
