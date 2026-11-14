# Developing the Cloudflare ERP

The migrated application runs Python/FastAPI/Pydantic in Workers with native D1
repositories and atomic SQL batches. React/Vite remains the browser application
and is served by Workers Static Assets on the same origin. The current public
`erp.aliahad.com` deployment remains on its original runtime until cutover.

## Environments

| Mode | Worker | Purpose |
|---|---|---|
| `staging` | `erp-feasibility` | Isolated runtime/precision/SQLAlchemy adapter proof |
| `app-staging` | `erp-migration-staging-20261113` | Full-schema financial, failure, Queue and recovery verification |
| `demo-staging` | `erp-demo-staging-20261114` | Fresh, coherent ERP demo and role acceptance |

The demo owns D1 `71ee20e1-b6de-4f2a-8f8f-4a5ecd070225`, private R2 bucket
`erp-demo-staging-backups-20261114`, independent event/dead-letter queues and
Durable Object namespaces. Its organization is
`01a1d6bf-eca3-7654-b225-ae37c723ed96`. Restoration is disabled and no recovery
database is bound to the demo. Use an explicitly isolated target for recovery.

## Build and check

From `cloudflare/`, use Node 24, pnpm 12.8.1 and uv with Python 3.14:

```sh
pnpm install --frozen-lockfile
pnpm quality
```

The shared quality command installs frozen host/frontend dependencies, runs
backend tests/lint/formatting and generated-schema checks, runs frontend tests
and lint, installs the committed Pyodide target lock, and compiles the SPA and
Worker into Build Output. It makes no Cloudflare resource changes. The default
mode is `demo-staging`; `CF_MODE=app-staging pnpm quality` selects verification.
Quality builds refuse the temporary demo-bootstrap flag.

The host-only `uuid-utils` development dependency lets schema generation inspect
the original model definitions. It is excluded from the Worker target lock.
Do not vendor host-native extensions into the Python Worker.

The pinned `cf` CLI needs the committed Python module-name patch and custom
Build Output bridge. See `cloudflare/README.md` before upgrading it. The Worker
does not start uvicorn or use PostgreSQL/Redis/S3 connection strings.

## Where functionality belongs

- `web/src/features/` owns the existing UI and typed API clients. Retain decimal
  strings, current permissions and lifecycle states when implementing Figma work.
- `api/app/modules/*/schemas.py` supplies the shared Pydantic contracts. Portable
  rules under `api/app/shared/` remain shared. The original SQLAlchemy services
  are the legacy runtime/reference, rather than the new persistence layer.
- `cloudflare/src/erp_cf/` owns the Worker API, business command planners,
  repositories, jobs and operational services. Register routes in `app.py` and
  commands in the owning command registry.
- Mutations go through `ERPCommands` and a `UnitOfWork`. Plan business changes,
  revision/permission guards, journals, numbering, audit, receipt and outbox in
  one native D1 batch. A Durable Object mutex does not replace SQL-enforced
  atomicity or durable idempotency.
- Reads use typed repositories and explicit primary-backed authorization.
  Reports validate the business commit epoch across their pages; every new
  business writer must maintain that epoch.
- Money uses Decimal/exact integers. D1 INTEGER transport uses integer strings
  and explicit casts; API decimals remain strings. Preserve financial scales,
  rounding, FX conventions and reversal snapshots.
- External mail, AI, Queue and R2 I/O belong outside the financial coordinator.
  Use the atomic outbox for post-commit effects and tolerate duplicate delivery.
- Wrap new pages in `RequirePermission` before mounting their queries. Section
  tabs/defaults live in `web/src/app/section-tabs.ts`; keep sidebar eligibility
  consistent. Gate each action with its server command's permission, including
  separate post, void, archive and create permissions.
- Gate form reference queries by both permission and whether the form is open.
  Preserve existing IDs when hiding inaccessible lookup controls. Keep the
  identity-scoped `SessionQueries` boundary inside `AuthProvider` so a later
  account cannot reuse the previous account's cache or page state.

