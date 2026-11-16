# D1 command and report capacity

This acceptance uses only `erp-migration-staging-20261113`, its existing native
D1 database, and real authenticated HTTP endpoints. The coherent demo and
production data are excluded. The measurements are an initial small-company
workload, not a maximum throughput or long-term data-size guarantee.

## Targets and workload

These provisional targets are recorded before measurement. Each writer is a
closed-loop client: it waits for its response before sending its next command.
Reader clients repeatedly alternate trial balance, a fixture account ledger
and dashboard. All HTTP failures, including safe `report_busy` responses, count
against the zero-error target. Measured requests have no automatic retries.

| Profile | Writers × commands each | Journal lines per command | Report readers | Per-endpoint p95 target |
|---|---:|---:|---:|---:|
| Baseline | 1 × 8 | 2 | 1 | 5 seconds |
| Normal | 4 × 5 | 20 | 2 | 5 seconds |
| Burst | 8 × 3 | 20 | 4 | 10 seconds |
| Large | 1 × 2 | 100 | 2 | 10 seconds |

Percentiles use nearest rank and must be read with the sample count; two large
commands do not establish a dependable population p95. Latencies include the
client's network, HTTP processing and coordinator queueing. Client concurrency
is not an offered requests-per-second rate, and the whole profile includes a
minimum report tail after writers finish. No open-loop saturation test is
implied. Manual journals exercise atomic accounting writes and numbering;
invoice, stock, payment, login and AI workloads require separate evidence.

The verifier preregisters every immutable request and command key in a private,
atomic checkpoint before sending. Cleanup reads durable receipts, compensates
each acknowledged journal with its exact opposite, deactivates fixture accounts,
and checks original balances, unique entries/numbers, foreign keys and empty
assertions. An ambiguous missing receipt requires native proof that the original
session was revoked: the atomic commit checks that session, so a delayed original
request cannot commit afterward. Without that fence cleanup stops. It never
creates an original posting to discover its ID. The immutable test history stays
in staging.

Run from `cloudflare`:

```sh
uv run python scripts/verify_capacity_remote.py --run
# Only after the previous process has ended, reconcile an interrupted run:
uv run python scripts/verify_capacity_remote.py --cleanup
# A further run requires the previous checkpoint to be cleaned:
uv run python scripts/verify_capacity_remote.py --new --run
```

The checkpoint is `.cloudflare/verification/capacity/measurement.json` (0600),
ignored by Git. It contains timings and fixture information, never credentials.

## Linked document workload planned before measurement

The next acceptance uses the existing verification database, including its
compensated journal history, a separate fixture customer/three products and an
admitted empty verification warehouse. Each closed-loop cycle creates and confirms a three-line
sales order, creates/posts its stock delivery, creates/posts its source-linked
invoice, and records a fully allocated `60.00` bank payment: seven real commands.
One initial receipt supplies 20 units of each product at cost `10.00`; each
completed cycle consumes one unit of each. No invoice email is requested.

| Profile | Writers × cycles each | Report readers | Per-command and per-report p95 target |
|---|---:|---:|---:|
| Baseline | 1 × 2 | 1 | 5 seconds |
| Normal | 4 × 2 | 2 | 5 seconds |
| Burst | 8 × 1 | 4 | 10 seconds |

The 18 cycles produce 126 measured commands. Readers alternate trial balance,
fixture stock and dashboard; stock must expose equal quantities for all three
products with exact cost/value, and books must balance at every accepted read.
Every HTTP/transport/validation failure counts against acceptance, including
`report_busy`; measurement has no automatic retries. Percentiles are descriptive
for the recorded sample counts, rather than population-tail guarantees. Cycle
elapsed time is reported separately. This is closed-loop contention, not an
offered-RPS saturation claim.

