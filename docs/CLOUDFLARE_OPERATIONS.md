# Cloudflare operational monitoring

The application assesses its operational records through native D1 and R2
bindings. `GET /api/operations/health` requires a current authenticated user with
`core.settings.read`. HTTP 200 means an assessment was returned; inspect its
`status`, `issues` and `metrics`. The states are `ok`, `maintenance`, `warning`,
`critical`, `unavailable` and `disabled`. The public `/readyz` remains a database
connectivity check and is not a substitute for this assessment.

The assessment reads operational aggregates and R2 object metadata. It does
not post financial commands, retry deliveries, resolve uncertain mail, cancel
backups or change maintenance leases. Its reports and Cron logs exclude raw
event payloads, recipients, subjects, tokens, SQL and exception text.

## Thresholds

These are explicit initial operating thresholds, not measured capacity/SLO
acceptance. Change the versioned bindings deliberately for another environment.

| Binding | Default | Assessment |
|---|---|---|
| `OPS_ENABLED` | true after initialization | Enable HTTP assessment and Cron heartbeat |
| `OPS_DATABASE_BUDGET_BYTES` | 10,000,000,000 | Warn at 80%, critical at 90% of this planning budget |
| `OPS_OUTBOX_AGE_SECONDS` | 900 | Critical when eligible queued/pending work exceeds 15 minutes |
| `OPS_SCHEDULER_AGE_SECONDS` | 900 | Critical when successful application maintenance is absent/stale |
| `OPS_BACKUP_AGE_SECONDS` | 93,600 | Critical with no completed backup or one older than 26 hours |
| `OPS_BACKUP_STALL_SECONDS` | 600 | Critical when a pending/capturing/publishing backup stops progressing |
| `OPS_RESTORE_STALL_SECONDS` | 1,800 | Critical when an active restore stops progressing |

Database size comes from the binding's `D1Result.meta.size_after`. Missing or
invalid metadata produces an issue instead of a zero-sized healthy database.
The planning budget does not increase D1's provider limit.
[D1 result metadata](https://developers.cloudflare.com/d1/worker-api/return-object/),
[D1 limits](https://developers.cloudflare.com/d1/platform/limits/).

Future-dated quota deferrals are excluded from the ready backlog. An unexpired
mail claim is not classified as an uncertain result; expired claims and dead
mail require operator review. Dead/quarantined events, a new backup failure
after the latest success, and failed restores produce warnings. Disabled jobs
or backups produce critical issues when operations monitoring is enabled.

An expected backup pause suppresses scheduler/backlog age alerts only within
the configured total pause budget plus 60 seconds. A pause without a matching
active backup or one that exceeds that budget remains critical. Completed
backup discovery, commit marker and manifest objects must be present with
bounded sizes in private R2. This metadata check does not hash every data page
or establish restorability; retain the [recovery procedure](CLOUDFLARE_RECOVERY.md).

## Independent Cloudflare Monitors

When monitoring is enabled, each application Cron invocation emits a redacted `erp_operational_health`
record, including when ordinary jobs fail or a backup pauses them. The stable
`message` is `erp_operational_health:<status>`. Native Monitors evaluate SQL
outside that invocation, so a missing heartbeat can be detected even when the
application Cron stops running.

Render the review manifest and exact SQL for an isolated environment:

```sh
cd cloudflare
uv run python scripts/render_monitors.py --mode demo-staging
```

`monitoring/monitors.json` is a review manifest, not a Cloudflare API request.
The renderer creates/enables nothing. It prepares these native Monitors:

| Monitor | Fifteen-minute signal | Condition |
|---|---|---|
| Missing operational heartbeat | Count of health messages | `< 1` |
| Operational assessment needs attention | Count of warning/critical/unavailable health messages | `> 0` |
| Scheduled error logs | Count of scheduled logs with error level | `> 0` |

Use Manual time-range mode, a five-minute execution interval and a one-hour
repeat interval. Each SQL query has one account-scoped dataset, a lower time
bound and one numeric `value` column. `COUNT` returns a zero-valued row when the
heartbeat is absent; a query that merely returns no rows cannot fire a Monitor.
[Native Monitors](https://developers.cloudflare.com/notifications/monitors/),
[SQL API](https://developers.cloudflare.com/analytics/sql-api/).

Logs and their queries may be sampled or delayed. Keep application invocation
logs and the health stream enabled, verify observed heartbeats, and investigate
an absence as either application or telemetry failure. This is one provider's
monitoring plane, within the requested all-Cloudflare failure domain.

The three demo Monitors are enabled with one user-approved email destination.
Their IDs and query mappings are in `cloudflare/monitoring/deployed-demo.json`.
Cloudflare accepted one INFO-severity delivery test per policy, notification
history records all three as sent and not silenced, and the user confirmed all
three arrived. Natural firing/resolution is a separate observation from test
delivery. The verification Worker's Monitors remain templates; only the coherent
demo is subscribed to notifications.

For another environment, create its Monitors disabled first and preview the
rendered queries. The Notifications API uses `custom_analytics`, `source` set to
`sql_api`, a single SQL string in `selectors`, threshold operator/value,
`alert_trigger_preferences_short_window` set to `15m`, and `execution_interval`
set to `5m`. Read back the recipient, query, interval and threshold before
enabling. The API requires the short window even though the SQL already has
an explicit fifteen-minute bound. The review renderer's disabled default does
not represent the live demo policy state.

## Verification

The local operational cases cover private artifact presence, read-only SQL,
current permissions, database warning/critical boundaries, future deferrals,
expected and excessive pauses, active/expired mail claims, stalled/failed
restores, source/configuration failure and same-second backup outcome ordering.
They form part of `pnpm quality`.

Native evidence belongs in [the implementation log](CLOUDFLARE_IMPLEMENTATION.md).
The ignored per-environment assessments are under
`cloudflare/.cloudflare/verification/operations/`. A green health assessment
does not replace realistic command/report capacity testing, restored-data
reconciliation or delivery acceptance.
