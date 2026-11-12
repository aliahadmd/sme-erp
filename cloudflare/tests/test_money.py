from decimal import Decimal

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, insert, select

from erp_cf.money import coefficient, exact_sum, from_coefficient, money
from erp_cf.orm_types import ScaledDecimal


@pytest.mark.parametrize("scale", [1, 2, 4, 6, 8])
@pytest.mark.parametrize("sign", ["", "-"])
def test_full_precision_roundtrip(scale, sign):
    raw = sign + "999999999999999999"
    value = from_coefficient(raw, scale)
    assert coefficient(value, scale) == raw


@pytest.mark.parametrize("value", [0.1, True, "NaN", "Infinity", "invalid"])
def test_unsafe_or_invalid_values_rejected(value):
    with pytest.raises(ValueError):
        coefficient(value, 2)


def test_scale_and_range_are_enforced_before_cast():
    with pytest.raises(ValueError, match="scale"):
        coefficient("1.001", 2)
    with pytest.raises(ValueError, match="precision"):
        coefficient("10000000000000000.00", 2)
    with pytest.raises(ValueError, match="projected"):
        from_coefficient(999999999999999999, 2)


def test_exponent_input_is_bounded_without_losing_valid_zeroes():
    for value in ["1e999999999", "1e-999999999", "-9e999999999"]:
        with pytest.raises(ValueError):
            coefficient(value, 2)
    assert coefficient("0e999999999", 2) == "0"
    assert coefficient("1.000000", 2) == "100"


def test_bankers_rounding_and_large_aggregate():
    assert money("1.005") == Decimal("1.00")
    assert money("1.015") == Decimal("1.02")
    assert money("-1.005") == Decimal("-1.00")
    maximum = from_coefficient("999999999999999999", 2)
    assert exact_sum([maximum] * 20) == Decimal("199999999999999999.80")


def test_orm_type_preserves_extreme_values_and_select_expressions():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    table = Table("precision_probe", metadata, Column("amount", ScaledDecimal(2)))
    metadata.create_all(engine)
    value = Decimal("9999999999999999.99")
    with engine.begin() as connection:
        connection.execute(insert(table).values(amount=value))
        assert connection.scalar(select(table.c.amount)) == value
        stored_type = connection.exec_driver_sql(
            "SELECT typeof(amount) FROM precision_probe"
        ).scalar()
        assert stored_type == "integer"
    assert "CAST" in str(select(table.c.amount))
