from decimal import Decimal


def calculate_total(
    subtotal: Decimal,
    discount_threshold: Decimal,
    discount_rate: Decimal,
) -> Decimal:
    """Return the order total after applying a threshold discount."""
    if subtotal > discount_threshold:
        return subtotal * (Decimal("1") - discount_rate)
    return subtotal
