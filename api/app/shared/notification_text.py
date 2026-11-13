"""Portable email address validation and invoice text; no delivery side effects."""

from email_validator import EmailNotValidError, validate_email


def party_email(contact) -> str | None:  # noqa: ANN001
    """First syntactically valid email of a contact (no header injection:
    CR/LF or malformed values are rejected by the validator)."""
    if contact is None or not contact.emails:
        return None
    for entry in contact.emails:
        value = entry.get("value") if isinstance(entry, dict) else entry
        if not isinstance(value, str):
            continue
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized
        except EmailNotValidError:
            continue
    return None


def document_email(invoice, company: str) -> tuple[str, str]:  # noqa: ANN001
    """(subject, body) for a customer invoice / credit note email."""
    label = "Credit note" if invoice.invoice_type.endswith("_credit") else "Invoice"
    subject = f"{label} {invoice.number} from {company} — {invoice.total} {invoice.currency}"
    due = f"\nDue date: {invoice.due_date}" if invoice.due_date else ""
    body = (
        f"Hello {invoice.party_name or ''},\n\n"
        f"{label} {invoice.number} dated {invoice.invoice_date} "
        f"for {invoice.total} {invoice.currency} has been issued.{due}\n\n"
        f"Kind regards,\n{company}"
    )
    return subject, body
