# Python Workers migration

This directory contains the isolated migration feasibility application. The
existing ERP API and public deployment still use the original runtime.
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
uv sync --frozen --python 3.13
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
Bearer token. The configuration rejects any mode other than `staging`.

`uv.lock` resolves host development dependencies; `pylock.toml` pins the
Pyodide-compatible runtime wheels and hashes. `pnpm vendor` installs that
existing target lock. Change it deliberately with
`uv run python scripts/vendor.py --update-lock` and review the resulting diff.
No host-native extension belongs in `python_modules/`.

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
created in the isolated `erp-staging-20261113` database; application handlers
and demo seed data still need porting before this database can serve the ERP.
