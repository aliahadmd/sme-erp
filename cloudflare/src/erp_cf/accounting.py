"""Journal plans share their caller's atomic D1 commit, never an ORM session.

Keep one Ledger per command. It carries newly planned accounts explicitly;
repository reads do not see the command's pending writes. Reversals mirror
stored lines rather than recalculating with current mappings or exchange rates.
"""

from datetime import UTC, datetime
from decimal import Decimal, DecimalException

from app.modules.accounting.defaults import DEFAULT_COA, DEFAULT_MAPPING

from erp_cf.db import assertion
from erp_cf.errors import ConflictError, ValidationError
from erp_cf.money import coefficient, decimal_value, exact_sum, money

ZERO = Decimal("0.00")


class Ledger:
    def __init__(self, plan):
        self.plan = plan
        self.accounts = {}
        self.by_code = {}
        self.mapping = None

    def remember(self, account):
        self.accounts[account["id"]] = account
        self.by_code[account["code"]] = account
        return account

    async def get_account(self, identity):
        identity = str(identity)
        if identity not in self.accounts:
            account = await self.plan.repository("accounting_accounts").get(
                identity, required=False
            )
            if account is None:
                raise ValidationError("Unknown account in entry")
            self.remember(account)
        return self.accounts[identity]

    async def account(self, key):
        if self.mapping is None:
            setting = await self.plan.repository("core_settings").first(
                "key=?", ("accounting.mapping",)
            )
            if setting:
                self.plan.guard("core_settings", setting)
            self.mapping = setting["value"] if setting else {}
        code = self.mapping.get(key) or DEFAULT_MAPPING.get(key)
        if not code:
            raise ValidationError(f"No account mapping for '{key}' — configure accounting.mapping")
        if code not in self.by_code:
            account = await self.plan.repository("accounting_accounts").first("code=?", (code,))
            if account is None:
                default = next((row for row in DEFAULT_COA if row[0] == code), None)
                if default is None:
                    raise ValidationError(f"Account {code} (mapped from '{key}') does not exist")
                account = self.plan.add(
                    "accounting_accounts",
                    {"code": code, "name": default[1], "type": default[2], "is_system": True},
                )
            self.remember(account)
        return self.by_code[code]

    async def payment_account(self, method):
        return await self.account("cash" if method == "cash" else "bank")

    async def post(
        self, *, entry_date, memo, source_type, lines, source_id=None, allow_inactive=False
    ):
        normalized = []
        accounts = {}
        for account, debit, credit in lines:
            try:
                d, c = decimal_value(debit), decimal_value(credit)
                if d < 0 or c < 0:
                    raise ValidationError("Journal amounts cannot be negative")
                if d > 0 and c > 0:
                    raise ValidationError("A journal line cannot have both debit and credit")
                d, c = money(d), money(c)
                coefficient(d, 2)
                coefficient(c, 2)
            except (DecimalException, ValueError) as exc:
                raise ValidationError("Invalid journal amount precision") from exc
            if not account["is_active"] and not allow_inactive:
                raise ValidationError(f"Account {account['code']} is inactive")
            if d == 0 and c == 0:
                continue
            accounts[account["id"]] = account
            normalized.append((account, d, c))
        debit = exact_sum([d for _, d, _ in normalized])
        credit = exact_sum([c for _, _, c in normalized])
        if debit != credit:
            raise ValidationError(f"Entry not balanced: debit {debit} != credit {credit}")
        if not normalized:
            raise ValidationError("Journal entry needs at least one nonzero line")
        for account in accounts.values():
            self.plan.guard("accounting_accounts", account)

        from erp_cf.numbering import next_number

        entry = self.plan.add(
            "accounting_journal_entries",
            {
                "number": await next_number(self.plan, "journal_entry"),
                "entry_date": entry_date,
                "memo": memo,
                "source_type": source_type,
                "source_id": source_id,
                "created_by": self.plan.actor_id,
            },
        )
        posted = []
        for account, d, c in normalized:
            line = self.plan.add(
                "accounting_journal_lines",
                {"entry_id": entry["id"], "account_id": account["id"], "debit": d, "credit": c},
            )
            posted.append(
                {**line, "account_code": account["code"], "account_name": account["name"]}
            )
        return {**entry, "lines": posted}

    async def reverse(self, source_id, *, memo, entry_date=None):
        source_id = str(source_id)
        entries = self.plan.repository("accounting_journal_entries")
        if await entries.first("source_id=? AND source_type='reversal'", (source_id,)):
            raise ConflictError("Source journal entries have already been reversed")
        originals = await entries.find(
            "source_id=? AND source_type!='reversal'", (source_id,), order_by="id"
        )
        if not originals:
            return None
        self.plan.statements.append(
            assertion(
                self.plan.identity,
                "NOT EXISTS(SELECT 1 FROM accounting_journal_entries "
                "WHERE org_id=? AND source_id=? AND source_type='reversal')",
                (self.plan.org_id, source_id),
            )
        )
        lines = []
        for entry in originals:
            self.plan.guard("accounting_journal_entries", entry)
            for line in await self.plan.repository("accounting_journal_lines").find(
                "entry_id=?", (entry["id"],), order_by="id"
            ):
                self.plan.guard("accounting_journal_lines", line)
                account = await self.get_account(line["account_id"])
                lines.append((account, line["credit"], line["debit"]))
        if not lines:
            return None
        return await self.post(
            entry_date=entry_date or datetime.now(UTC).date(),
            memo=memo,
            source_type="reversal",
            source_id=source_id,
            lines=lines,
            allow_inactive=True,
        )
