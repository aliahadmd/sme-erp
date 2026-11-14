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

The next performance work should reduce avoidable primary round trips while
preserving fresh authorization and atomic financial guards, then repeat the same
targets. In particular, authorization currently reads the user/session, roles
and permissions sequentially, and some HTTP dependency paths repeat that work
within one request. Do not introduce cross-request permission caches to improve
these measurements. Larger histories and representative invoice/stock/payment
loads remain necessary before claiming a wider SME operating envelope.
