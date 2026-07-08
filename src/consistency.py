"""Programmatic error->distractor consistency checker.

For structured (synthetic/templated) questions we can recompute exactly what a
given misconception would produce and compare to a candidate answer. For free-text
real questions we cannot extract operands, so we return None (LLM-judge fallback).

v4 adds a *computation-based* check that works on ANY item (synthetic or real): a
distractor now carries a `computation` string (e.g. "0.4 \u00f7 0.2 = 2"); we parse the
left-hand side, evaluate it exactly (Fraction arithmetic), and compare to the answer.
This gives a free (no-API) consistency signal for computation-bearing outputs.
"""
from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import List, Optional

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


# ---------------- computation strings: parse "<arithmetic> = <answer>" and evaluate the LHS ----------------
# A small, safe arithmetic evaluator over exact Fractions. It intentionally supports ONLY
# the operators our targets use (+ - * / and parentheses, plus ^ / superscripts for powers),
# so there is no eval()/exec() and no way to run arbitrary code.
_SUPERSCRIPT = str.maketrans("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079", "0123456789")


def _preprocess_expr(s: str) -> str:
    s = str(s)
    s = re.sub(r"[\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079]+",
               lambda m: "^" + m.group(0).translate(_SUPERSCRIPT), s)
    for a, b in (("\u00d7", "*"), ("\u22c5", "*"), ("\u00b7", "*"), ("\u2217", "*"),
                 ("\u00f7", "/"), ("\u2212", "-"), ("\u2013", "-"), ("\u2014", "-")):
        s = s.replace(a, b)
    s = re.sub(r"\bof\b", "*", s, flags=re.IGNORECASE)          # "20% of 50" -> "(20/100) * 50"
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    s = re.sub(r"(?<=[\d\)])\s*[xX]\s*(?=[\d\(])", "*", s)      # 'x' as multiply between operands
    s = re.sub(r"(?<=\d),(?=\d)", "", s)                          # thousands separators
    return s


def _tokenize(s: str) -> Optional[List[str]]:
    tokens, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "+-*/^()":
            tokens.append(c)
            i += 1
            continue
        if c.isdigit() or c == ".":
            j = i
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            tokens.append(s[i:j])
            i = j
            continue
        return None  # unrecognized character -> not a pure-arithmetic expression
    return tokens


def _eval_tokens(tokens: List[str]) -> Optional[Fraction]:
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def eat():
        nonlocal pos
        t = tokens[pos]
        pos += 1
        return t

    def expr():
        v = term()
        if v is None:
            return None
        while peek() in ("+", "-"):
            op = eat()
            r = term()
            if r is None:
                return None
            v = v + r if op == "+" else v - r
        return v

    def term():
        v = factor()
        if v is None:
            return None
        while peek() in ("*", "/"):
            op = eat()
            r = factor()
            if r is None:
                return None
            if op == "/":
                if r == 0:
                    return None
                v = v / r
            else:
                v = v * r
        return v

    def factor():
        sign = 1
        while peek() in ("+", "-"):
            if eat() == "-":
                sign = -sign
        v = power()
        return None if v is None else sign * v

    def power():
        v = atom()
        if v is None:
            return None
        if peek() == "^":
            eat()
            e = factor()
            if e is None or e.denominator != 1:
                return None
            try:
                v = v ** int(e)
            except (ZeroDivisionError, ValueError):
                return None
        return v

    def atom():
        t = peek()
        if t == "(":
            eat()
            v = expr()
            if v is None or peek() != ")":
                return None
            eat()
            return v
        if t is None or t in "+-*/^)":
            return None
        eat()
        try:
            return Fraction(Decimal(t)) if "." in t else Fraction(int(t))
        except Exception:
            return None

    v = expr()
    return v if (v is not None and pos == len(tokens)) else None


def eval_computation(computation) -> Optional[Fraction]:
    """Evaluate the left-hand side of a "<arithmetic> = <answer>" computation string.

    Returns an exact Fraction, or None if the LHS is empty or not pure arithmetic.
    """
    if not computation:
        return None
    s = str(computation)
    if "=" in s:
        s = s.split("=", 1)[0]  # only the arithmetic before the first '='
    toks = _tokenize(_preprocess_expr(s))
    if not toks:
        return None
    return _eval_tokens(toks)


def computation_consistent(computation, answer) -> Optional[bool]:
    """True/False if the computation's LHS evaluates to `answer`; None if unparseable.

    Used by (a) the real-data verifier (quality filter) and (b) the eval harness'
    free `computation_consistency` metric. Compares by exact numeric value, so
    "1/2" and "0.5" are treated as equal.
    """
    lhs = eval_computation(computation)
    if lhs is None:
        return None
    ans = to_value(answer)
    if ans is None:
        return None
    return lhs == ans


def check_synthetic_example(ex: dict) -> bool:
    """Every distractor equals its misconception's computed value and none == correct.

    When a distractor also carries a `computation` (v4), that string's LHS must
    evaluate to the distractor's answer too.
    """
    fam, ops = ex["family"], ex["operands"]
    correct = to_value(ex["correct"])
    for d in ex["distractors"]:
        if is_consistent(fam, ops, d["misconception_id"], d["answer"]) is not True:
            return False
        if to_value(d["answer"]) == correct:
            return False
        comp = d.get("computation")
        if comp and computation_consistent(comp, d["answer"]) is not True:
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
