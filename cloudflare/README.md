# Python Workers migration

This directory contains the isolated financial proof and the ERP application
being ported to Workers. The existing public deployment still uses the
original runtime.
See [implementation status](../docs/CLOUDFLARE_IMPLEMENTATION.md) for the full
migration scope and unfinished work.

The proof uses FastAPI/Pydantic, Python Decimal, synchronous SQLAlchemy with
`sqlalchemy-cloudflare-d1`, and native D1 batches. The adapter's `commit()` and
`rollback()` do not provide atomic transactions. Financial commands always use
the native batch path with SQL assertions and durable idempotency.

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

## Application authentication

The application Worker is separate from the proof and uses the full business
schema in `erp-staging-20261113`:

```sh
pnpm build:app-staging
pnpm deploy:app-staging
uv run python -u scripts/verify_auth_remote.py
```

`.secrets.app-staging.json` provides independent `JWT_SECRET` and
`BOOTSTRAP_TOKEN` values. The ignored `.demo.app-staging.json` holds the fresh
demo's `email`, `password` and `full_name`. Keep both private. On a new, empty
application database, add `--bootstrap` to the verifier to initialize the
organization, branch, administrator, 84 permissions and seven system roles.
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

Only authentication and bootstrap have been ported so far. Core management,
business module handlers, jobs, frontend assets and the full demo seed remain
in progress. This Worker is not yet the complete ERP.

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
the production backup still needs pagination, write quiescence, retention and
restore reconciliation for all ERP tables.

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
created in the isolated `erp-staging-20261113` database; authentication now
uses it. All business handlers and the full demo seed still need porting.
