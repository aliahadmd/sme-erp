"""Money and quantity helpers — Decimal end-to-end, NUMERIC in the database.

Never store monetary values in floats. All arithmetic helpers quantize with
banker's rounding (ROUND_HALF_EVEN).
"""

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated

from pydantic import BeforeValidator, StringConstraints

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")
_Q6 = Decimal("0.000001")


def __getattr__(name: str):
    # Preserve the old server's type aliases without importing its database
    # library when Workers only needs Decimal helpers or CurrencyCode.
    scales = {"MONEY": 2, "QUANTITY": 4, "UNIT_PRICE": 6}
    if name in scales:
        from sqlalchemy import Numeric

        return Numeric(18, scales[name])
    raise AttributeError(name)


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


def _upper_code(value: str) -> str:
    return value.strip().upper()


# ISO 4217-style code: 3 letters, normalized to upper case (so "eur" and "EUR"
# resolve to the same FX rates). Use for every currency field in schemas.
CurrencyCode = Annotated[
    str, BeforeValidator(_upper_code), StringConstraints(pattern=r"^[A-Z]{3}$")
]
