from django.test import TestCase
from dining_alliance.finance import commission_fen
from dining_alliance.access import DiningError


class CommissionTests(TestCase):
    def test_quantity_and_profit_ratio(self):
        self.assertEqual(commission_fen(20000, 6000, 2, 2000), 1600)

    def test_invalid_or_negative_profit_held(self):
        for args in [(100, 60, 2, 2000), (100, 60, True, 2000), (100, 60, 1, 10001)]:
            with self.assertRaises(DiningError):
                commission_fen(*args)
