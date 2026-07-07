"""Programmatic error->distractor consistency checker.

For structured (synthetic/templated) questions we can recompute exactly what a
given misconception would produce and compare to a candidate answer. For free-text
real questions we cannot extract operands, so we return None (LLM-judge fallback).
"""
from __future__ import annotations

from fractions import Fraction
from typing import Optional

from .buggy_procedures import REGISTRY
from .text_utils import normalize_answer


def to_value(s) -> Optional[Fraction]:
    """Parse an answer into an exact Fraction, or None if not parseable."""
    if isinstance(s, Fraction):
        return s
    if isinstance(s, int):
        return Fraction(s)
    s = normalize_answer(s)
    try:
        if "/" in s:
            num, den = s.split("/")
            return Fraction(int(num), int(den))
        return Fraction(s)  # handles ints and decimal strings
    except Exception:
        return None


def expected_answer(family: str, operands: dict, misconception_id: str) -> Optional[Fraction]:
    mc = REGISTRY.get(misconception_id)
    if mc is None or mc.family != family:
        return None
    try:
        return mc.apply(operands)
    except Exception:
        return None


def is_consistent(family: str, operands: dict, misconception_id: str, candidate) -> Optional[bool]:
    """True/False when checkable programmatically, else None (needs LLM judge)."""
    exp = expected_answer(family, operands, misconception_id)
    if exp is None:
        return None
    cand = to_value(candidate)
    if cand is None:
        return None
    return exp == cand


def check_synthetic_example(ex: dict) -> bool:
    """Every distractor equals its misconception's computed value and none == correct."""
    fam, ops = ex["family"], ex["operands"]
    correct = to_value(ex["correct"])
    for d in ex["distractors"]:
        if is_consistent(fam, ops, d["misconception_id"], d["answer"]) is not True:
            return False
        if to_value(d["answer"]) == correct:
            return False
    return True


if __name__ == "__main__":
    import random

    from .buggy_procedures import generate_example

    r = random.Random(1)
    n = ok = 0
    for _ in range(300):
        ex = generate_example(r)
        if not ex:
            continue
        n += 1
        ok += 1 if check_synthetic_example(ex) else 0
    print(f"consistency self-check on {n} synthetic examples: {ok}/{n} consistent")
