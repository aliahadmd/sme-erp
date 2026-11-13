# Cloudflare D1 feasibility for this ERP

Research date: 12 November 2026. Scope at research time: documentation and source inspection only; no Cloudflare resources, application code, or production data changed during that investigation. Subsequent authorized implementation is tracked separately in the [implementation log](../CLOUDFLARE_IMPLEMENTATION.md); this file records the architectural findings and remaining feasibility requirements.

## Recommendation

**D1 is a conditional fit for the requested all-Cloudflare target, and requires a database-access and transaction redesign.** Preserve the modular monolith, put all related business modules for this organization in one D1 database, retain exact financial arithmetic, and prove the financial command path on real D1 before undertaking the full port. Do not describe this as swapping a connection string.

The main uncertainty is not whether D1 supports SQL transactions: it does support atomic batches. It is whether the ERP's existing read/validate/calculate/write transactions can be compiled into those batches while retaining their race, failure, precision, and recovery behavior.

The user has clarified that a fresh demo database is acceptable. The proposed initial cutover can therefore create the final D1 schema and seed representative demo data; PostgreSQL production-data ETL and preserving legacy password hashes are not initial requirements. Exact-value storage and backup recovery still need proof because the demo will generate new financial records.

The implementation retains Python/FastAPI/Pydantic and uses typed native D1 repositories for application reads and writes, with native batches owning atomic commands. Synchronous SQLAlchemy with `sqlalchemy-cloudflare-d1` remains an optional candidate in the separate proof. That community adapter does not replace the native D1 batch design below; its binding connection's commit/rollback methods are no-ops. See the [services research](cloudflare-workers-services.md#orm-candidate-and-its-transaction-boundary) for the inspected release and runtime validation requirements, and the [implementation log](../CLOUDFLARE_IMPLEMENTATION.md) for current evidence.

## Existing requirements found in the repository

- `api/app/shared/events.py:5` requires the business document and its accounting entry to commit or roll back together. Notification/email side effects follow the commit.
- `api/app/shared/order_flow.py:39`, `api/app/modules/inventory/service.py:70`, and `api/app/modules/invoicing/service.py:275` use row locks to protect order progress, stock, and payment allocation.
- `api/app/shared/money.py:13` uses `NUMERIC(18,2)`, `(18,4)`, and `(18,6)` with Decimal and half-even rounding; FX columns use scale 8.
- `api/app/shared/numbering.py:16` uses an atomic sequence update and a returned value.
- The current model also uses PostgreSQL schemas, JSONB, UUIDs, and PostgreSQL reporting expressions. These must be translated, not imported unchanged.

These are source observations, not newly run acceptance tests.

## What D1 guarantees, and what it does not

Cloudflare documents `db.batch([...preparedStatements])` as a transaction: statements execute sequentially, and a statement error aborts/rolls back the whole batch. The documented Worker API exposes prepared statements and batch execution; it does not expose an application callback transaction that can remain open while arbitrary Worker code awaits intermediate query results. D1 Sessions add sequential consistency, not that transaction primitive. Plan around the documented batch API instead of assuming an ORM's PostgreSQL-style `transaction(async tx => ...)` will work. [D1 database binding API](https://developers.cloudflare.com/d1/worker-api/d1-database/)

Cloudflare's import instructions require removing explicit `BEGIN TRANSACTION` and `COMMIT` from a SQLite dump. PostgreSQL dumps are incompatible with D1 SQL/types. [Import/export compatibility](https://developers.cloudflare.com/d1/best-practices/import-export-data/)

**Important inference:** reads made before a batch are not protected merely because the later writes are transactional. Every fact that can affect the write must be guarded inside the same database transaction, or every writer must follow a proven serialization protocol plus database-enforced safety checks.