Add versioned D1 SQL migrations for schema changes. Applied migration files are
immutable. New tables need the verified insert/update/delete maintenance guards
so full backups can pause every writer. Update desired model metadata and the
generator deliberately when evolving the original model baseline; never rewrite
applied `0002_business.sql` to make a schema check pass.

## Migrations and fresh-demo seeding

`scripts/apply_migrations.py` selects only the configured isolated databases.
Its default is a read-only plan:

```sh
uv run python scripts/apply_migrations.py --mode demo-staging
uv run python scripts/apply_migrations.py --mode demo-staging --apply
```

Every DDL/data migration and SHA-256 receipt commit in the same D1 batch. Changed,
missing or noncontiguous history is rejected. An uncertain command result is
checked against its receipt before continuing. Never reset a target to rerun a
verifier or regenerate already applied SQL.

The private `.secrets.demo-staging.json` contains `JWT_SECRET`,
`COMMAND_HASH_KEY` and `BOOTSTRAP_TOKEN`. The private `.demo.demo-staging.json`
contains the administrator and nine role credentials. Keep these ignored files
at mode 0600. Preserve the command hash key across deployments.

For a genuinely new, empty demo target, the initialization deployment is:

```sh
ERP_DEMO_BOOTSTRAP=true pnpm build:demo-staging
ERP_DEMO_BOOTSTRAP=true pnpm deploy:demo-staging
uv run python -u scripts/seed_demo_remote.py --bootstrap
pnpm quality
pnpm deploy:demo-staging
uv run python -u scripts/seed_demo_remote.py
uv run python -u scripts/verify_demo_roles_remote.py
```

The temporary flag disables ordinary jobs/scheduled capture during seeding.
The final build closes bootstrap and enables jobs/daily backups. The seed's
ignored checkpoint preserves its date, step results and mutation keys. Re-running
it resumes the same commands and verifies live balances; it does not reset data.
The native role verifier checkpoints completed roles and supports `--resume`.
Both remote demo scripts accept `--ipv4` for a broken local IPv6 path while
retaining normal certificate verification.
After users change demo data, review the seed acceptance baseline before trying
to apply it again.

For native browser checks, open a Playwright CLI session named `erp-roles` at
the fresh demo's `/login` page, then run:

```sh
uv run python scripts/verify_browser_session_remote.py --scenario roles
uv run python scripts/verify_browser_session_remote.py
uv run python scripts/verify_browser_session_remote.py --scenario stale-response
```

The first command walks all nine active profiles, checks navigation/access
controls and opens/closes authorized forms without submitting them. Use
`--scenario roles --role warehouse` to narrow a diagnostic run. The default
scenario switches from administrator to employee in the same live application
and verifies that only the employee's leave is rendered. The stale-response
scenario holds an old HR read across the same switch, releases it as 401 and
requires no refresh/retry under the later account; it submits no financial
command. All scenarios sign out their actors. The launcher pins the demo origin
before filling credentials, reads the ignored demo credentials, creates a
temporary 0600 callback, captures/redacts CLI source echo, deletes that callback,
and writes non-credential evidence under `.cloudflare/verification/role-browser/`.
Evidence includes the asset name, timestamps and running/complete/failed state.
Keep the browser's normal TLS verification enabled. A passing navigation/form
check does not prove every financial or HR submission workflow.

Financial response-loss checks use the separate verification Worker and keep
the coherent demo unchanged. With `erp-financial` open at that Worker's `/login`:

```sh
ERP_RESTART_PROOF=true CF_MODE=app-staging pnpm quality
ERP_RESTART_PROOF=true pnpm deploy:app-staging
uv run python scripts/verify_browser_payment_remote.py --prepare --restart
uv run python scripts/verify_browser_payment_remote.py --browser
pnpm build:app-staging
pnpm deploy:app-staging
```

