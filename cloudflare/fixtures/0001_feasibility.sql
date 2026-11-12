-- Disposable, isolated proof data. These tables are not ERP business records.
CREATE TABLE IF NOT EXISTS proof_precision (
    id TEXT PRIMARY KEY NOT NULL,
    amount INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS proof_stock (
    id TEXT PRIMARY KEY NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    revision INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE IF NOT EXISTS proof_invoices (
    id TEXT PRIMARY KEY NOT NULL,
    total INTEGER NOT NULL CHECK (total >= 0),
    status TEXT NOT NULL CHECK (status IN ('posted', 'void'))
) STRICT;

CREATE TABLE IF NOT EXISTS proof_journal (
    id TEXT NOT NULL,
    account TEXT NOT NULL,
    debit INTEGER NOT NULL CHECK (debit >= 0),
    credit INTEGER NOT NULL CHECK (credit >= 0),
    CHECK (debit = 0 OR credit = 0),
    PRIMARY KEY (id, account)
) STRICT;

CREATE TABLE IF NOT EXISTS proof_audit (
    command_id TEXT PRIMARY KEY NOT NULL REFERENCES cf_commands(id),
    document_id TEXT NOT NULL,
    action TEXT NOT NULL
) STRICT;
