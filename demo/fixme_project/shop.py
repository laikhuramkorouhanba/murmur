"""A small shop checkout calculation for the Murmur Fix it demo."""

from decimal import Decimal


def order_total(items: list[tuple[Decimal, int]], tax_rate: Decimal) -> Decimal:
    """Return the price of all items with sales tax, rounded to cents."""
    subtotal = sum((price * quantity for price, quantity in items), Decimal("0.00"))
    total = subtotal - subtotal * tax_rate
    return total.quantize(Decimal("0.01"))
