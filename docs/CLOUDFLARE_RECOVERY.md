# Cloudflare backup and recovery operations

This procedure applies to the Python Worker/D1 application. Production still
uses the original runtime until cutover is accepted. Staging resources are:

| Resource | Isolated staging value |
|---|---|
| Application | `erp-migration-staging-20261113` |
| Source D1 | `0f278204-3190-49e1-96d2-a9312fa57e3d` |
| Private backup bucket | `erp-staging-backups-20261113` |
| Recovery D1 | `d7bb688c-8009-4eb0-a21f-993310c23fcc` |

The bucket's managed public domain is disabled. Application routes expose job
metadata and archive IDs; they do not expose the snapshot contents. Backups
contain sensitive application data, including password hashes and session
records, and must remain private. Runtime access uses bindings rather than an
account API token.

The separate coherent demo has its first committed private R2 baseline:
`01a1da6a-5839-76e7-b180-7b49a4fdbc35`, captured on 14 November 2026. It contains
1,558 rows in 77 pages (460,502 bytes) across all 62 captured data tables and
completed in approximately 4 minutes 50 seconds. The archive is discoverable,
the write pause is released, and the requester has signed out. The ignored
checkpoint is `cloudflare/.cloudflare/verification/demo-baseline-backup.json`.
This is capture/publication evidence; the full restore proof below used the
separate verification application's database. The demo still has restoration
disabled and no recovery-database binding.

## Capture and publication

An authenticated superuser with settings access requests a backup using
`POST /api/backups`. An idempotency key returns the same request receipt.
Inspect `GET /api/backups/{id}` or the paginated `GET /api/backups` list.

| State | Meaning |
|---|---|
| `requested` | Waiting to acquire the database-wide write pause |
| `capturing` | Writes are paused; bounded, exact pages are being copied to R2 |
| `publishing` | The consistent snapshot is sealed in D1; writers are released |
| `complete` | Manifest, commit marker and discovery index are published |
| `failed` | No completed backup is promised; inspect the error and request a new run |
| `deleting` / `deleted` | Retention is removing or has removed the archive |

The controller uses a 300-second lease with durable progress checkpoints. SQL
triggers pause business, authentication and background writes across Workers;
reads remain available. Writes return `503 maintenance`. The controller checks
the configured total pause budget (`BACKUP_MAX_PAUSE_SECONDS`, default 600).
Lease expiry independently reopens writes if the controller disappears.

A resumed controller must prove the same owner, fence and unexpired lease.
An expired capture cannot continue its old pages into a valid snapshot. It is
failed and needs a new request. A superuser can cancel an unsealed request with
`POST /api/backups/{id}/cancel`; this releases its pause and records an audit.

Each data page projects INTEGER values as text and restores them with explicit
integer casts. The format preserves row IDs, schema, indexes, triggers and
migration receipts. Pages are bounded before crossing the Worker boundary and
addressed by their SHA-256 hash. Only ephemeral assertions and backup/recovery
control data are excluded from captured rows. Those exclusions are explicit in
the manifest.

A manifest file alone is provisional. Recovery requires both the manifest and
its matching `COMMITTED.json` marker. The marker is written only after D1 has
atomically sealed the capture and released the pause. A failed marker/index
write can be retried without recapturing data or pausing writers again.

## Scheduling and retention

The existing minute Cron advances bounded backup/recovery work. Automatic daily
requests use `BACKUP_SCHEDULE_ENABLED`, `BACKUP_HOUR_LOCAL` (default 2), and the
explicit business offset. Staging uses UTC+08:00. Daily keys prevent repeated
requests for the same business date. An administrator can also advance bounded
work with `POST /api/backups/advance`.

The default retention is 35 days (`BACKUP_RETENTION_DAYS`). Recovery in progress
protects its source archive from deletion. Retention first removes discoverable
commit markers, then deletes the archive prefix through paginated R2 listings.
Deletion state survives restarts. Completed/failed job metadata remains available
after its page ledger and R2 objects are removed.

Confirm the deployed schedule flag and job results rather than assuming a local
configuration change has enabled automatic capture. Failed jobs and overdue
backups need operational alerts; these are part of production acceptance.

## Isolated restore

1. Prepare an **empty, isolated D1 database**, bind it as `RESTORE_DB`, and set
   `RESTORE_DATABASE_ID` to that resource's ID. Set `RESTORE_ENABLED=true` only
   in the environment intended to operate that target. Keep it off public
   application traffic and ordinary Queue/Cron consumers during recovery.
