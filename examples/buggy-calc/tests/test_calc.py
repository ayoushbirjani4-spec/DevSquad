import unittest

from calc import average, clamp


class CalcTests(unittest.TestCase):
    def test_average(self):
        self.assertEqual(average([1, 2, 3]), 2)

    def test_clamp(self):
        self.assertEqual(clamp(5, 0, 3), 3)
        self.assertEqual(clamp(-1, 0, 3), 0)


if __name__ == "__main__":
    unittest.main()
