"""SQLAlchemy type whose bind and result expressions preserve int64 precision."""

from decimal import Decimal

from sqlalchemy import BigInteger, String, cast, type_coerce
from sqlalchemy.types import TypeDecorator

from erp_cf.money import coefficient, from_coefficient


class ScaledDecimal(TypeDecorator):
    impl = BigInteger
    cache_ok = True

    def __init__(self, scale: int, precision: int = 18):
        super().__init__()
        self.scale = scale
        self.precision = precision

    def process_bind_param(self, value, dialect):
        return None if value is None else coefficient(value, self.scale, self.precision)

    def bind_expression(self, bindvalue):
        return cast(bindvalue, BigInteger)

    def column_expression(self, column):
        return type_coerce(cast(column, String), self)

    def process_result_value(self, value, dialect):
        return None if value is None else from_coefficient(value, self.scale)

    @property
    def python_type(self):
        return Decimal
