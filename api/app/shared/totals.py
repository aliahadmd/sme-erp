"""Document totals engine — the single implementation used by orders,
invoices, and accounting previews.

Money rules: quantities 4dp, unit prices 6dp, money 2dp, banker's rounding.
Line math: base = qty × price × (1 − discount%); tax = base × rate%; total = base + tax.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.shared.money import line_tax, line_total, money


@dataclass(frozen=True)
class LineMath:
    base: Decimal  # after discount, before tax
    tax: Decimal
    total: Decimal


def compute_line(
    qty: Decimal | str,
    unit_price: Decimal | str,
    discount_pct: Decimal | str = Decimal("0"),
    tax_rate_pct: Decimal | str = Decimal("0"),
) -> LineMath:
    base = line_total(qty, unit_price, discount_pct)
    tax = line_tax(base, Decimal(str(tax_rate_pct)))
    return LineMath(base=base, tax=tax, total=base + tax)


@dataclass(frozen=True)
class Totals:
    subtotal: Decimal  # sum of line bases before discounts
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal


def compute_totals(
    lines: list[tuple[Decimal | str, Decimal | str, Decimal | str, Decimal | str]],
) -> Totals:
    """lines: (qty, unit_price, discount_pct, tax_rate_pct) tuples."""
    subtotal = Decimal("0.00")
    after_discount = Decimal("0.00")
    tax_total = Decimal("0.00")
    for qty, price, discount_pct, tax_rate in lines:
        gross = line_total(qty, price, Decimal("0"))
        line = compute_line(qty, price, discount_pct, tax_rate)
        subtotal += gross
        after_discount += line.base
        tax_total += line.tax
    return Totals(
        subtotal=money(subtotal),
        discount_total=money(subtotal - after_discount),
        tax_total=money(tax_total),
        total=money(after_discount + tax_total),
    )
