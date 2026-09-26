"""Quotation lifecycle — its own state machine, built on the shared helpers."""

from datetime import date
from decimal import Decimal

from app.core.errors import ConflictError, ValidationError
from app.modules.sales.models import Quotation

QUOTE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"sent", "cancelled"},
    "sent": {"accepted", "rejected", "cancelled", "expired"},
    "accepted": {"converted", "cancelled"},
    "converted": set(),
    "rejected": set(),
    "expired": set(),
    "cancelled": set(),
}

EDITABLE = {"draft"}


def ensure_transition(current: str, target: str) -> None:
    if target not in QUOTE_TRANSITIONS.get(current, set()):
        raise ConflictError(f"Cannot move quotation from '{current}' to '{target}'")


def ensure_editable(quotation: Quotation) -> None:
    if quotation.status != "draft":
        raise ConflictError(f"Only draft quotations can be edited (status: {quotation.status})")


def validate_line(line: Quotation) -> None:
    if line.qty <= 0:
        raise ValidationError("Quotation line quantity must be positive")
    if line.line_total < 0:
        raise ValidationError("Quotation line total cannot be negative")


def recompute(quotation: Quotation) -> None:
    """Recompute line + header totals (single money engine)."""
    from app.shared.order_engine import apply_line_math

    subtotal = Decimal("0")
    discounted = Decimal("0")
    tax = Decimal("0")
    for line in quotation.lines:
        math = apply_line_math(line)
        subtotal += Decimal(str(line.qty)) * Decimal(str(line.unit_price))
        discounted += math.base
        tax += math.tax
    from app.shared.money import money

    quotation.subtotal = money(subtotal)
    quotation.discount_total = money(subtotal - discounted)
    quotation.tax_total = money(tax)
    quotation.total = money(discounted + tax)


def check_expiry(quotation: Quotation, today: date) -> None:
    """Lazily mark sent quotes past their valid_until date."""
    if quotation.status == "sent" and quotation.valid_until and quotation.valid_until < today:
        quotation.status = "expired"
