# Plan 8 — Email Notifications & Preferences

- **Depends on:** plan-1 (worker), plan-6 (credit notes referenced in templates)
- **Goal:** the ERP talks to the outside world: transactional emails for the
  moments that matter, with user-level preferences and sane rate limits.

## 1. Backend

- [ ] SMTP settings (host, port, user, from, TLS) in `settings` + env fallback;
      `JOBS_MODE=inline` sends synchronously in dev, worker sends in prod.
- [ ] `jobs/email.py`: templated emails (Jinja2 templates in
      `app/jobs/templates/`: invoice_sent, payment_received, invoice_overdue,
      low_stock_digest, leave_request (plan-10 hook), test_email).
- [ ] Event → email subscribers (reuse the phase-1 notification events):
      `invoice.posted` → email the party's billing contact (contacts gain an
      optional billing email field), `payment.recorded` → receipt email;
      overdue reminders daily via the existing overdue job (per-invoice, max
      once per 7 days, tracked on the notification dedupe payload).
- [ ] User preferences: `email_opt_in` per notification type
      (`core.notification_prefs` JSONB on settings or a per-user table) +
      endpoints to read/update own preferences.
- [ ] Safety: email sending is rate-limited per party per day; failures logged
      and retried by arq (3 attempts); dev without SMTP logs the email instead.

## 2. Frontend

- [ ] Settings → Email: SMTP config form (superuser) + "Send test email".
- [ ] User menu → Notification preferences: per-type toggles.
- [ ] Invoice editor: "Send to customer" action (queues the email, marks the
      invoice `sent_at`, visible as a small badge in lists).

## Acceptance

- [ ] Posting an invoice with a billing email queues and delivers an email
      (Mailpit-style local sink in dev compose); overdue job sends at most one
      reminder per week; preferences actually suppress emails.
- [ ] No emails leave the system when SMTP is unconfigured (logged only).
- [ ] `make verify` green; template rendering covered by tests.
