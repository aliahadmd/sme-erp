"""Exact ledger reads without SQLite SUM overflow or floating-point promotion.

Journal rows are immutable. Capture both header and line rowid high-water marks
in one read batch; later/backdated postings cannot enter subsequent pages.
Trial-balance accumulation uses Python integer cents and bounded D1 reads.
"""

from decimal import Decimal

from app.modules.accounting.schemas import LedgerRow, TrialBalanceRow

from erp_cf.db import Statement, plain
from erp_cf.store import Repository, tables


def cents(value):
    """Convert an unbounded integer total without a decimal context truncation."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    return Decimal(f"{sign}{value // 100}.{value % 100:02d}")


class JournalReader:
    def __init__(self, db, org_id):
        self.db = db
        self.org_id = org_id

    async def snapshot(self):
        account_table = tables()["accounting_accounts"]
        results = await self.db.batch(
            [
                Statement(
                    "SELECT (SELECT CAST(COALESCE(MAX(rowid),0) AS TEXT) "
                    "FROM accounting_journal_entries) AS entry_limit, "
                    "(SELECT CAST(COALESCE(MAX(rowid),0) AS TEXT) "
                    "FROM accounting_journal_lines) AS line_limit"
                ),
                Statement(
                    f"SELECT {account_table.projection()} FROM accounting_accounts "
                    "WHERE org_id=? ORDER BY code",
                    (self.org_id,),
                ),
            ]
        )
        self.limits = plain(results[0]["results"][0])
        self.accounts = [account_table.decode(plain(r)) for r in results[1]["results"]]
        return self

    async def lines(self, *, account_id=None, as_of=None):
        cursor = "0"
        while True:
            where = ""
            params = [
                self.org_id,
                self.limits["entry_limit"],
                self.limits["line_limit"],
                cursor,
            ]
            if account_id:
                where += " AND l.account_id=?"
                params.append(str(account_id))
            if as_of:
                where += " AND e.entry_date<=?"
                params.append(as_of.isoformat())
            rows = await self.db.all(
                "SELECT CAST(l.rowid AS TEXT) AS cursor,l.id,l.account_id,"
                "CAST(l.debit AS TEXT) AS debit,CAST(l.credit AS TEXT) AS credit,"
                "e.id AS entry_id,e.number AS entry_number,e.entry_date,e.created_at,e.memo "
                "FROM accounting_journal_lines l JOIN accounting_journal_entries e "
                "ON e.id=l.entry_id WHERE e.org_id=? AND e.status='posted' "
                "AND e.rowid<=CAST(? AS INTEGER) AND l.rowid<=CAST(? AS INTEGER) "
                "AND l.rowid>CAST(? AS INTEGER)" + where + " ORDER BY l.rowid LIMIT 500",
                tuple(params),
            )
            if not rows:
                return
            for row in rows:
                yield row
            cursor = rows[-1]["cursor"]
            if len(rows) < 500:
                return

    async def trial_balance(self, as_of=None):
        totals = {}
        async for line in self.lines(as_of=as_of):
            total = totals.setdefault(line["account_id"], [0, 0])
            total[0] += int(line["debit"])
            total[1] += int(line["credit"])
        result = []
        for account in self.accounts:
            debit, credit = totals.get(account["id"], (0, 0))
            result.append(
                TrialBalanceRow(
                    account_id=account["id"],
                    code=account["code"],
                    name=account["name"],
                    type=account["type"],
                    total_debit=cents(debit),
                    total_credit=cents(credit),
                    balance=cents(debit - credit),
                )
            )
        return result

    async def ledger(self, account_id):
        lines = [line async for line in self.lines(account_id=account_id)]
        lines.sort(key=lambda r: (r["entry_date"], r["created_at"], r["entry_id"], r["id"]))
        balance = 0
        for index, line in enumerate(lines):
            debit, credit = int(line["debit"]), int(line["credit"])
            balance += debit - credit
            lines[index] = LedgerRow(
                entry_number=line["entry_number"],
                entry_date=line["entry_date"],
                memo=line["memo"],
                debit=cents(debit),
                credit=cents(credit),
                balance=cents(balance),
            )
        return lines

    async def entries(self, limit, offset, source_type=None):
        repository = Repository(self.db, "accounting_journal_entries", self.org_id)
        where = "rowid<=CAST(? AS INTEGER)"
        params = (self.limits["entry_limit"],)
        if source_type:
            where += " AND source_type=?"
            params += (source_type,)
        page = await repository.page(
            limit,
            offset,
            where=where,
            params=params,
            order_by="entry_date DESC,created_at DESC,id DESC",
        )
        accounts = {account["id"]: account for account in self.accounts}
        by_entry = {entry["id"]: [] for entry in page["items"]}
        identities = list(by_entry)
        for start in range(0, len(identities), 80):
            chunk = identities[start : start + 80]
            lines = await Repository(self.db, "accounting_journal_lines", self.org_id).find(
                "entry_id IN (" + ",".join("?" for _ in chunk) + ") AND rowid<=CAST(? AS INTEGER)",
                (*chunk, self.limits["line_limit"]),
                order_by="id",
            )
            for line in lines:
                account = accounts[line["account_id"]]
                by_entry[line["entry_id"]].append(
                    {**line, "account_code": account["code"], "account_name": account["name"]}
                )
        page["items"] = [{**entry, "lines": by_entry[entry["id"]]} for entry in page["items"]]
        return page