2. Choose a completed backup. `GET /api/backup-archive` lists the private R2
   discovery index. If source job metadata was lost, an authorized superuser can
   rebuild it with `POST /api/backup-archive/{id}/import`. The importer validates
   the marker, manifest identity/hash, organization and page namespace. It does
   not need the original source job row to exist.
3. Request `POST /api/backups/{id}/restore` and follow
   `GET /api/recoveries/{id}`. Requests record their target ID; a binding change
   during a run fails instead of redirecting subsequent pages. An occupied
   target is rejected. The service never resets a target database.
4. Target receipts make every DDL/data page resumable after a lost response.
   Tables load in dependency order. Nullable self references are repaired in a
   second pass. Every captured page hash, table row count, schema definition and
   foreign key is checked before operational reconciliation. Immutability
   triggers are installed after original data is complete.
5. A completed restore revokes captured sessions and establishes an external
   recovery hold. Pending/rejected/uncertain mail becomes uncertain for review;
   old mail-delivery events cannot automatically resend it. Other restored
   pending events carry a sticky recovery flag. They may rebuild in-app notices,
   but their mail remains held. Old overdue reminders also require review.
   Workers AI is held before quota/provider calls while the recovery policy is
   active.
6. Review journals, balances, stock, credits, numbering, open documents and job
   state before any traffic switch. Reconcile external effects against provider
   receipts/logs, including effects delivered after the backup point. Do not
   assume that reverting D1 retracts email or external actions.
7. Keep `EMAIL_MODE=test`/`disabled`, `AI_ENABLED=false` and ordinary jobs disabled
   while doing recovery acceptance. The recovery hold is not automatically
   cleared. An operator must explicitly review its policy row and deliberately
   enable effects afterward. Mail retries require the existing audited
   reconciliation flow and acknowledgment of possible duplicate delivery.
8. Start the restored application with matching code/schema, require fresh sign-in,
   and verify its browser/role workflows before changing live routing. Preserve
   the previous deployment and backup during the rollback window.

The current application has no managed attachment-upload store. Snapshot data
includes image/file references but does not download external URLs. Adding a
managed R2 file store requires immutable object versions, matching retention and
reconciliation alongside D1 snapshots.

## Verification and limits

Run `uv run python -u scripts/verify_backups_remote.py` from `cloudflare/` for the
isolated staging proof. It creates an exact 18-digit invoice/payment fixture,
requests a full backup, restores it into the configured empty target, and then
reverses its source fixture. It prints no credentials. Its ignored checkpoint
supports `--resume`; inspect that checkpoint before rerunning after a failure.
Use `--backup-only` when the recovery target is already occupied by a prior
accepted proof. A completed proof does not make an occupied target reusable.

The separate `scripts/verify_backup_operations_remote.py` supports restricted
staging failure probes. `--prepare-expiry` creates and checkpoints one backup
ID. After that run has captured pages, an operator can expire only its matching
owner/fence in `platform_maintenance`; `--check-expiry` verifies failure without
publication and successful fresh authentication. To test retention without
waiting 35 days, age only that failed probe's control timestamp, with no restore
references, and use `--check-retention` after scheduled cleanup. These are
deliberate staging fault injections, never routine production commands.

`--import ID` verifies private archive discovery and repeat import. For a real
lost-metadata proof, first confirm that the selected staging archive has no
restore references, save its metadata/checksum, and remove only its
`platform_backup_pages` and `platform_backup_runs` control rows. Keep the R2
archive intact. The import must rebuild the same manifest hash, row count and
page ledger. Never use the occupied accepted recovery database for this probe.

The format supports ordinary rowid tables with TEXT/INTEGER columns and the
current acyclic cross-table dependencies. Unsupported schema shapes fail
explicitly. The current bounds are 20,000 pages, 16 MiB per object and an 8 MiB
manifest. Capacity and maximum pause duration must be measured for real data;
passing a small recovery proof does not establish production-scale capacity.

D1 Time Travel supplements these archives. Code rollback does not restore D1,
and restoring D1 does not atomically restore other services. R2 and D1 remain in
the same provider/account failure domain under the requested all-Cloudflare
architecture. See the implementation log for current native evidence and open
acceptance work.
