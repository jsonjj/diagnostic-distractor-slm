"""Bounded exact-number parsing for learner-facing model output.

Only a single integer, decimal, or fraction is accepted.  This module deliberately
does not evaluate expressions: computations are rendered by the product registry,
never trusted from model text.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import re
import unicodedata


MAX_DIGITS = 18
MAX_ABSOLUTE = Fraction(10**12)

_INTEGER = re.compile(r"[+-]?[0-9]+", re.ASCII)
_DECIMAL = re.compile(r"([+-]?[0-9]+)\.([0-9]+)", re.ASCII)
_FRACTION = re.compile(r"([+-]?[0-9]+)/([0-9]+)", re.ASCII)


class NumericParseError(ValueError):
    """Raised when text is not one bounded, supported exact number."""


@dataclass(frozen=True, slots=True)
class ExactValue:
    value: Fraction
    display: str


def _has_control(text: str) -> bool:
    return any(unicodedata.category(char).startswith("C") for char in text)


def _check_digit_bound(*parts: str) -> None:
    if any(len(part.lstrip("+-")) > MAX_DIGITS for part in parts):
        raise NumericParseError("numeric token exceeds the digit limit")


def _check_value_bound(value: Fraction) -> None:
    if abs(value) > MAX_ABSOLUTE:
        raise NumericParseError("numeric value exceeds the magnitude limit")


def format_fraction(value: Fraction) -> str:
    """Return a canonical integer, terminating decimal, or reduced fraction."""

    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)

    remaining = value.denominator
    while remaining % 2 == 0:
        remaining //= 2
    while remaining % 5 == 0:
        remaining //= 5
    if remaining != 1:
        return f"{value.numerator}/{value.denominator}"

    negative = value.numerator < 0
    numerator = abs(value.numerator)
    whole, remainder = divmod(numerator, value.denominator)
    digits: list[str] = []
    while remainder:
        remainder *= 10
        digit, remainder = divmod(remainder, value.denominator)
        digits.append(str(digit))
    sign = "-" if negative else ""
    return f"{sign}{whole}.{''.join(digits)}"


def parse_exact_value(text: str, *, allow_percent: bool = False) -> ExactValue:
    """Parse one bounded exact display number without floats or expression eval.

    A percent suffix represents the displayed percentage magnitude: ``60%`` has
    value ``Fraction(60)`` here.  The owning question supplies the percent unit.
    """

    if not isinstance(text, str):
        raise NumericParseError("numeric value must be text")
    if _has_control(text):
        raise NumericParseError("control characters are not allowed")
    token = text.strip()
    if not token:
        raise NumericParseError("numeric value is empty")

    percent = token.endswith("%")
    if percent:
        if not allow_percent:
            raise NumericParseError("percent suffix is not allowed here")
        token = token[:-1]
        if not token:
            raise NumericParseError("percentage is missing a number")

    fraction_match = _FRACTION.fullmatch(token)
    decimal_match = _DECIMAL.fullmatch(token)
    if fraction_match:
        numerator_text, denominator_text = fraction_match.groups()
        _check_digit_bound(numerator_text, denominator_text)
        denominator = int(denominator_text)
        if denominator == 0:
            raise NumericParseError("fraction denominator cannot be zero")
        value = Fraction(int(numerator_text), denominator)
    elif decimal_match:
        whole_text, decimal_text = decimal_match.groups()
        _check_digit_bound(whole_text, decimal_text)
        sign = -1 if whole_text.startswith("-") else 1
        whole = abs(int(whole_text))
        value = Fraction(sign * (whole * (10 ** len(decimal_text)) + int(decimal_text)), 10 ** len(decimal_text))
    elif _INTEGER.fullmatch(token):
        _check_digit_bound(token)
        value = Fraction(int(token))
    else:
        raise NumericParseError("unsupported numeric syntax")

    _check_value_bound(value)
    display = format_fraction(value) + ("%" if percent else "")
    return ExactValue(value=value, display=display)
