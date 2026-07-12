from fractions import Fraction
import unittest

from services.wayline_forge.app.safe_numeric import (
    ExactValue,
    NumericParseError,
    format_fraction,
    parse_exact_value,
)


class SafeNumericTests(unittest.TestCase):
    def test_parses_decimal_without_float_rounding(self):
        parsed = parse_exact_value("0.29")
        self.assertEqual(parsed, ExactValue(Fraction(29, 100), "0.29"))

    def test_parses_reduced_fraction_exactly(self):
        parsed = parse_exact_value(" 6/14 ")
        self.assertEqual(parsed.value, Fraction(3, 7))
        self.assertEqual(parsed.display, "3/7")

    def test_percent_requires_explicit_permission_and_keeps_display_magnitude(self):
        with self.assertRaises(NumericParseError):
            parse_exact_value("60%")
        parsed = parse_exact_value("60%", allow_percent=True)
        self.assertEqual(parsed.value, Fraction(60))
        self.assertEqual(parsed.display, "60%")

    def test_formats_only_terminating_fractions_as_decimals(self):
        self.assertEqual(format_fraction(Fraction(1, 8)), "0.125")
        self.assertEqual(format_fraction(Fraction(-7, 2)), "-3.5")
        self.assertEqual(format_fraction(Fraction(2, 3)), "2/3")
        self.assertEqual(format_fraction(Fraction(8, 4)), "2")

    def test_rejects_unsafe_or_ambiguous_syntax(self):
        rejected = (
            "",
            "NaN",
            "Infinity",
            "1e9",
            "2^8",
            "1 + 2",
            "1/2/3",
            "1 / 2",
            "1,000",
            ".5",
            "5.",
            "--2",
            "<b>2</b>",
            "12\u0000",
            "½",
        )
        for text in rejected:
            with self.subTest(text=text), self.assertRaises(NumericParseError):
                parse_exact_value(text)

    def test_rejects_zero_denominator_and_boundedness_violations(self):
        for text in ("1/0", "1234567890123456789", "1000000000001"):
            with self.subTest(text=text), self.assertRaises(NumericParseError):
                parse_exact_value(text)


if __name__ == "__main__":
    unittest.main()
