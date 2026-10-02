"""Accounting service — journal posting, CoA seeding, trial balance.

The event subscribers in postings.py translate business events into balanced
journal entries through `post_entry`.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.modules.accounting.models import Account, JournalEntry, JournalLine
from app.shared.numbering import next_number
from app.shared.order_engine import order_number_prefix

DEFAULT_COA: list[tuple[str, str, str]] = [
    # (code, name, type)
    ("1000", "Cash", "asset"),
    ("1010", "Bank", "asset"),
    ("1100", "Accounts Receivable", "asset"),
    ("1200", "Inventory", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("2050", "Goods Received Not Invoiced", "liability"),
    ("2100", "Tax Payable", "liability"),
    ("3000", "Owner's Equity", "equity"),
    ("4000", "Sales Revenue", "income"),
    ("4100", "Sales Returns & Refunds", "expense"),
    ("4900", "Realized FX Gain", "income"),
    ("5000", "Cost of Goods Sold", "expense"),
    ("5100", "Purchases Expense", "expense"),
    ("5200", "Stock Correction", "expense"),
    ("5900", "Rounding", "expense"),
    ("6900", "Realized FX Loss", "expense"),
]

DEFAULT_MAPPING: dict[str, str] = {
    "ar": "1100",
    "ap": "2000",
    "sales_revenue": "4000",
    "tax_payable": "2100",
    "inventory": "1200",
    "cogs": "5000",
    "refunds": "4100",
    "purchases": "5100",
    "stock_correction": "5200",
    "grni": "2050",
    "fx_gain": "4900",
    "fx_loss": "6900",
    "cash": "1000",
    "bank": "1010",
}


async def seed_coa(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Idempotent: creates missing system accounts + the default mapping setting."""
    from app.modules.core.service import get_setting, set_setting

    existing = set(
        (await session.scalars(select(Account.code).where(Account.org_id == org_id))).all()
    )
    for code, name, type_ in DEFAULT_COA:
        if code not in existing:
            session.add(Account(org_id=org_id, code=code, name=name, type=type_, is_system=True))
    await session.flush()
    if await get_setting(session, org_id, "accounting.mapping") is None:
        await set_setting(session, org_id, "accounting.mapping", dict(DEFAULT_MAPPING))


async def resolve_account(session: AsyncSession, org_id: uuid.UUID, mapping_key: str) -> Account:
    """Account by settings-mapped key, falling back to the default mapping."""
    from app.modules.core.service import get_setting

    mapping = await get_setting(session, org_id, "accounting.mapping")
    code = (mapping or {}).get(mapping_key) or DEFAULT_MAPPING.get(mapping_key)
    if not code:
        raise ValidationError(
            f"No account mapping for '{mapping_key}' — configure accounting.mapping"
        )
    result = await session.scalars(
        select(Account).where(Account.org_id == org_id, Account.code == code)
    )
    account = result.first()
    if not account:
        default = next((row for row in DEFAULT_COA if row[0] == code), None)
        if default is None:
            raise ValidationError(
                f"Account {code} (mapped from '{mapping_key}') does not exist — "
                "create it or fix accounting.mapping"
            )
        # System accounts added in later releases are created on first use so
        # existing databases never fail a posting for want of a re-seed.
        account = Account(
            org_id=org_id, code=code, name=default[1], type=default[2], is_system=True
        )
        session.add(account)
        await session.flush()
    return account


async def resolve_payment_account(session: AsyncSession, org_id: uuid.UUID, method: str) -> Account:
    key = "cash" if method == "cash" else "bank"
    return await resolve_account(session, org_id, key)


async def post_entry(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    entry_date: date,
    memo: str,
    source_type: str,
    source_id: uuid.UUID | None = None,
    lines: list[tuple[Account, Decimal | str, Decimal | str]],
    actor_id: uuid.UUID | None = None,
) -> JournalEntry:
    """Post a balanced journal entry. lines: (account, debit, credit)."""
    normalized = []
    for account, debit, credit in lines:
        d = Decimal(str(debit))
        c = Decimal(str(credit))
        if d < 0 or c < 0:
            raise ValidationError("Journal amounts cannot be negative")
        if d > 0 and c > 0:
            raise ValidationError("A journal line cannot have both debit and credit")
        if d == 0 and c == 0:
            continue
        normalized.append((account, d.quantize(Decimal("0.01")), c.quantize(Decimal("0.01"))))
    total_debit = sum((d for _, d, _ in normalized), Decimal("0"))
    total_credit = sum((c for _, _, c in normalized), Decimal("0"))
    if total_debit != total_credit:
        raise ValidationError(f"Entry not balanced: debit {total_debit} != credit {total_credit}")
    if not normalized:
        raise ValidationError("Journal entry needs at least one line")

    prefix = await order_number_prefix(session, org_id, "journal_entry", "JE")
    number = await next_number(session, org_id, "journal_entry", prefix)
    entry = JournalEntry(
        org_id=org_id,
        number=number,
        entry_date=entry_date,
        memo=memo,
        source_type=source_type,
        source_id=source_id,
        created_by=actor_id,
    )
    entry.lines = [
        JournalLine(account_id=account.id, debit=debit, credit=credit)
        for account, debit, credit in normalized
    ]
    session.add(entry)
    await session.flush()
    return entry


async def reverse_source_entries(
    session: AsyncSession,
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    memo: str,
    entry_date: date | None = None,
    actor_id: uuid.UUID | None = None,
) -> JournalEntry | None:
    """Post one entry that exactly mirrors every journal entry booked for a
    source document (voids). Mirroring what was actually posted — rather than
    recomputing from the document — keeps reversals exact even when rates,
    mappings, or rounding rules changed in between."""
    originals = (
        await session.scalars(
            select(JournalEntry).where(
                JournalEntry.org_id == org_id,
                JournalEntry.source_id == source_id,
                JournalEntry.source_type != "reversal",
            )
        )
    ).all()
    lines: list[tuple[Account, Decimal, Decimal]] = []
    for entry in originals:
        for line in entry.lines:
            lines.append((line.account, Decimal(str(line.credit)), Decimal(str(line.debit))))
    if not lines:
        return None
    return await post_entry(
        session,
        org_id,
        entry_date=entry_date or date.today(),
        memo=memo,
        source_type="reversal",
        source_id=source_id,
        lines=lines,
        actor_id=actor_id,
    )
