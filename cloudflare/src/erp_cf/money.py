"""Exact decimal calculations and lossless D1 integer transport.

D1's JavaScript boundary cannot safely transport arbitrary int64 coefficients
as Numbers. Bind coefficients as strings with an explicit INTEGER cast, and
project stored integers as TEXT. Never send a Python float through this path.
"""

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def decimal_value(value: str | int | Decimal) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Exact values must be decimal strings, integers, or Decimal")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal value") from exc
    if not result.is_finite():
        raise ValueError("Decimal value must be finite")
    return result


def quantize(value: str | int | Decimal, scale: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        return decimal_value(value).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN)


def coefficient(value: str | int | Decimal, scale: int, precision: int = 18) -> str:
    """Encode a bounded exact coefficient, rejecting excess precision or scale."""
    if not 0 <= scale <= precision <= 18:
        raise ValueError("Invalid decimal precision/scale")
    number = decimal_value(value)
    if not number:
        return "0"
    # Bound exponent notation before scaleb/int conversion. A short input such
    # as '1e999999999' must fail validation rather than exhaust the context.
    adjusted = number.adjusted() + scale
    if adjusted >= precision:
        raise ValueError(f"Value exceeds precision {precision}")
    if adjusted < 0:
        raise ValueError(f"Value exceeds scale {scale}")
    with localcontext() as ctx:
        ctx.prec = 80
        scaled = number.scaleb(scale)
        if scaled != scaled.to_integral_value():
            raise ValueError(f"Value exceeds scale {scale}")
        if abs(scaled) >= 10**precision:
            raise ValueError(f"Value exceeds precision {precision}")
        integer = int(scaled)
    if not INT64_MIN <= integer <= INT64_MAX:
        raise ValueError("Value exceeds signed 64-bit storage")
    return str(integer)


def from_coefficient(value: str, scale: int) -> Decimal:
    """Decode a TEXT projection; numeric results indicate an unsafe adapter path."""
    if not isinstance(value, str) or not INTEGER.fullmatch(value):
        raise ValueError("D1 financial coefficients must be projected as integer text")
    integer = int(value)
    if not INT64_MIN <= integer <= INT64_MAX:
        raise ValueError("Value exceeds signed 64-bit storage")
    with localcontext() as ctx:
        ctx.prec = 80
        return Decimal(integer).scaleb(-scale)


def money(value: str | int | Decimal) -> Decimal:
    return quantize(value, 2)


def exact_sum(values: list[Decimal]) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        return sum(values, Decimal(0))
