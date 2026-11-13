"""Consistent invoice/payment views and exact statements across currencies."""

from decimal import localcontext
from types import SimpleNamespace

from app.modules.invoicing import schemas

from erp_cf.db import Statement, plain
from erp_cf.document_reads import read_documents
from erp_cf.errors import NotFoundError
from erp_cf.money import from_coefficient, money
from erp_cf.settlement import ALLOCATIONS, INVOICES, PAYMENTS, ZERO, balance, credit
from erp_cf.store import tables

INVOICE = SimpleNamespace(
    table=INVOICES,
    lines="invoicing_invoice_lines",
    parent="invoice_id",
    label="invoice",
    party="party_id",
)
PAYMENT = SimpleNamespace(
    table=PAYMENTS, lines=ALLOCATIONS, parent="payment_id", label="payment", party="party_id"
)


async def invoice_page(db, org_id, **options):
    return await read_documents(db, org_id, INVOICE, **options)


async def payment_page(db, org_id, **options):
    page = await read_documents(db, org_id, PAYMENT, **options)
    for item in page["items"]:
        item["allocations"] = item.pop("lines")
    return page


async def statement(db, org_id, party_id):
    party_id = str(party_id)
    contacts, invoices, payments, allocations, orgs = [
        tables()[name]
        for name in ["crm_contacts", INVOICES, PAYMENTS, ALLOCATIONS, "core_organizations"]
    ]
    results = await db.batch(
        [
            Statement(
                f"SELECT {contacts.projection()} FROM crm_contacts WHERE id=? AND org_id=?",
                (party_id, org_id),
            ),
            Statement(
                f"SELECT {invoices.projection()} FROM invoicing_invoices "
                "WHERE org_id=? AND party_id=? AND status IN ('posted','partial') "
                "ORDER BY invoice_date,id",
                (org_id, party_id),
            ),
            Statement(
                f"SELECT {payments.projection()} FROM invoicing_payments "
                "WHERE org_id=? AND party_id=? AND status='recorded' AND credit_note_id IS NULL",
                (org_id, party_id),
            ),
            Statement(
                f"SELECT {allocations.projection('a')} FROM invoicing_payment_allocations a "
                "JOIN invoicing_payments p ON p.id=a.payment_id "
                "WHERE p.org_id=? AND p.party_id=? AND p.status='recorded' "
                "AND p.credit_note_id IS NULL",
                (org_id, party_id),
            ),
            Statement(
                "SELECT e.invoice_id,CAST(e.amount_base AS TEXT) AS amount_base "
                "FROM cf_settlement_effects e JOIN invoicing_invoices i ON i.id=e.invoice_id "
                "LEFT JOIN invoicing_payments p ON e.source_kind='payment' AND p.id=e.source_id "
                "LEFT JOIN invoicing_invoices c ON e.source_kind='credit' AND c.id=e.source_id "
                "WHERE e.org_id=? AND i.party_id=? AND i.status IN ('posted','partial') "
                "AND (p.status='recorded' OR c.status IN ('posted','partial','paid'))",
                (org_id, party_id),
            ),
            Statement(f"SELECT {orgs.projection()} FROM core_organizations WHERE id=?", (org_id,)),
        ]
    )
    if not results[0]["results"]:
        raise NotFoundError("Contact not found")
    party = contacts.decode(plain(results[0]["results"][0]))
    org = orgs.decode(plain(results[5]["results"][0]))
    rows = [invoices.decode(plain(r)) for r in results[1]["results"]]
    pay_rows = [payments.decode(plain(r)) for r in results[2]["results"]]
    with localcontext() as context:
        context.prec = 80
        paid, used, by_currency, unapplied = {}, {}, {}, {}
        for record in results[3]["results"]:
            allocation = allocations.decode(plain(record))
            paid[allocation["payment_id"]] = (
                paid.get(allocation["payment_id"], ZERO) + allocation["amount"]
            )
        for record in results[4]["results"]:
            record = plain(record)
            used[record["invoice_id"]] = used.get(record["invoice_id"], ZERO) + from_coefficient(
                record["amount_base"], 2
            )
        lines, base_total, base_unapplied = [], ZERO, ZERO
        for row in rows:
            amount = balance(row)
            base = row["total_base"] - used.get(row["id"], ZERO)
            if credit(row):
                amount, base = -amount, -base
            base_total += base
            by_currency[row["currency"]] = by_currency.get(row["currency"], ZERO) + amount
            lines.append(
                schemas.StatementLine(
                    invoice_id=row["id"],
                    invoice_type=row["invoice_type"],
                    number=row["number"],
                    invoice_date=row["invoice_date"],
                    due_date=row["due_date"],
                    total=row["total"],
                    total_base=row["total_base"],
                    amount_paid=row["amount_paid"],
                    balance=amount,
                    status=row["status"],
                    currency=row["currency"],
                    balance_base=base,
                )
            )
        for row in pay_rows:
            allocated = paid.get(row["id"], ZERO)
            amount = row["amount"] - allocated
            base_unapplied += row["amount_base"] - money(allocated / row["fx_rate"])
            unapplied[row["currency"]] = unapplied.get(row["currency"], ZERO) + amount
            by_currency[row["currency"]] = by_currency.get(row["currency"], ZERO) - amount
        base_total = money(base_total - base_unapplied)
        currency = next(iter(by_currency)) if len(by_currency) == 1 else org["base_currency"]
        return schemas.StatementOut(
            party_id=party_id,
            party_name=party["name"],
            currency=currency,
            base_currency=org["base_currency"],
            base_open_balance=base_total,
            open_balance=money(next(iter(by_currency.values())))
            if len(by_currency) == 1
            else base_total,
            unapplied_payments=money(sum(unapplied.values(), ZERO))
            if len(by_currency) <= 1
            else money(base_unapplied),
            balances_by_currency=by_currency,
            unapplied_by_currency=unapplied,
            invoices=lines,
        )
