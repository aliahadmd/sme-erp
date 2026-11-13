"""Quotation lifecycle — its own state machine, built on the shared helpers."""

from datetime import date

from app.core.errors import ConflictError, ValidationError
from app.modules.sales.models import Quotation
from app.shared.document_states import QUOTE_TRANSITIONS as QUOTE_TRANSITIONS

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

    for line in quotation.lines:
        apply_line_math(line)
    from app.shared.totals import compute_totals

    totals = compute_totals(
        [
            (line.qty, line.unit_price, line.discount_pct, line.tax_rate_pct)
            for line in quotation.lines
        ],
        round_gross_per_line=False,
    )
    quotation.subtotal = totals.subtotal
    quotation.discount_total = totals.discount_total
    quotation.tax_total = totals.tax_total
    quotation.total = totals.total


def check_expiry(quotation: Quotation, today: date) -> None:
    """Lazily mark sent quotes past their valid_until date."""
    if quotation.status == "sent" and quotation.valid_until and quotation.valid_until < today:
        quotation.status = "expired"
