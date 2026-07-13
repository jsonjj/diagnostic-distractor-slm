import unittest

from src.consistency import computation_consistent, eval_computation, to_display_value


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

    def test_v8_display_value_mode_handles_percentage_options(self):
        self.assertEqual(str(to_display_value("6%")), "6")
        self.assertIsNone(
            computation_consistent("0.6 * 10 = 6%", "6%", "Convert 0.6")
        )
        self.assertTrue(
            computation_consistent(
                "0.6 * 10 = 6%",
                "6%",
                "Convert 0.6",
                display_units=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
