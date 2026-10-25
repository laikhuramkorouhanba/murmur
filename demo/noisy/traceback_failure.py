#!/usr/bin/env python3
"""Raise a real KeyError beneath a pytest-style failure header."""

import sys


def load_tax_policy() -> dict[str, float | str]:
    policy = {"currency": "EUR", "tax_percent": 20.0}
    return policy["tax_rate"]


def calculate_tax(subtotal: float) -> float:
    policy = load_tax_policy()
    return subtotal * float(policy["tax_rate"]) / 100


def total_for_lines(lines: list[dict[str, float]]) -> float:
    subtotal = sum(line["quantity"] * line["unit_price"] for line in lines)
    return subtotal + calculate_tax(subtotal)


def build_receipt(order: dict[str, object]) -> dict[str, float | str]:
    lines = order["lines"]
    total = total_for_lines(lines)  # type: ignore[arg-type]
    return {"order_id": str(order["id"]), "total": total}


def test_checkout_total_includes_tax() -> None:
    order: dict[str, object] = {
        "id": "ORD-1048",
        "lines": [
            {"quantity": 2, "unit_price": 8.50},
            {"quantity": 1, "unit_price": 4.20},
        ],
    }
    receipt = build_receipt(order)
    assert receipt["total"] == 25.44


def main() -> None:
    sys.stderr.write(
        "============================= test session starts ==============================\n"
        "platform darwin -- Python 3.13.5, pytest-8.4.1, pluggy-1.5.0\n"
        "rootdir: demo/noisy\n"
        "collected 1 item\n\n"
        "tests/test_checkout.py F                                                   [100%]\n"
        "=================================== FAILURES ===================================\n"
        "________________________ test_checkout_total_includes_tax ________________________\n"
    )
    test_checkout_total_includes_tax()


if __name__ == "__main__":
    main()
