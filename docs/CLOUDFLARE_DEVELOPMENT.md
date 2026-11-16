# Developing the Cloudflare ERP

The migrated application runs Python/FastAPI/Pydantic in Workers with native D1
repositories and atomic SQL batches. React/Vite remains the browser application
and is served by Workers Static Assets on the same origin. The current public
`erp.aliahad.com` deployment remains on its original runtime until cutover.

Command routing and database placement are part of deployment configuration.
See [capacity measurements and the quiesced coordinator transition](CLOUDFLARE_CAPACITY.md).
An existing Durable Object does not move when a location hint changes. The
verification environment uses generation `apac-v1` with an `apac` hint. Native
analytics found the demo's existing command object already in SIN, so the demo
keeps its existing route and now supplies an `apac` creation hint too. Every HTTP/job/
proof command uses the shared route helper. Never change generation per request
or run multiple generations as write shards against this database.

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

Operational assessment, thresholds, native Monitor queries and deployed demo
policy IDs are documented in [the operations guide](CLOUDFLARE_OPERATIONS.md).
Monitoring is disabled during demo initialization and enabled afterward. Native
Monitor delivery has been activated and tested for the demo; the query renderer
itself remains read-only.

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

## Direct CLI deployment and quality checks

Deployment uses the authenticated Cloudflare CLI directly. GitHub hosts the
published migration branch and can run optional quality checks; it is not a
runtime or deployment dependency. The user chose direct CLI deployment after
reviewing the optional GitHub integration on 15 November. Its unsaved repository
permission change was cancelled, and the demo's Workers Builds configuration
was removed. Native readback confirms the build configuration is absent while
the current demo deployment remains at 100%.

From `cloudflare/`, build/check and deploy the same mode together:

```sh
CF_MODE=demo-staging pnpm quality
pnpm deploy:demo-staging
```

For the separate financial verification environment:

```sh
CF_MODE=app-staging pnpm quality
pnpm deploy:app-staging
```

The deployment scripts call the pinned `cf deploy --prebuilt` with the explicit
mode and its ignored local secrets file. Complete one environment's build and
deployment before building another: Build Output holds the latest build. Keep
local secrets at mode 0600 and preserve the command hash key across deployments.
Migrations and seeding remain separate explicit steps. Production configuration
and cutover remain outstanding; these commands select only staging environments.

The optional `.github/workflows/cloudflare.yml` quality workflow uses no
Cloudflare credentials and performs no deployment, migration or seeding. The
published verifier at `e5c02ee` passed the complete hosted gate with 336 backend
and 39 frontend tests:
[run 43691642662](https://github.com/aliahadmd/sme-erp/actions/runs/43691642662).

Direct CLI deployments subsequently published verification code `87f9a778643f`
and `49630bebfc88` on 16 November. The latter is serving at 100% as version
`e81ce0eb-0c2b-4c7d-b09c-724400575572`, following the full local gate (347 backend,
39 frontend tests). Its native linked-trade proof passes all commands, reports
and exact cleanup, but still fails the unchanged latency targets; see
[capacity evidence](CLOUDFLARE_CAPACITY.md). No GitHub deployment permission is
required for this procedure.

### Earlier hosted-build proof

Before the direct-CLI choice, a Cloudflare hosted preflight exposed missing
SQLite support in the build image's asdf Python. Requiring the pinned uv's
managed Python fixed host tests. Native build
`dc43abf0-85ce-40df-825f-96785cafcdcb` passed all then-current checks and invoked
`cf` to deploy published commit `5787c401917d197a86b3c80b43e1e2be8b819432` as demo
version `566fc464-d983-4f21-ba76-5311c9f44aa0` at 07:10:19 UTC on 15 November.
Native metadata records the full commit and tag `git-5787c401917d`. Existing
login/permissions, exact dashboard/trial balance, healthy assessment and closed
bootstrap/diagnostics passed afterward. Its owned session was signed out and
revoked. This was a manual hosted build, not a GitHub push-triggered deployment.
That optional build configuration is now removed; reconnecting GitHub is not an
outstanding migration requirement.

See [implementation evidence](CLOUDFLARE_IMPLEMENTATION.md) and the
[backup/recovery runbook](CLOUDFLARE_RECOVERY.md) for current acceptance and limits.
