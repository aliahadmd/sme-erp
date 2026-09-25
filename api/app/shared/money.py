"""Money and quantity helpers — Decimal end-to-end, NUMERIC in the database.

Never store monetary values in floats. All arithmetic helpers quantize with
banker's rounding (ROUND_HALF_EVEN).
"""

from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import Numeric

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 4)
UNIT_PRICE = Numeric(18, 6)

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")
_Q6 = Decimal("0.000001")


def money(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(_Q2, rounding=ROUND_HALF_EVEN)


def quantity(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(_Q4, rounding=ROUND_HALF_EVEN)


def price(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(_Q6, rounding=ROUND_HALF_EVEN)


def line_total(qty: Decimal, unit: Decimal, discount_pct: Decimal = Decimal("0")) -> Decimal:
    """Gross line total after a percentage discount, before tax."""
    factor = Decimal("1") - (Decimal(str(discount_pct)) / Decimal("100"))
    return money(Decimal(str(qty)) * Decimal(str(unit)) * factor)


def line_tax(base: Decimal, rate_pct: Decimal) -> Decimal:
    """Tax amount for a taxed base."""
    return money(Decimal(str(base)) * Decimal(str(rate_pct)) / Decimal("100"))
