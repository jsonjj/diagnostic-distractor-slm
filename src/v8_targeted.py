"""Executable v8 synthetic families for legacy-development-set failure topics.

These templates use new operands and never copy a held-out question. Every target is
checked with the same hardened arithmetic gate used at evaluation.
"""
from __future__ import annotations

import math
import random
from fractions import Fraction
from typing import Callable

from .buggy_procedures import fmt
from .consistency import computation_consistent
from .prompts import SYSTEM_PROMPT, build_assistant, build_user
from .text_utils import normalize_answer


def _delta_computation(source: int, target: int) -> str:
    if target >= source:
        return f"{source} + {target - source} = {target}"
    return f"{source} - {source - target} = {target}"


def _example(
    *,
    family: str,
    topic: str,
    question: str,
    correct,
    distractors: list[tuple[str, str, object]],
) -> dict:
    values = [
        {
            "misconception": misconception,
            "computation": computation,
            "answer": normalize_answer(fmt(answer)),
        }
        for misconception, computation, answer in distractors
    ]
    return {
        "family": family,
        "topic": topic,
        "question": question,
        "correct": normalize_answer(fmt(correct)),
        "distractors": values,
    }


def _equivalent_fraction(r: random.Random) -> dict:
    n, d, k = r.randint(1, 8), r.randint(2, 12), r.randint(2, 6)
    return _example(
        family="equivalent_fraction_v8",
        topic="Equivalent Fractions",
        question=f"Complete {n}/{d} = ?/{d * k}. What number replaces ?",
        correct=n * k,
        distractors=[
            ("Leaves the numerator unchanged", f"{n} * 1 = {n}", n),
            ("Adds the scale factor instead of multiplying", f"{n} + {k} = {n + k}", n + k),
            (
                "Applies the denominator scale factor twice to the numerator",
                f"{n} * {k} * {k} = {n * k * k}",
                n * k * k,
            ),
        ],
    )


def _standard_form(r: random.Random) -> dict:
    coefficient, exponent = r.randint(2, 99), r.randint(1, 6)
    correct = coefficient * (10**exponent)
    return _example(
        family="standard_form_v8",
        topic="Standard Form",
        question=(
            f"Write {coefficient} × 10^{exponent} as an ordinary number."
        ),
        correct=correct,
        distractors=[
            (
                "Uses an exponent one too small",
                f"{coefficient} * 10^({exponent} - 1) = {coefficient * (10 ** (exponent - 1))}",
                coefficient * (10 ** (exponent - 1)),
            ),
            (
                "Uses an exponent one too large",
                f"{coefficient} * 10^({exponent} + 1) = {coefficient * (10 ** (exponent + 1))}",
                coefficient * (10 ** (exponent + 1)),
            ),
            (
                "Multiplies by the exponent instead of a power of ten",
                f"{coefficient} * {exponent} = {coefficient * exponent}",
                coefficient * exponent,
            ),
        ],
    )


