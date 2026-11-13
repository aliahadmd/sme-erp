# Python Workers migration

This directory contains the isolated financial proof and the ERP application
being ported to Workers. The existing public deployment still uses the
original runtime.
See [implementation status](../docs/CLOUDFLARE_IMPLEMENTATION.md) for the full
migration scope and unfinished work.

The proof uses FastAPI/Pydantic, Python Decimal, synchronous SQLAlchemy with
`sqlalchemy-cloudflare-d1`, and native D1 batches. The adapter's `commit()` and
`rollback()` do not provide atomic transactions. Financial commands always use
the native batch path with SQL assertions and durable idempotency. The
application uses typed native D1 repositories; it does not depend on ORM session
transactions for business changes.

## Build and verify

Run from this directory with Node 24, pnpm 12, uv, and authenticated `cf`:

```sh
pnpm install --frozen-lockfile
uv sync --frozen --python 3.14
pnpm vendor
pnpm test
pnpm lint
pnpm build
pnpm deploy:staging
uv run python -u scripts/verify_remote.py https://erp-feasibility.aliahad.workers.dev
```

The deployment requires the ignored `.secrets.staging.json` with a `PROOF_TOKEN`
property. This credential must be a random secret, never a demo password. It is
not included in generated bundles or logs. Proof endpoints require it as a
Bearer token. The `staging` mode selects only the proof Worker; `app-staging`
selects the application Worker. Other modes are rejected until configured.

`uv.lock` resolves host development dependencies; `pylock.toml` pins the
Pyodide-compatible runtime wheels and hashes. `pnpm vendor` installs that
existing target lock. Change it deliberately with
`uv run python scripts/vendor.py --update-lock` and review the resulting diff.
No host-native extension belongs in `python_modules/`.

## Application authentication and management

The application Worker is separate from the proof and uses the full business
schema in `erp-staging-20261113`:

```sh
pnpm build:app-staging
pnpm deploy:app-staging
uv run python -u scripts/verify_auth_remote.py
uv run python -u scripts/verify_management_remote.py
uv run python -u scripts/verify_accounting_remote.py
uv run python -u scripts/verify_documents_remote.py
uv run python -u scripts/verify_inventory_remote.py
```

`.secrets.app-staging.json` provides independent `JWT_SECRET`, `BOOTSTRAP_TOKEN`
and `COMMAND_HASH_KEY` values. The command key must contain at least 32 random
characters and must remain stable for receipt fingerprint verification. The
ignored `.demo.app-staging.json` holds the fresh
demo's `email`, `password` and `full_name`. Keep both private. On a new, empty
application database, add `--bootstrap` to the verifier to initialize the
organization, branch, administrator, 84 permissions, seven system roles and
minimal accounting/settings/base-currency defaults.
The bootstrap requires its secret and the staging-only enable flag, and
rejects an initialized target. Production configuration must omit it.

Authentication preserves the frontend's access-token/HTTP-only-cookie API.
Password hashing retains Argon2id using pinned `argon2-cffi` and compatible
Pyodide wheels: 19 MiB, two passes, one lane, 16-byte salts and 32-byte output.
Only newly issued hashes with these parameters are accepted for this fresh
demo; no legacy-hash import is provided. `verify_password_remote.py` verifies
the WASM/native boundary with a synthetic vector.

Access tokens last 15 minutes. Sessions have an absolute seven-day expiry.
Refresh credentials are random and stored only as SHA-256 digests in D1;
rotation is a guarded atomic batch. Reuse revokes the session, and each API
authorization checks current session/account state and RBAC on D1's primary.
Login creation, last-login update and audit commit together. A SQLite-backed
Durable Object tracks failures and in-flight checks per hashed `(IP,email)`
scope, with ten failures in a five-minute window.

Core management, CRM contacts, catalog master data and currencies/FX rates have
also been ported. Their writes go through the private `ERPCommands` Durable
Object and commit records, numbering, revision assertions, audit and command
receipts in one native D1 batch. Decimal values remain exact; Unicode search
uses atomically maintained casefold keys. Client mutation keys survive an
authentication retry. The management verifier creates synthetic staging data
and checks these contracts, reference constraints and permission revocation.

Accounting now includes chart-of-account writes, balanced manual journal
posting, journal browsing, ledger and dated trial-balance reads. The reusable
`Ledger` plans financial entries in the caller's batch and reverses stored
source lines exactly. Database triggers protect posted history; report reads
use immutable high-water marks and paged Python integer-cent accumulation to
avoid SQLite SUM overflow. The accounting verifier posts synthetic books in
dedicated staging accounts, including a 600-line journal and exact totals beyond
signed 64-bit aggregate range. Posted proof history is immutable.

Sales/purchase orders and quotations now preserve their CRUD/lifecycle APIs,
catalog and dated FX snapshots, Unicode search and exact totals. Quote conversion
and both audits commit together. Order progress helpers support repeated planned
changes and releases; inventory and invoicing now use them.
Order confirmation events are dispatched through the durable outbox and Queues.
Cron runs bounded quote-expiry, overdue and audit-retention plans with durable
cursor checkpoints.

Inventory now includes warehouses, stock/low-stock and movement reads,
receipts/deliveries with post/void, and adjustments. Stock, original cost
snapshots, order progress, journals, audits and outbox events commit together.
Voids use original costs; consumed receipts that would leave residual inventory
value require dependent documents to be voided first. The remote verifier checks
forced rollback, stock races, exact costs and a 360-line post/void cycle.

