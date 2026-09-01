from decimal import Decimal
import unittest

from order_pricing import calculate_total


class OrderPricingTest(unittest.TestCase):
    def test_below_threshold_has_no_discount(self):
        self.assertEqual(
            calculate_total(Decimal("99"), Decimal("100"), Decimal("0.10")),
            Decimal("99"),
        )

    def test_above_threshold_has_discount(self):
        self.assertEqual(
            calculate_total(Decimal("110"), Decimal("100"), Decimal("0.10")),
            Decimal("99.00"),
        )

    def test_discount_threshold_is_inclusive(self):
        self.assertEqual(
            calculate_total(Decimal("100"), Decimal("100"), Decimal("0.10")),
            Decimal("90.00"),
        )