The probe creates a disposable customer, submits a real `12.34` payment, resets
the coordinator after its commit, withholds the response, and retries through
the UI. It checks the unchanged key/payload, changed coordinator instance,
native single payment/posting, one reversal and restored account balances.
Cleanup voids only that payment and archives only its fixture contact. If the
process is interrupted, inspect its live process/browser first, then use
`--cleanup` when that process is terminal. Cleanup reads the known command receipt
and never creates a payment to discover its ID. Start another run with
`--new --prepare --restart` only after the previous checkpoint is cleaned.

The final two commands close the temporary diagnostic flag; verify the hidden
coordinator endpoints return 404 afterward. The flag is unavailable on the demo
and future production origins. `CommandIntent` currently protects the payment/
refund form's unresolved submissions. Manual journal entries use the same
controller. The adjustment form retains separate create/post commands and its
acknowledged draft ID, so a posting failure never starts another draft.

The separate financial verifier covers those forms without enabling a restart
probe. Open `erp-financial` at the verification Worker's `/login`, then run:

```sh
uv run python scripts/verify_browser_financial_remote.py --prepare --scenario manual
uv run python scripts/verify_browser_financial_remote.py --browser
uv run python scripts/verify_browser_financial_remote.py --new --prepare --scenario adjustment-post
uv run python scripts/verify_browser_financial_remote.py --browser
```

Additional scenarios are `adjustment-create` and `adjustment-rejected`. The last
keeps negative-stock protection enabled, rejects the first posting, replenishes
only the disposable product, then retries the same draft. Each run checks native
records and adds an exact compensating entry/adjustment, voids its initial stock
receipt and archives/deactivates its fixture master data. It never deletes
financial records. All account balances must match the pre-fixture baseline.
Its private `financial-retry.json` checkpoint supports `--cleanup` after the
original process is terminal, using the known create receipt. `--new` refuses
an uncleaned checkpoint. A CLI/transport interruption records an unknown browser
outcome and leaves cleanup pending; inspect the original browser actions before
compensating or starting another run. Other forms and recovery across page
reloads still need acceptance before complete browser recovery is claimed.

## CI and Workers Builds

`.github/workflows/cloudflare.yml` runs `pnpm quality` without database services,
Cloudflare credentials, deployment or migrations. Action commits, Node/pnpm and
uv versions are pinned. The legacy workflow remains a reference-runtime check
until retirement is accepted. Hosted execution of the new workflow still needs
verification after publication.

Workers Builds can run the same gate with repository root directory
`cloudflare`, build command `pnpm install --frozen-lockfile && pnpm quality`,
and deploy command `uv run python scripts/deploy_from_env.py --deploy`.
Use build variables `NODE_VERSION=24`, `PYTHON_VERSION=3.14`,
`PNPM_VERSION=12.8.1`, `SKIP_DEPENDENCY_INSTALL=1`, and `CF_MODE=demo-staging`.
These settings are prepared; no automatic repository deployment is enabled by
these local files alone.

Store the existing runtime values as secret build variables `ERP_JWT_SECRET`
and `ERP_COMMAND_HASH_KEY`, plus a scoped Cloudflare deployment credential.
The deploy wrapper verifies the exact prebuilt target and closed-bootstrap
state, writes runtime secrets into a temporary 0600 file for `cf`, and removes
that file afterward. Without `--deploy`, it only validates. Missing secrets fail
closed; no secret is a Vite variable or a public asset. Migrations and seeding
remain explicit operator steps, separate from code deployment.

Primary tooling references: [Workers build image](https://developers.cloudflare.com/workers/ci-cd/builds/build-image/),
[Workers build settings](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/),
[checkout](https://github.com/actions/checkout), [setup-node](https://github.com/actions/setup-node),
[setup-uv](https://github.com/astral-sh/setup-uv).

See [implementation evidence](CLOUDFLARE_IMPLEMENTATION.md) and the
[backup/recovery runbook](CLOUDFLARE_RECOVERY.md) for current acceptance and limits.