Invoicing now includes source-order billing, AR/AP posting, credits, payments,
on-account allocations, refunds, statements and exact voids. Immutable clearing
effects preserve original FX and residual base cents. The payment UI restricts
FIFO allocations by currency. Real D1 checks cover forced rollback, retries,
refunds, 18-digit values and a full buy/sell/pay cycle with exact stock/book reversals.
Invoice email requests become durable mail intents. Staging uses test mode:
Queues renders messages and records D1 attempt receipts without calling the
Email Sending provider. The live adapter records a claim before sending and
preserves ambiguous outcomes for deliberate operator reconciliation. See
[mail operations](../docs/CLOUDFLARE_IMPLEMENTATION.md#email-delivery-and-reconciliation)
for its limits and verification status.

HR now includes departments/employees, linked self-service identities, leave
types and requests, yearly allowance/overlap guards and immutable decisions.
Employee revisions serialize competing plans; in-app notifications and audit
share the request's atomic batch. Real D1 checks cover forced rollback,
concurrent decisions, ownership, cross-year allowances and cancellation.

All six reporting endpoints now use bounded D1 reads and exact integer/rational
aggregates. An atomic business commit epoch detects changes between pages and
retries the complete report; future jobs/maintenance must preserve this writer
contract. Real D1 checks cover totals beyond int64, tax/FX snapshots, 601-line
paging and an intentionally interleaved void that discards the stale partial read.

The last backend verification passed 306 tests and lint/schema checks. The
frontend passes 14 tests and build/lint. Real Queue and Cron
checks verify notifications, deduplication, DLQ replay, quote expiry and audit
retention. Run `uv run python -u scripts/verify_jobs_remote.py` for the staging
delivery/reversal proof. Disabled and test email modes perform no outbound send.
Run `uv run python -u scripts/verify_mail_remote.py` to exercise the native
Queue/D1 test delivery path; this does not prove live email acceptance/delivery.
All 139 original HTTP operations are now present. Workers AI handles echo,
summaries and product suggestions; bulk results enter an atomic review queue.
One daily Durable Object budget covers every actual inference attempt. Quota
exhaustion defers bulk work in D1 for Cron to resume. Run
`uv run python -u scripts/verify_ai_remote.py` for the real-model staging proof.
Login budgets now expire through alarms while preserving in-flight checks.
Live mail, full recovery acceptance, frontend assets, browser/role acceptance and the
full demo seed remain unfinished. This Worker is not yet the complete ERP.

## CLI packaging workaround

The pinned `cf@0.15.0` CLI prefixes uploaded additional-module names with `./`.
For Python's vendored packages that prevents the runtime from finding
`/session/metadata/python_modules`. The committed pnpm patch preserves Python
module names; it leaves other bundle types unchanged. The custom prebuilt
builder follows Cloudflare's Python vendor layout and Build Output format.
This workaround was needed because the CLI's detected Python build helper,
`cloudflare-py-dev-server`, was not published when investigated.

Remove the patch/custom bridge when a verified official `cf` Python build path
supersedes it. Dependency upgrades must repeat the remote runtime checks.

## Recovery proof

The proof can capture all synthetic tables in one atomic D1 read batch, store
an exact JSON snapshot in private R2, read it back, and restore it into the
separate recovery D1 database. Integer columns are projected as text and
restored through explicit integer casts. Checksums cover schema and row values;
foreign keys are checked before the restore batch commits.

Append `--recovery` to the remote verifier only when the configured recovery
database is empty. The endpoint rejects an occupied target and never resets
it. It supports only the proof's TEXT/INTEGER tables, up to 4 MiB and 400 restore
statements. This establishes exact-value recovery for the feasibility dataset;
the separate application backup now provides pagination, fenced write pauses,
retention and restore reconciliation for all ERP data tables. Reads stay
available and protected writes return `503 maintenance` during capture.
See the [full recovery evidence](../docs/CLOUDFLARE_IMPLEMENTATION.md#full-r2-backup-and-isolated-d1-recovery)
and [operator runbook](../docs/CLOUDFLARE_RECOVERY.md). The application proof is:

```sh
uv run python -u scripts/verify_backups_remote.py
```

Resume an interrupted proof with `--resume`; use `--backup-only` when the
configured recovery target already contains a prior accepted recovery. Never
reset that database to rerun a verifier. The script checkpoints fixture/job IDs
and reverses its synthetic source invoice/payment after success.

## Business schema foundation

`migrations/0002_business.sql` and `src/erp_cf/business_schema.json` are generated
from all 45 original modeled tables. They preserve columns, foreign keys,
uniqueness, indexes and decimal scales, replacing PostgreSQL schemas with table
prefixes and exact scaled INTEGER storage. Every table also carries a guarded
revision. Python defaults become D1 defaults where appropriate; repositories
must generate UUIDv7 keys and advance timestamps/revisions.

Regenerate or verify the artifacts using the original API's host dependencies:

```sh
uv run --project ../api python scripts/generate_business_schema.py --check
```

Run without `--check` after intentional model changes and review the generated
SQL. Once a migration is applied beyond isolated feasibility, create a new
migration for later schema changes rather than rewriting its history.

`fixtures/0001_feasibility.sql` is excluded from application migrations. It must
never be applied to the final ERP database. The full business schema has been
created in the isolated `erp-staging-20261113` database; authentication,
management, accounting, orders/quotations, inventory, invoicing/payments, HR,
reporting and background/AI APIs now use it. Sixteen application migrations
are recorded there. Full recovery acceptance, the demo seed and complete deployment/acceptance
remain unfinished.