def _round_significant_figures(r: random.Random) -> dict:
    number = r.randint(1100, 9899)
    correct = ((number + 50) // 100) * 100
    one_sf = ((number + 500) // 1000) * 1000
    three_sf = ((number + 5) // 10) * 10
    truncate = (number // 100) * 100
    return _example(
        family="round_significant_figures_v8",
        topic="Rounding to Significant Figures",
        question=f"Round {number} to 2 significant figures.",
        correct=correct,
        distractors=[
            (
                "Rounds to 1 significant figure instead of 2",
                _delta_computation(number, one_sf),
                one_sf,
            ),
            (
                "Rounds to 3 significant figures instead of 2",
                _delta_computation(number, three_sf),
                three_sf,
            ),
            (
                "Truncates after 2 significant figures instead of rounding",
                _delta_computation(number, truncate),
                truncate,
            ),
        ],
    )


def _mixed_to_improper(r: random.Random) -> dict:
    whole, denominator = r.randint(2, 8), r.randint(3, 9)
    numerator = r.randint(1, denominator - 1)
    correct = Fraction(whole * denominator + numerator, denominator)
    return _example(
        family="mixed_to_improper_v8",
        topic="Converting Mixed Number and Improper Fractions",
        question=(
            f"Convert {whole} {numerator}/{denominator} to an improper fraction."
        ),
        correct=correct,
        distractors=[
            (
                "Adds the whole number to the numerator without scaling",
                f"({whole} + {numerator}) / {denominator} = {fmt(Fraction(whole + numerator, denominator))}",
                Fraction(whole + numerator, denominator),
            ),
            (
                "Drops the fractional numerator",
                f"({whole} * {denominator}) / {denominator} = {whole}",
                whole,
            ),
            (
                "Subtracts the numerator after scaling the whole number",
                f"({whole} * {denominator} - {numerator}) / {denominator} = {fmt(Fraction(whole * denominator - numerator, denominator))}",
                Fraction(whole * denominator - numerator, denominator),
            ),
        ],
    )


def _negative_multiplication(r: random.Random) -> dict:
    a, b = r.randint(2, 15), r.randint(2, 12)
    return _example(
        family="negative_multiplication_v8",
        topic="Multiplying and Dividing Negative Numbers",
        question=f"What is -{a} × {b}?",
        correct=-(a * b),
        distractors=[
            (
                "Ignores the negative sign",
                f"{a} * {b} = {a * b}",
                a * b,
            ),
            (
                "Subtracts the factors instead of multiplying",
                f"-{a} - {b} = {-a - b}",
                -a - b,
            ),
            (
                "Subtracts the second factor from the unsigned first factor",
                f"{a} - {b} = {a - b}",
                a - b,
            ),
        ],
    )


def _fraction_to_decimal(r: random.Random) -> dict:
    denominator = r.choice(
        [2, 4, 5, 8, 10, 16, 20, 25, 32, 40, 50, 64, 80, 100]
    )
    numerator = r.randint(1, denominator - 1)
    return _example(
        family="fraction_to_decimal_v8",
        topic="Converting between Fractions and Decimals",
        question=f"Convert {numerator}/{denominator} to a decimal.",
        correct=Fraction(numerator, denominator),
        distractors=[
            (
                "Divides in the reverse order",
                f"{denominator} / {numerator} = {fmt(Fraction(denominator, numerator))}",
                Fraction(denominator, numerator),
            ),
            (
                "Multiplies numerator and denominator",
                f"{numerator} * {denominator} = {numerator * denominator}",
                numerator * denominator,
            ),
            (
                "Introduces an extra factor of ten in the denominator",
                f"{numerator} / ({denominator} * 10) = {fmt(Fraction(numerator, denominator * 10))}",
                Fraction(numerator, denominator * 10),
            ),
        ],
    )


def _fraction_to_percentage(r: random.Random) -> dict:
    denominator = r.choice([2, 4, 5, 10, 20, 25, 40, 50, 80, 100])
    numerator = r.randint(1, denominator - 1)
    correct = Fraction(100 * numerator, denominator)
    return _example(
        family="fraction_to_percentage_v8",
        topic="Converting between Fractions and Percentages",
        question=(
            f"Convert {numerator}/{denominator} to a percentage. "
            "Enter the number without the % sign."
        ),
        correct=correct,
        distractors=[
            (
                "Gives the decimal rather than the percentage",
                f"{numerator} / {denominator} = {fmt(Fraction(numerator, denominator))}",
                Fraction(numerator, denominator),
            ),
            (
                "Multiplies by 10 instead of 100",
                f"({numerator} / {denominator}) * 10 = {fmt(Fraction(10 * numerator, denominator))}",
                Fraction(10 * numerator, denominator),
            ),
            (
                "Reverses the fraction before converting to a percentage",
                f"({denominator} / {numerator}) * 100 = {fmt(Fraction(100 * denominator, numerator))}",
                Fraction(100 * denominator, numerator),
            ),
        ],
    )


def _lcm(r: random.Random) -> dict:
    factor = r.randint(2, 12)
    while True:
        x, y = r.randint(2, 15), r.randint(2, 15)
        if x != y and math.gcd(x, y) == 1:
            break
    a, b = factor * x, factor * y
    correct = factor * x * y
    return _example(
        family="lcm_v8",
        topic="Multiples and Lowest Common Multiple",
        question=f"Find the lowest common multiple of {a} and {b}.",
        correct=correct,
        distractors=[
            (
                "Finds the highest common factor instead",
                f"{a} / {x} = {factor}",
                factor,
            ),
            (
                "Multiplies the two numbers without removing a common factor",
                f"{a} * {b} = {a * b}",
                a * b,
            ),
            (
                "Adds the two numbers",
                f"{a} + {b} = {a + b}",
                a + b,
            ),
        ],
    )


def _square_root(r: random.Random) -> dict:
    root = r.randint(3, 150)
    square = root * root
    return _example(
        family="square_root_v8",
        topic="Square Roots, Cube Roots, etc",
        question=f"What is the square root of {square}?",
        correct=root,
        distractors=[
            (
                "Divides the number by 2",
                f"{square} / 2 = {fmt(Fraction(square, 2))}",
                Fraction(square, 2),
            ),
            (
                "Doubles the number",
                f"{square} * 2 = {square * 2}",
                square * 2,
            ),
            (
                "Subtracts 2 from the number",
                f"{square} - 2 = {square - 2}",
                square - 2,
            ),
        ],
    )


def _percentage_increase(r: random.Random) -> dict:
    amount = 5 * r.randint(4, 100)
    percent = r.choice([5, 10, 15, 20, 25, 30, 40, 50, 75])
    correct = Fraction(amount * (100 + percent), 100)
    return _example(
        family="percentage_increase_v8",
        topic="Percentage Increase and Decrease",
        question=f"Increase {amount} by {percent}%.",
        correct=correct,
        distractors=[
            (
                "Calculates only the increase",
                f"{amount} * {percent} / 100 = {fmt(Fraction(amount * percent, 100))}",
                Fraction(amount * percent, 100),
            ),
            (
                "Decreases instead of increasing",
                f"{amount} * (100 - {percent}) / 100 = {fmt(Fraction(amount * (100 - percent), 100))}",
                Fraction(amount * (100 - percent), 100),
            ),
            (
                "Adds the percentage number directly",
                f"{amount} + {percent} = {amount + percent}",
                amount + percent,
            ),
        ],
    )


def _fraction_division(r: random.Random) -> dict:
    a, b = r.randint(1, 7), r.randint(2, 9)
    c, d = r.randint(1, 7), r.randint(2, 9)
    correct = Fraction(a * d, b * c)
    return _example(
        family="fraction_division_v8",
        topic="Dividing Fractions",
        question=f"What is {a}/{b} ÷ {c}/{d}?",
        correct=correct,
        distractors=[
            (
                "Multiplies the fractions instead of dividing",
                f"({a} * {c}) / ({b} * {d}) = {fmt(Fraction(a * c, b * d))}",
                Fraction(a * c, b * d),
            ),
            (
                "Divides in the reverse order",
                f"({c} * {b}) / ({d} * {a}) = {fmt(Fraction(c * b, d * a))}",
                Fraction(c * b, d * a),
            ),
            (
                "Adds across the diagonal terms",
                f"({a} + {d}) / ({b} + {c}) = {fmt(Fraction(a + d, b + c))}",
                Fraction(a + d, b + c),
            ),
        ],
    )


def _simplifying_fraction(r: random.Random) -> dict:
    while True:
        numerator = r.randint(1, 14)
        denominator = r.randint(numerator + 1, 16)
        if math.gcd(numerator, denominator) == 1:
            break
    factor = r.randint(2, 12)
    top, bottom = numerator * factor, denominator * factor
    return _example(
        family="simplifying_fraction_v8",
        topic="Simplifying Fractions",
        question=f"Simplify {top}/{bottom}.",
        correct=Fraction(numerator, denominator),
        distractors=[
            (
                "Adds the common factor to numerator and denominator",
                f"({numerator} + {factor}) / ({denominator} + {factor}) = {fmt(Fraction(numerator + factor, denominator + factor))}",
                Fraction(numerator + factor, denominator + factor),
            ),
            (
                "Subtracts the common factor once from numerator and denominator",
                f"({top} - {factor}) / ({bottom} - {factor}) = {fmt(Fraction(top - factor, bottom - factor))}",
                Fraction(top - factor, bottom - factor),
            ),
            (
                "Divides only the numerator by the common factor",
                f"({top} / {factor}) / {bottom} = {fmt(Fraction(numerator, bottom))}",
                Fraction(numerator, bottom),
            ),
        ],
    )


_FAMILIES: dict[str, Callable[[random.Random], dict]] = {
    "equivalent_fraction_v8": _equivalent_fraction,
    "fraction_division_v8": _fraction_division,
    "fraction_to_decimal_v8": _fraction_to_decimal,
    "fraction_to_percentage_v8": _fraction_to_percentage,
    "lcm_v8": _lcm,
    "mixed_to_improper_v8": _mixed_to_improper,
    "negative_multiplication_v8": _negative_multiplication,
    "percentage_increase_v8": _percentage_increase,
    "round_significant_figures_v8": _round_significant_figures,
    "simplifying_fraction_v8": _simplifying_fraction,
    "square_root_v8": _square_root,
    "standard_form_v8": _standard_form,
}


def _valid(example: dict) -> bool:
    distractors = example["distractors"]
    answers = [normalize_answer(item["answer"]) for item in distractors]
    misconceptions = [
        item["misconception"].strip().casefold()
        for item in distractors
    ]
    return (
        len(distractors) == 3
        and len(set(answers)) == 3
        and len(set(misconceptions)) == 3
        and normalize_answer(example["correct"]) not in answers
        and all(
            computation_consistent(
                item["computation"],
                item["answer"],
                example["question"],
                display_units=True,
            )
            is True
            for item in distractors
        )
    )


def generate_targeted_sft(*, per_family: int = 100, seed: int = 91) -> list[dict]:
    """Generate deterministic, verifier-filtered SFT records per targeted family."""
    rng = random.Random(seed)
    records = []
    for family, generator in _FAMILIES.items():
        seen = set()
        attempts = 0
        while len(seen) < per_family and attempts < per_family * 500:
            attempts += 1
            example = generator(rng)
            if example["question"] in seen or not _valid(example):
                continue
            seen.add(example["question"])
            records.append(
                {
                    "system": SYSTEM_PROMPT,
                    "user": build_user(
                        example["question"],
                        example["correct"],
                        example["topic"],
                    ),
                    "assistant": build_assistant(example["distractors"]),
                    "meta": {
                        "family": family,
                        "topic": example["topic"],
                        "source": "synthetic_v8_targeted",
                        "dataset_version": "v8",
                    },
                }
            )
        if len(seen) < per_family:
            raise RuntimeError(
                f"targeted family {family} produced only {len(seen)}/{per_family}"
            )
    return records