`scripts/verify_trade_capacity_remote.py --run` writes immutable requests/keys
before transmission to its private `trade-capacity/measurement.json` checkpoint.
It fences the original session and reconciles native receipts before cleanup;
`--cleanup` resumes that cleanup without repeating original requests. Cleanup
voids payments, invoices and deliveries before cancelling orders and voiding
the supply receipt. It checks every original account balance, dashboard, empty
fixture stock, numbering, foreign keys and assertion cleanup, then archives its
customer/products. The first run created warehouse
`01a1de1b-1da8-70e3-802f-dc0c97af2310` (`TCA9884D3B39BB`); later runs require
that same admitted warehouse to be empty and reuse it. The warehouse and
immutable history remain in staging because warehouse removal is not part of
the API. The first run's derived low-stock effect is recorded in its results.

Results will be recorded below after native execution; these targets are not
evidence of a passing result.

## Platform constraints

D1 executes a primary database's queries serially, so expensive report queries
compete with commands. The current application uses primary reads and does not
activate read replication. Replicas require the Sessions API and deliberate
consistency handling; toggling replication alone does not move these reads.
Keep the exact integer report arithmetic and accounting consistency guards
when optimizing. [D1 limits](https://developers.cloudflare.com/d1/platform/limits/),
[D1 read replication](https://developers.cloudflare.com/d1/best-practices/read-replication/)

Database-size alerts and storage headroom are separate from latency acceptance.
Existing recovery evidence covers thousands of rows; it cannot establish a
backup write-pause budget at hundreds of thousands of rows. Measure larger
datasets and actual document distributions before widening the operating scope.

## Measurement results

The first run started at 16:01 UTC on 14 November 2026 against version
`f773d584-745b-414e-92f0-0aaad2090019`. Every client request entered through SIN.
The starting database contained 210 journals, 1,052 lines and 786 command
receipts, occupying about 6.3 MB.

| Profile | Command samples | Command p50 / p95 | Trial balance p95 | Ledger p95 | Dashboard p95 | Result |
|---|---:|---:|---:|---:|---:|---|
| Baseline | 8 | 5.02 / 7.45 s | 3.37 s | 2.56 s | 2.87 s | Latency target failed |
| Normal | 20 | 10.30 / 13.24 s | 3.14 s | 2.77 s | 2.22 s | Latency target failed |
| Burst | 24 | 21.35 / 23.58 s | 2.76 s | 2.17 s | 2.47 s | Five response transport failures; load stopped |
| Large | 0 | — | — | — | — | Not run after burst failure |

The burst command latency distribution includes all attempted requests, including
transport failures. Nineteen burst clients received 201, five received no HTTP
response. Report sample counts were 18, 76 and 136 across the three profiles;
all returned 200 and their arithmetic/content checks passed. No automatic retry
hid any failed measurement.

Native receipt reconciliation found four of the five lost-response commands
committed and one absent. The original session's revocation was read back from
D1 before treating the absent command as uncommitted. Thus 51 of the 52 attempted
original commands have receipts. Cleanup compensated all 51 exactly, deactivated
the two fixture accounts, verified every original account balance and dashboard
value, unique entry numbers, clean foreign keys and zero assertions, then signed
out. The database now contains 312 journals and 2,804 lines, about 7.3 MB.

## Placement diagnosis and controlled staging correction

The native analytics window from 16:02 to 16:07:30 UTC reports the command
coordinator in **AMS**, while the database primary is in **APAC** (a direct D1
query reports KIX). The sampled D1 backend data contains 2,938 observations,
averaging 0.561 ms per batch with an approximate maximum of 16.142 ms. The 55
sampled command-object invocations all report success, averaging 64.1 ms CPU
and 11,674 ms wall time, with an approximate maximum wall time of 23,441 ms.
These aggregates include background operations in that window; they are not
one-to-one joins with measured client commands or a pure SQL-versus-network
decomposition. They establish a concrete placement mismatch and low SQL
execution time; the improvement hypothesis needs a controlled rerun.

A location hint cannot move an already-created object. The staging correction
uses one explicit `apac-v1` generation and an `apac` creation hint, shared by
HTTP commands, jobs and restart/report proofs. The unconfigured route preserves
the existing organization name. Generations are an operator deployment setting,
never per-request sharding. No object business state needs copying: command
receipts, numbering and financial state are in D1. The hint is best effort,
so the new location must be measured. [Durable Object location rules](https://developers.cloudflare.com/durable-objects/reference/data-location/)

Only the isolated verification environment is selected for this correction.
Before changing an existing route, finish/reconcile all verifiers, acquire a
short native D1 write pause, deploy at 100%, and establish that old coordinator
invocations have drained before releasing the pause. Never run old and new
coordinators concurrently as an attempted throughput improvement. Production
needs its own quiesced transition if an object already exists.

The application was deployed at 100% as version
`8d61b241-b5c4-41da-a20c-3fb214512b42` at 16:16:37 UTC, with reported startup
4,623 ms and unchanged frontend `/assets/index-C3bnUq5k.js`. The native pause
used owner `coordinator-apac-20261115`, fence 5, a ten-minute expiry, and was
released after 16:17:39 UTC. The last observed old coordinator completion began
at 16:12:59 and lasted 2.85 seconds; all verifier processes had ended before the
pause. Native analytics confirms the new object in **SIN**. No demo or production
route was changed, and no object storage was deleted.

A replay through the new coordinator of an existing command returned the exact
original entry `01a1dacb-ddce-721d-bd87-181fa4e2c399`, with journal count unchanged
at 312. The HTTP replay took 2,055 ms and the native object invocation 472 ms.
This verifies that moving the coordinating instance retains D1 idempotency
receipts, including when its fixture accounts are now inactive.

## Rerun after the placement correction

Only command routing/placement changed; authorization, query construction,
financial planning and report implementations were retained. The database is
larger because the first run's immutable compensated history remains. This is
a useful successive-run comparison, not randomized benchmarking.

| Profile | Commands | Command p50 / p95 | Trial balance p95 | Ledger p95 | Dashboard p95 | Result |
|---|---:|---:|---:|---:|---:|---|
| Baseline | 8 | 1.55 / 3.70 s | 2.33 s | 1.67 s | 1.53 s | Passed |
| Normal | 20 | 3.98 / 5.71 s | 2.76 s | 1.21 s | 1.57 s | Command latency exceeds 5 s |
| Burst | 24 | 7.68 / 10.61 s | 2.66 s | 1.39 s | 2.24 s | Command latency exceeds 10 s |
| Large | 2 | 1.96 / 1.99 s | 2.76 s | 2.66 s | 1.83 s | Passed; only two commands |

All 54 original commands returned 201. All 126 report requests returned 200,
with balanced trial balances, exact running ledger values and unchanged invoice/
dashboard totals. There were no response transport failures. Normal command p95
fell from 13.24 to 5.71 seconds, and burst p95 from 23.58 to 10.61 seconds. The
original zero-error and latency targets were not relaxed. Overall capacity
acceptance still fails the normal/burst command latency thresholds.

All 54 originals were reconciled and compensated, fixture accounts deactivated,
and every original account balance/dashboard value restored. The verifier exits
nonzero for the unmet latency targets after successful cleanup. Final native
checks find 420 journal entries, 4,996 lines, 1,005 command receipts, database size
8,536,064 bytes, clean foreign keys, zero assertions, no active maintenance lease,
no scheduler error and no active sessions created during these runs. The health
assessment returns `ok`; it does not replace the failed latency acceptance.

During the 16:18:30–16:21:00 UTC analytics window, 68 sampled coordinator
invocations all report SIN/success, averaging 66.3 ms CPU and 3,221 ms wall time
(approximate maximum 7,522 ms). D1's 1,553 sampled backend observations average
0.587 ms per batch (approximate maximum 12.936 ms), still on its APAC primary.
The window includes background work and some cleanup, so these are corroborating
placement/timing observations, not matched per-request profiler spans.

## Fresh authorization with fewer database trips

Two new regression cases first failed against the sequential authorization
implementation: the real trial-balance dependency graph performed identity
authorization twice, and identity reads escaped a single native batch. Identity
now reads the user/session, roles and permissions in one primary D1 batch. The
permission query determines superuser status inside that same batch. FastAPI's
dependency graph shares the actor only within the current HTTP request; the next
request starts fresh. Command coordination still independently reauthorizes
after acquiring its mutex, and commit-time session/user guards are unchanged.
No cross-request permission cache was added. [D1 batches](https://developers.cloudflare.com/d1/worker-api/d1-database/),
[FastAPI dependency caching](https://fastapi.tiangolo.com/tutorial/dependencies/sub-dependencies/)

The focused boundary/auth/management checks pass, as does the shared quality gate
with 332 backend and 39 frontend tests. Verification version
`bf0cb996-f6a0-4972-b861-3a3c9f50f2c3` was deployed at 100% at 16:32:41 UTC on
14 November, reported startup 2,653 ms, retaining the SIN `apac-v1` coordinator
and unchanged frontend asset. The demo received the same authorization change as
version `3559ee6f-54a5-45ea-968c-9335910cd3f7` on 15 November at 05:33:17 UTC,
reported startup 2,710 ms. Its existing command object was independently found in
SIN, so its original organization route is preserved, with an `apac` hint for
creation. All 112 native role/ownership checks and the coherent 95-step seed
checkpoint verification pass on that version, with exact stock/open balances and
balanced journals. Production remains on its original runtime.

`scripts/verify_identity_remote.py` passes 21 native HTTP checks: administrator
permissions, a restricted staff role, allowed reads and one allowed command,
forbidden access, immediate role revocation for an existing token, and immediate
access/refresh rejection after password reset. It reconciles creation receipts,
deactivates the fixture user, clears the fixture role and archives the product;
both sessions sign out. Its private resumable checkpoint is under
`.cloudflare/verification/identity/`. It sends no mail or financial commands.

The rerun started at 16:35:11 UTC with 420 journals and 4,996 lines already
present. Its immutable history is larger than both earlier runs.

| Profile | Commands | Command p50 / p95 | Trial balance p95 | Ledger p95 | Dashboard p95 | Result |
|---|---:|---:|---:|---:|---:|---|
| Baseline | 8 | 1.29 / 3.86 s | 2.74 s | 0.77 s | 1.76 s | Passed |
| Normal | 20 | 3.27 / 3.54 s | 2.62 s | 1.17 s | 1.70 s | Passed |
| Burst | 24 | 6.39 / 7.97 s | 2.89 s | 0.83 s | 1.56 s | Passed |
| Large | 2 | 1.46 / 1.52 s | 2.72 s | 0.99 s | 1.41 s | Passed; only two commands |

All 54 commands and 115 reports returned their expected success responses and
passed arithmetic/content checks. All four profiles meet the unchanged latency
and zero-error targets. The slowest normal command was 5.21 seconds; the target
is a p95, not a promise that every request takes under five seconds. Measurements
ended at 16:36:37; receipt reconciliation and exact compensation completed at
16:38:08. Every original account balance and dashboard value was restored,
fixture accounts deactivated, and the session signed out. Final history contains
528 journals, 7,188 lines and 1,124 command receipts. The checkpoint's health
assessment is `ok`; later native checks also find clean foreign keys, zero
assertions, no active maintenance lease, no scheduler error and no active
sessions created during these checks.

This accepts only the stated journal/report experiment at its measured data
size. It establishes neither an open-loop maximum rate nor a years-of-history
capacity guarantee. Larger histories, representative invoice/stock/payment
loads and recovery headroom remain necessary before claiming a wider SME
operating envelope. The small sample counts and successive-run design above
continue to apply.

## Scheduled recovery capacity finding

A fresh health check on 15 November finds the coherent demo healthy. Its
scheduled backup `01a1db37-c986-76f7-8b01-3d4e83577669` completed on 14 November
between 18:00:28 and 18:05:20 UTC: 1,609 rows, 77 pages, 470,434 bytes, manifest
`40f770be7adafc324519bfc7f2d91ce1a49dbea8a480530bfec1f620292bd0b0`.

The larger verification database's scheduled backup
`01a1db37-fc50-736d-8747-dabc347ed5ce` failed at 18:10:43 UTC with
`backup_timeout: Backup exceeded its maximum write pause`, after 13,895 rows,
312 pages and 6,405,815 bytes, at table index 49. Its lease is released, but its
latest successful backup was overdue, so that check's operational assessment was
**critical** (`backup_overdue`, `backup_failed`). This supersedes the earlier
post-load `ok` assessment for current recovery readiness; the journal/report
timing measurements remain valid at their recorded times.

The ten-minute pause bound remains unchanged. Backup throughput and larger
recovery acceptance need correction before production cutover. The failure is
retained as evidence; neither an incomplete archive nor its manifest is treated
as recoverable. Private health/backup readbacks are saved under
`.cloudflare/verification/identity/`.

## Larger snapshot correction and exact recovery

The next regression used 1,250 small audit rows. It first reproduced 26 page
exchanges, then passed with three pages after increasing the candidate row cap
from 50 to 500. SQLite still applies the existing normal 2 MiB byte budget before
returning values to the Worker, and the 16 MiB absolute object bound remains.
The same test restores native SQL batches containing 500 rows plus their
receipt, preserving Unicode and the 18-digit invoice coefficient. Wide-row,
lost-response, corruption, occupied-target and lease-expiry tests still pass.
The exact v2 archive format and 600-second pause limit are unchanged.

The first variable changed was page density; the backup object remains in ATL,
and D1/R2 routing, controller loop and maximum pause were retained. This isolates
the correction from a simultaneous controller placement change. Version
`ea8a98e0-61f1-4674-9eeb-df804f0b81ca` was deployed to verification on 15 November
at 05:52:44 UTC, reported startup 2,643 ms. The shared quality gate passes 333
backend and 39 frontend tests.

Cron-driven backup `01a1ddc5-9ef0-7617-821c-18736e9f1b87` completed 14,415 rows
across all 62 data tables in 83 pages and 6,519,252 bytes. Capture began at
05:54:40.803 UTC and publication completed at 05:58:18.815: **218 seconds**,
comfortably inside the unchanged pause bound. Native metadata confirms pages
with 500 rows. The manifest hash is
`7a1b49f07e47b3e1a689369e93f40dd3892aad98c7225e4718c02d626ec8b3e2`.

A new APAC target, `bd4015b2-46eb-48d4-baef-8651420ef6f3`, was verified empty
before binding it for this run. The earlier accepted target remains preserved.
Recovery `01a1ddc9-10f8-7519-acfe-f31939ac7941`, requested at 05:58:24.249 UTC,
completed at 06:08:58.452: about **10 minutes 34 seconds**. All exact rows/schema,
foreign keys, target session revocation and external delivery holds passed.
Direct target reads find 528 journals, 7,188 journal lines and coefficient
`999999999999999999`, no active sessions, no assertions and `hold_external=1`.
Source balances match their pre-run baseline and source health returns `ok`.

`scripts/verify_backup_capacity_remote.py` observes the saved jobs while Cron
drives them; it makes no manual advance calls and creates no financial fixture.
Its private checkpoint is complete, the proof session signed out and its
temporary refresh cookie removed. Both accepted recovery databases are occupied
and must not be reset to repeat a verifier. This resolves the observed larger
snapshot failure at this dataset size; it does not establish an unbounded backup
or recovery envelope.

The coherent demo also received the page-density correction as version
`b0bf18a0-1937-4b82-895c-7a12bfd478eb` on 15 November at 06:19:06 UTC,
reported startup 2,816 ms. Its frontend asset remains unchanged. Native checks
confirm healthy assessment, closed bootstrap/restart probes, enabled jobs and
test-mode email; the proof session signed out. No new demo recovery binding was
added.

## Linked trade measurement on 15 November

The first run (`a9884d3b39bb4edd8cf3684901f7f1da`) started at 07:27:44 UTC
on verification version `ea8a98e0-61f1-4674-9eeb-df804f0b81ca`, with 528 journals,
7,188 journal lines, 42 invoices and 34 payments already present. All 18 linked
cycles complete: orders are closed, three product lines have matching delivered/
invoiced quantities, invoices are paid for exactly `60.00`, payment allocations
are complete, and final stock/COGS/revenue/AR checks match the known quantities.

| Operation | Baseline p95 | Normal p95 | Burst p95 |
|---|---:|---:|---:|
| order-create | 2.76 s | 7.51 s | 11.12 s |
| order-confirm | 1.42 s | 5.52 s | 8.59 s |
| delivery-create | 1.57 s | 6.43 s | 11.57 s |
| delivery-post | 2.27 s | 7.55 s | 14.56 s |
| invoice-create | 1.68 s | 6.74 s | 13.70 s |
| invoice-post | 3.36 s | 9.32 s | 18.19 s |
| payment-create | 2.59 s | 8.54 s | 17.49 s |
| trial-balance | 3.27 s | 3.01 s | 3.22 s |
| stock | 0.42 s | 0.44 s | 0.47 s |
| dashboard | 1.32 s | 1.33 s | 1.62 s |
| cycle | 14.35 s | 45.01 s | 84.80 s |

Command samples per operation are 2/8/8, and cycle samples are 2/8/8.
The 126 measured commands all returned their expected success. Of 403 report
requests, one burst dashboard request returned **503**; all others returned 200.
The first verifier recorded the status and validation failure, but not that
response body, so its exact error code is unproven. Later runs also capture the
redacted error code. Baseline passes; normal and burst fail the predeclared
latency targets, and burst also fails the zero-error target. In particular,
invoice-post p95 is **9.32 s** with four writers and **18.19 s** with eight.
The successful journal-only experiment cannot stand in for these results.

The first cleanup restored every account balance and zero fixture stock but
correctly stopped at a dashboard mismatch: creating an empty warehouse adds a
low-stock row for each of four existing eligible products. The pre-fixture
low-stock count was 20 and the count with the retained warehouse is 24. It is
not a monetary imbalance. Cleanup now derives that four-row effect from the
eligible product count, independently of the observed dashboard difference,
and compares every other dashboard field exactly. Future runs reuse this
warehouse, so they must restore the complete pre-run dashboard without another
warehouse increment. A local regression with an existing low-stock product
covers both the reused-warehouse and first-run retained-warehouse cases.

Cleanup resumed the same receipts without recreating original commands and
finished at **07:41:13 UTC**. All 18 payments/invoices/deliveries are reversed,
orders cancelled, the supply receipt voided and fixture customer/products
archived. Native checks pass numbering uniqueness, foreign keys, zero assertion
rows, exact original account balances and the explicitly expected dashboard.
All three verifier sessions are revoked, no refresh cookie remains in the private
checkpoint and operational health is `ok`. Immutable history now contains
638 journals, 7,408 journal lines, 60 invoices, 52 payments and 883 stock moves.

The verifier has separate local regressions for committed payment response loss,
original-session fencing and resuming cleanup without another journal. Its
first published implementation passes the hosted quality gate at `4979b29`:
[run 43690146538](https://github.com/aliahadmd/sme-erp/actions/runs/43690146538).

**Capacity acceptance remains failed.** Reduce sequential D1 reference/settings/
numbering/account reads while retaining fresh authorization, revision guards,
exact arithmetic and the single atomic commit; then rerun the unchanged profiles.
The dashboard 503 also needs diagnosis and an observed zero-error rerun before
production capacity is claimed. No production or coherent-demo data was used.

### Read-boundary diagnosis

A local trace executes the real API and the same three-product linked cycle,
counting `D1Database.all` and `batch` calls. Each batch counts as one boundary
even when it contains several SQL statements. These are source/runtime boundary
counts, **not native latency attribution**:

| Command | D1 calls, including authorization/receipt/commit |
|---|---:|
| Order create | 11 |
| Order confirm | 7 |
| Delivery create | 13 |
| Delivery post | 18 |
| Invoice create | 10 |
| Invoice post | 23 |
| Payment create | 17 |

Invoice posting reads its source order and lines three times. Other repeated
reads include organization data, numbering prefixes and individual account
mappings. The first optimization should reuse those explicit, revision-guarded
references only within the current command and batch independent reads. Retain
fresh authorization after the mutex, durable receipt claims, uncertain-response
reconciliation, exact decimal transport, all revision guards and the one atomic
write batch. A changed query count alone does not prove the native targets pass.

### Command batching and second native measurement

The command plan now batches bounded organization/settings/numbering/mapped-
account references once within the current command. Aggregate headers/lines,
stock references and settlement effects are also read in native batches.
Invoice validation and progress share one guarded source-order context, which
carries planned progress. Each new command starts fresh; authorization and the
atomic write/receipt boundary are retained. The shared app-staging quality gate
passes 339 backend and 39 frontend tests, including custom account mapping,
next-command policy freshness, stale-policy rollback and one source-order read.

The same local API trace now reports:

| Command | Before D1 calls | After D1 calls |
|---|---:|---:|
| sales/orders | 11 | 8 |
| sales/orders/:id/confirm | 7 | 6 |
| inventory/deliveries | 13 | 10 |
| inventory/deliveries/:id/post | 18 | 10 |
| invoicing/invoices | 10 | 8 |
| invoicing/invoices/:id/post | 23 | 9 |
| invoicing/payments | 17 | 8 |

These counts include the independent HTTP/coordinator authorization, receipt
checks and one atomic commit. No cross-request cache was added.

The second native run (`71556363c93a48d88bba8f0dcf64a771`) began at 15:41:52 UTC
on 15 November, after direct CLI deployment of commit `6370e756f11d`, version
`63700716-6c5b-40bc-84b8-ef288eaa7720`. The unchanged profiles complete all 18
linked cycles and all 126 commands, but capacity remains **failed**:

| Operation | Baseline p95 | Normal p95 | Burst p95 |
|---|---:|---:|---:|
| order-create | 2.45 s | 10.26 s | 9.81 s |
| order-confirm | 0.83 s | 4.66 s | 7.99 s |
| delivery-create | 1.61 s | 5.34 s | 8.44 s |
| delivery-post | 1.86 s | 4.95 s | 9.86 s |
| invoice-create | 1.08 s | 5.63 s | 10.35 s |
| invoice-post | 1.63 s | 5.14 s | 9.77 s |
| payment-create | 1.48 s | 4.95 s | 9.18 s |
| trial-balance | 3.65 s | 3.74 s | 4.12 s |
| stock | 0.43 s | 0.55 s | 0.56 s |
| dashboard | 1.76 s | 1.78 s | 2.18 s |
| cycle | 10.00 s | 37.51 s | 58.80 s |

Of 255 reports, five dashboards return 503 with the captured code `report_busy`:
one in normal and four in burst. All other reports return 200. Baseline passes;
normal and burst fail both their respective latency and zero-error targets.
These small samples describe this bounded run, not a population percentile or
saturation limit. Faster invoice posting does not make the full profile pass.

Exact cleanup finished at 15:48:25 UTC. The reused warehouse is empty, every
original account balance and the complete dashboard match the pre-run values,
and numbering/FKs, empty assertions, health and owned-session revocation pass.
Retained history contains 748 journals, 7,628 journal lines, 78 invoices,
70 payments and 997 stock moves. No refresh cookie remains in the checkpoint.

### Dashboard consistency window

The dashboard previously read its epoch, invoice pages, clearing-effect pages,
low-stock counts and final epoch through separate sequential boundaries. Under
concurrent commits it exhausted all three consistency attempts and correctly
returned `report_busy`. Initial invoice/clearing pages, low-stock counts and
the initial epoch now share one native read batch. Continuation pages remain
bounded at 500 rows; all money still crosses D1 as text and aggregates exactly
in Python. A separate final epoch comparison and the same three-attempt limit
remain mandatory. [D1 batch semantics](https://developers.cloudflare.com/d1/worker-api/d1-database/#batch)

A dashboard without continuation pages now needs two D1 boundaries rather than
five. Regression cases commit after the initial batch and between continuation
pages, requiring discarded results and a fresh attempt; continuous changes
must still produce `report_busy` after three attempts. Native remeasurement is
required before claiming this resolves the observed failures. The full quality
gate passes 343 backend and 39 frontend tests, metadata checks, lint/format,
vendoring and the app-staging Worker/SPA build.
