import unittest

from src.consistency import eval_computation


class ComputationResourceLimitTests(unittest.TestCase):
    def test_rejects_extreme_exponents_before_building_huge_integers(self):
        result = eval_computation("2^100000 = 0")

        self.assertTrue(result is None)

    def test_rejects_oversized_numeric_tokens_and_excessive_nesting(self):
        huge_number = "9" * 100
        deep_expression = "(" * 40 + "1 + 1" + ")" * 40

        self.assertTrue(eval_computation(huge_number) is None)
        self.assertTrue(eval_computation(deep_expression) is None)

    def test_keeps_normal_sixth_grade_exact_arithmetic(self):
        self.assertEqual(str(eval_computation("(5 + 1) / (8 + 4) = 1/2")), "1/2")
        self.assertEqual(str(eval_computation("2^5 + 4 * 3 = 44")), "44")


if __name__ == "__main__":
    unittest.main()
