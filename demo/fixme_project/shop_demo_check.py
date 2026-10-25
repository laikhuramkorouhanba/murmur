from decimal import Decimal

from shop import order_total


def test_order_total_adds_sales_tax() -> None:
    items = [(Decimal("20.00"), 1)]

    assert order_total(items, Decimal("0.08")) == Decimal("21.60")
