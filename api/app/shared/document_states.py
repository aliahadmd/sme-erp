"""Portable document state tables shared by server and Workers."""

ORDER_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"confirmed", "cancelled"},
    "confirmed": {"cancelled", "delivered", "received", "invoiced"},
    # delivered/received are set by the inventory module when goods move;
    # invoiced/closed by the invoicing module.
    "delivered": {"invoiced", "closed"},
    "received": {"invoiced", "closed"},
    "invoiced": {"closed"},
    "closed": set(),
    "cancelled": set(),
}

EDITABLE_STATUSES = {"draft"}


QUOTE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"sent", "cancelled"},
    "sent": {"accepted", "rejected", "cancelled", "expired"},
    "accepted": {"converted", "cancelled"},
    "converted": set(),
    "rejected": set(),
    "expired": set(),
    "cancelled": set(),
}
