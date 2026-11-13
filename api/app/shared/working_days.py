"""Inclusive Monday-Friday counts split by year; public holidays are unmodeled."""

from datetime import date
from decimal import Decimal


def working_days_by_year(date_from: date, date_to: date) -> dict[int, Decimal]:
    days = {}
    for year in range(date_from.year, date_to.year + 1):
        first, last = max(date_from, date(year, 1, 1)), min(date_to, date(year, 12, 31))
        if first > last:
            continue
        weeks, remainder = divmod((last - first).days + 1, 7)
        count = weeks * 5 + sum((first.weekday() + i) % 7 < 5 for i in range(remainder))
        if count:
            days[year] = Decimal(count)
    return days
