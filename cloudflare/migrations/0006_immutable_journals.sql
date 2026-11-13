-- Posted history is append-only. Voids add exact reversing entries.
-- Header and line rowid high-water marks provide a stable reporting snapshot
-- across bounded reads: later/backdated entries cannot enter an in-flight report.
CREATE TRIGGER accounting_entries_no_update BEFORE UPDATE ON accounting_journal_entries
BEGIN SELECT RAISE(ABORT, 'Posted journal entries are immutable'); END;
CREATE TRIGGER accounting_entries_no_delete BEFORE DELETE ON accounting_journal_entries
BEGIN SELECT RAISE(ABORT, 'Posted journal entries are immutable'); END;
CREATE TRIGGER accounting_lines_no_update BEFORE UPDATE ON accounting_journal_lines
BEGIN SELECT RAISE(ABORT, 'Posted journal lines are immutable'); END;
CREATE TRIGGER accounting_lines_no_delete BEFORE DELETE ON accounting_journal_lines
BEGIN SELECT RAISE(ABORT, 'Posted journal lines are immutable'); END;
