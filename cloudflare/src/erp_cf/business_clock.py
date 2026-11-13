"""Explicit fixed-offset business calendar for the current Shanghai deployment."""

from datetime import UTC, datetime, timedelta, timezone


def business_time(env, scheduled_ms=None):
    offset = int(getattr(env, "BUSINESS_UTC_OFFSET_MINUTES", "0"))
    if not -720 <= offset <= 840:
        raise ValueError("Invalid business UTC offset")
    instant = (
        datetime.now(UTC)
        if scheduled_ms is None
        else datetime.fromtimestamp(int(scheduled_ms) / 1000, UTC)
    )
    return instant.astimezone(timezone(timedelta(minutes=offset)))
