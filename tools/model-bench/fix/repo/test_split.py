import unittest
from budget.split import split_bill

class Split(unittest.TestCase):
    def test_even(self):
        self.assertEqual(split_bill(30, 3), [10.0, 10.0, 10.0])
    def test_shares_add_up_to_the_total(self):
        shares = split_bill(100, 3)
        self.assertAlmostEqual(sum(shares), 100.00, places=2)
        self.assertEqual(len(shares), 3)
    def test_tip(self):
        self.assertAlmostEqual(sum(split_bill(50, 4, tip_percent=20)), 60.00, places=2)

if __name__ == "__main__":
    unittest.main()
