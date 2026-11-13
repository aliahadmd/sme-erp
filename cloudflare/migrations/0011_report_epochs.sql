-- Each supported business command advances this in its own atomic batch.
-- Paged cross-module reports retry if the epoch changes during their reads.
CREATE TABLE cf_business_epochs (
 org_id TEXT PRIMARY KEY NOT NULL,
 version INTEGER NOT NULL CHECK(version>=0 AND version<=9007199254740991)
) STRICT;
CREATE INDEX invoicing_report_dates ON invoicing_invoices(org_id,status,invoice_date);
