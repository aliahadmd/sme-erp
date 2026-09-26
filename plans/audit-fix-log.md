# Audit Fix Log — 2026-09-26

All findings from the codebase audit were fixed and verified (`make verify` exit 0,
68 API tests + 7 web tests). Commits: `ab6d256` (critical + high), `bc7d1ab` (mediums).

| Finding | Fix | Test |
|---|---|---|
| C1 refresh recursion storm | `retry:false` on refresh + single-flight + `/api/auth/` interceptor guard | web suite |
| C2 unauthenticated AI proxy | `require_superuser` on `/api/ai/echo` | `test_ai_echo_requires_superuser` |
| C3 stock/allocation lost updates | `SELECT … FOR UPDATE` in `post_move` + payment allocation/void | `test_concurrent_deliveries_never_lose_updates` |
| H1 superuser escalation / self-service | superuser-mint guard, self-deactivation block, last-active-superuser block | `test_superuser_escalation_blocked`, `test_self_deactivation_and_last_superuser_guarded` |
| H2 over-delivery / duplicate invoicing | per-line `qty_delivered/qty_received/qty_invoiced` tracking, remaining-qty guards, auto-close | `test_over_delivery_and_partial_remaining`, `test_receipt_of_fully_received_po_rejected`, `test_duplicate_invoicing_of_order_rejected_and_autoclose` |
| H3 event handler errors → 500 after commit | per-handler try/except + error log in `events.publish` | `test_event_bus_isolates_failing_handlers` |
| H4 no login rate limiting | redis failure counter per (ip, email), 10/5min → 429 | `test_login_rate_limited_after_repeated_failures` |
| H5 insecure defaults | fail-fast validator (non-dev requires ≥32-char non-default JWT_SECRET); CORS accepts comma-separated | `test_production_rejects_default_jwt_secret`, `test_cors_origins_accept_comma_separated` |
| M: VITE_API_URL never reached web container | passed via compose `environment` | compose |
| M: committed S3 credentials | `s3.json` generated from env at container start; file removed from git | compose recreate |
| M: uoms cached under permissions key | dedicated cache key | code |
| M: orders never auto-closed | closed when delivered/received AND invoiced | `test_duplicate_invoicing…autoclose` |
| M: SKU collision-prone generator | uuid-suffix | code |
| M: missing api/web healthchecks | compose healthchecks (both healthy) | `docker compose ps` |
| M: untyped document responses | response_model restored on order routers | code |

Deliberately NOT changed (documented design/phase-2):
- AP invoices booked gross (input-tax recovery deferred)
- Receipt-void restores quantity, not average cost
- Containers run as root (dev-only; production Dockerfiles are phase 2, non-root then)
- CI/CD + backups (phase 2)


---

# Post-Phase-2 Audit Fix Log — 2026-09-27

Findings from the second full audit, all fixed and verified (`make verify` exit 0,
88 API tests + 7 web tests). Commits: `4ea5d14`, `f214f45`+`bd9141f`+`0163e18` (HR UI/fixes).

| Finding | Severity | Fix | Test |
|---|---|---|---|
| Prod backup job crashes (root-owned /app/dumps) | HIGH | `install -d -o appuser /app/dumps` in api.prod.Dockerfile | backup job skips gracefully in dev |
| Quotation→SO conversion loses FX/base | HIGH | conversion inherits quotation fx_rate + computes total_base | multi-currency test asserts AR delta |
| Statement ignores credit notes | HIGH | party_statement nets posted credit notes as negative lines | credit-flow test asserts AR delta + balance |
| HR leave: no ownership scoping | HIGH | non-approvers can only see/submit their own employee's requests | hr ownership checks (PermissionDenied) |
| Email bodies logged at info | MEDIUM | metadata-only logging (to/subject/length) | code |
| AI summarize: no usage budget | MEDIUM | redis daily counter, 429 ai_budget_exceeded over limit | `test_ai_budget_enforced` |
| Quotation expiry via write-in-GET | MEDIUM | `expire_quotations` worker cron (00:05 daily); GET is read-only | expired-quote test uses the job |
| Numbering prefix length unvalidated | MEDIUM | 1–10 char alphanumeric cap in update_numbering_prefix | validation error path |
| ai/search docstring overstates scope | LOW | corrected to products-only keyword search | code |