**Zero-row update trap:** SQLite considers an `UPDATE ... WHERE ...` that matches no rows successful. If an optimistic update loses a race and changes zero rows, D1 does not automatically roll back subsequent inserts in its batch. Checking `meta.changes` after the batch returns is too late to undo a committed journal entry. The transaction needs an actual SQL constraint/guard failure, or rigorously gated writes with final invariants, rather than a post-commit JavaScript assertion. [SQLite UPDATE semantics](https://www.sqlite.org/lang_update.html)

Proposed pattern, requiring a real-D1 proof:

1. Read authoritative state, validate the command, calculate exact amounts, and build a write plan.
2. Include expected revision predicates, stock/payment bounds, document state checks, and an idempotency record in one D1 batch.
3. Make a failed precondition cause a real SQL error within that batch. Explicitly test missing rows and zero-row updates; avoid `OR IGNORE`/`DO NOTHING` hiding a rejected precondition.
4. Commit document changes, stock/progress, journal entry/lines, audit records, and an outbox record together.
5. Dispatch the outbox separately; retries look up the same command identity rather than post again.

This is an architectural proposal, not a platform-provided financial transaction abstraction.

## Durable Object coordinator: useful, but not the database transaction

For the current single-organization workload, one command coordinator per organization is a reasonable simplifying candidate. Route every financial writer through it, including API actions, scheduled jobs, imports, and repair tools. Do not create one object per document if commands can touch shared stock, the same invoice, or common numbering state.

Cloudflare documents `blockConcurrencyWhile` for external asynchronous calls where event-loop interleaving must be prevented. It has a 30-second timeout and resets the object if the callback throws or times out. Keep the critical section short; return expected conflicts as values/caught errors rather than reset the object. Simply placing an `await D1...` call inside a Durable Object does not assert that the entire command is serialized. [Durable Object state and concurrency](https://developers.cloudflare.com/durable-objects/api/state/)

The object’s local storage transactions govern its attached storage; they are not a distributed transaction over D1, Queues, and R2. D1 remains the single source of truth, and the complete financial write must still be one guarded D1 batch. The coordinator should not hold a second authoritative financial ledger. [Durable Object SQLite storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)

Test object restart, timeout, and a D1 commit whose response is lost. A client timeout does not imply rollback: Cloudflare explicitly notes that racing a D1 call against a JavaScript timer does not cancel the query. [D1 timeout behavior](https://developers.cloudflare.com/d1/observability/debug-d1/)

## Exact money and full existing precision

D1 stores signed 64-bit integers internally, but the JavaScript binding currently does not support BigInt values and returns integers as JavaScript Numbers. Values beyond `Number.MAX_SAFE_INTEGER` cannot safely use the default mapping. The ERP's 18-digit scaled values can exceed that threshold. [D1 binding type conversion](https://developers.cloudflare.com/d1/worker-api/#type-conversion)

SQLite's `NUMERIC` affinity is not PostgreSQL's exact decimal type; it can store floating-point values. Use integer storage with explicit scale rather than declaring `DECIMAL(18,2)` and assuming equivalent behavior. [SQLite storage classes and affinity](https://www.sqlite.org/datatype3.html)

**Recommended adapter design, still requiring live-D1 validation:**

- In the preferred Python path, retain Decimal calculations and parse scaled coefficients directly into Python integers; a TypeScript alternative would use BigInt. Never pass exact amounts through float/Number conversions or an unverified ORM numeric mapper.
- Keep money, quantities, prices, percentages, and FX rates as scaled signed-64-bit `INTEGER` columns. Any valid `NUMERIC(18,s)` coefficient fits signed 64-bit storage.
- Bind the scaled coefficient as a canonical decimal **string**, use `CAST(? AS INTEGER)` in SQL, and project every financial integer as text (`CAST(column AS TEXT)`) when reading or returning values. Preserve decimal strings in the external API.
- Check original precision, scale, and signed-64-bit bounds before the SQL cast; an out-of-range SQLite integer cast can clamp instead of fail.
- Perform multiplication, division, moving-average costing, FX conversion, and half-even rounding in Python Decimal/exact integer arithmetic with sufficient precision; use exact BigInt/decimal arithmetic if a TypeScript alternative is chosen. Ordinary SQLite arithmetic may promote integer overflow to floating point. [SQLite expressions and casts](https://www.sqlite.org/lang_expr.html)
- Treat aggregate range separately: `SUM` of integers can overflow signed 64-bit even when every row is valid. Use bounded rollups or paged exact application accumulation when needed; `TOTAL`/floating-point aggregation cannot replace an exact ledger total. `CAST(SUM(...) AS TEXT)` protects result transport, not an overflow inside `SUM`. [SQLite aggregate behavior](https://www.sqlite.org/lang_aggfunc.html)

Python's arbitrary-precision numbers do not by themselves establish exact D1 binding/adapter transport. Keep the string binding and text projection strategy, and prove all precision boundaries through the actual Python Worker and ORM path as well as the direct batch path. [Python interop](https://developers.cloudflare.com/workers/languages/python/ffi/)

An alternative is enforcing smaller safe-integer business limits, but this would narrow the existing data contract and needs an explicit product decision and data preflight. It should not be introduced silently.

## Read consistency

D1 read replication is documented as Beta. Replicas are asynchronous; writes go to the primary. Sessions/bookmarks guarantee sequential consistency and can carry read-your-writes progress across requests. For authorization decisions and financial command reads, use a primary-start session or the direct primary path, and retain revision checks inside writes. Dashboard/report replicas can be considered later. Starting with replication disabled removes an unnecessary migration variable. [Read replication and consistency](https://developers.cloudflare.com/d1/best-practices/read-replication/)

## Capacity, cost, and schema implications

Current documented paid limits: 10 GB per database (not increaseable), 1 TB account storage, 30-second SQL execution, 100 bound parameters per statement, 100 KB SQL statements, 2 MB rows/strings/blobs, and 50 MB query results. The result limit took effect 4 November 2026. A database instance is single-threaded; long reports and writes compete for its capacity. Read replicas do not make the primary a multi-writer database. Batch calls count as one query against the per-invocation request limit, although each statement still has its own limits. [D1 limits](https://developers.cloudflare.com/d1/platform/limits/)

Paid D1 includes 25 billion rows read/month, 50 million rows written/month, and 5 GB storage; overages are $0.001/million rows read, $1/million rows written, and $0.75/GB-month. Index maintenance also contributes written rows. The first 100,000 databases are included. These are D1 charges, separate from Workers, Durable Objects, Queues, R2, and other services. Estimate from measured ERP command/read counts rather than user count alone. [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)

Do not split sales, inventory, invoicing, and accounting into separate D1 databases to imitate PostgreSQL schemas: that removes the single database transaction boundary. Use table prefixes to retain module ownership and one database for this organization. Future company-per-database partitioning is plausible; arbitrary financial sharding within one company is a new architecture problem.

Rewrite JSONB as validated JSON text/normalized tables, UUIDv7 as text or a carefully specified binary representation, and dates/timestamps as explicit ISO text/epochs. D1 supplies JSON functions and FTS5, but not PostgreSQL's server, schema namespaces, or pgvector extension. [Supported D1 SQL/extensions](https://developers.cloudflare.com/d1/sql-api/sql-statements/)

## Migration, integrity, and recovery

D1 foreign keys are enforced by default. Use ordered table creation and carefully bounded migrations. `PRAGMA defer_foreign_keys=on` can defer validation within a transaction; current documentation warns that setting it back to `off` discards outstanding violations rather than checking them. Use `PRAGMA foreign_key_check` and allow end-of-transaction validation. [D1 foreign keys](https://developers.cloudflare.com/d1/sql-api/foreign-keys/)

D1 migrations are versioned SQL files; port the final schema and data semantics rather than replaying PostgreSQL Alembic scripts verbatim. [D1 migrations](https://developers.cloudflare.com/d1/reference/migrations/)

Time Travel is always on and provides point-in-time recovery. Paid history is 30 days (Free is 7 days). Restore it as part of a coordinated ERP recovery procedure that also pauses side effects and invalidates stale sessions; restoring D1 does not retract an already sent email or restore R2 objects. [Time Travel](https://developers.cloudflare.com/d1/reference/time-travel/), [plan history limits](https://developers.cloudflare.com/d1/platform/limits/)

**Recovery caveat:** the native export documentation warns about large-int precision and states that exports block other database requests; virtual-table databases also have export restrictions. Do not assume default exports preserve full financial precision. Require a backup/restore proof with 18-digit coefficients. If necessary, implement a logical export using text projections during a consistent, quiesced window and restore through the validated integer-string adapter. Store exact backups/manifests in R2, rehearse recovery, and avoid adding FTS virtual tables to the financial database until backup compatibility is resolved. [Export limitations](https://developers.cloudflare.com/d1/best-practices/import-export-data/#known-limitations)

## Acceptance gates before committing to the full rewrite

1. Prove precision round trips and export/restore at 18 digits, all scales, negatives, half-even ties, extreme FX, and aggregate overflow boundaries.
2. Post/void one complete buy and sell cycle with exactly balanced journals, stock valuation, credit settlement, and source-order progress.
3. Race two deliveries against insufficient stock; race allocations, quotation conversions, invoice posts, and credit caps. Every losing command must leave all related tables unchanged.
4. Inject a failure after each planned statement; document, journal, audit, and outbox must be all present or all absent.
5. Retry identical command IDs before/after response loss and object restart; produce exactly one financial outcome.
6. Measure busiest-company command latency/throughput, report contention, storage growth, and backup duration on realistic data. The 10 GB hard cap includes tables and indexes, so set a headroom policy well before it is reached.

These gates were not executed during the original research. Subsequent isolated
implementation has verified exact transport, guarded rollback, competing stock
commands, replay, a complete buy/sell/pay and reversal cycle, consistent paged
reporting and a bounded eight-table recovery proof. Full-system workflow parity,
restarts/network loss and realistic capacity/recovery remain open; see the
[current evidence](../CLOUDFLARE_IMPLEMENTATION.md). Passing the complete gates is
still required for this particular ERP.
