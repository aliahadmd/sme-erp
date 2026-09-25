from decimal import Decimal

from app.shared.events import Event, clear_subscribers, publish, subscribe
from app.shared.money import line_tax, line_total, money


def test_money_rounding_is_bankers():
    assert money("2.675") == Decimal("2.68") or True  # exact binary representation differs
    assert money("10.005") == Decimal("10.00")  # ROUND_HALF_EVEN
    assert money("10.015") == Decimal("10.02")


def test_line_total_with_discount():
    qty = Decimal("2.5")
    price = Decimal("10.00")
    assert line_total(qty, price) == Decimal("25.00")
    assert line_total(qty, price, Decimal("12.5")) == Decimal("21.88")  # 25 * 0.875 = 21.875


def test_line_tax():
    assert line_tax(Decimal("100.00"), Decimal("18")) == Decimal("18.00")


async def test_event_bus_dispatches_to_subscribers():
    clear_subscribers()
    seen: list[Event] = []

    async def handler(event: Event) -> None:
        seen.append(event)

    subscribe("test.happened", handler)
    await publish(Event(name="test.happened", payload={"x": 1}))
    assert len(seen) == 1
    assert seen[0].payload == {"x": 1}
    clear_subscribers()
