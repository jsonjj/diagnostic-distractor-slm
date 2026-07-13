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


def to_display_value(s) -> Optional[Fraction]:
    """Parse a displayed numeric option, treating a trailing % as a unit.

    For distractor-generation consistency, ``6%`` is compared to a computation
    ending in the displayed coefficient ``6``. This does not redefine 6% as the
    mathematical quantity 6; it only supports exact option-text verification.
    """
    normalized = normalize_answer(s)
    if normalized.endswith("%"):
        normalized = normalized[:-1]
    return to_value(normalized)


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
_MAX_EXPRESSION_CHARS = 2048
_MAX_TOKENS = 256
_MAX_NUMBER_TOKEN_CHARS = 64
_MAX_PAREN_DEPTH = 32
_MAX_ABS_EXPONENT = 12
_MAX_VALUE_BITS = 4096


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
    if len(s) > _MAX_EXPRESSION_CHARS:
        return None
    tokens, i, n = [], 0, len(s)
    depth = 0
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "+-*/^()":
            tokens.append(c)
            if c == "(":
                depth += 1
                if depth > _MAX_PAREN_DEPTH:
                    return None
            elif c == ")":
                depth -= 1
                if depth < 0:
                    return None
            i += 1
        elif c in "0123456789" or c == ".":
            j = i
            while j < n and (s[j] in "0123456789" or s[j] == "."):
                j += 1
            if j - i > _MAX_NUMBER_TOKEN_CHARS:
                return None
            tokens.append(s[i:j])
            i = j
        else:
            return None  # unrecognized character -> not a pure-arithmetic expression
        if len(tokens) > _MAX_TOKENS:
            return None
    return tokens if depth == 0 else None


def _eval_tokens(tokens: List[str]) -> Optional[Fraction]:
    pos = 0

    def bounded(value):
        if value is None:
            return None
        if (
            value.numerator.bit_length() > _MAX_VALUE_BITS
            or value.denominator.bit_length() > _MAX_VALUE_BITS
        ):
            return None
        return value

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
            try:
                v = bounded(v + r if op == "+" else v - r)
            except (ArithmeticError, MemoryError, OverflowError):
                return None
            if v is None:
                return None
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
                try:
                    v = bounded(v / r)
                except (ArithmeticError, MemoryError, OverflowError):
                    return None
            else:
                try:
                    v = bounded(v * r)
                except (ArithmeticError, MemoryError, OverflowError):
                    return None
            if v is None:
                return None
        return v

    def factor():
        sign = 1
        while peek() in ("+", "-"):
            if eat() == "-":
                sign = -sign
        v = power()
        return None if v is None else bounded(sign * v)

    def power():
        v = atom()
        if v is None:
            return None
        if peek() == "^":
            eat()
            e = factor()
            if (
                e is None
                or e.denominator != 1
                or abs(e.numerator) > _MAX_ABS_EXPONENT
            ):
                return None
            try:
                v = bounded(v ** int(e))
            except (ArithmeticError, MemoryError, OverflowError, ValueError):
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
            return bounded(Fraction(Decimal(t)) if "." in t else Fraction(int(t)))
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


# ---- v5 hardening: reject computations that game the free metric ----
# The pre-v5 check only verified LHS == answer, so a model could satisfy it with a
# degenerate tautology ("6 = 6") or self-consistent arithmetic unrelated to the question
# ("2÷4 = 0.5" for "0.2 ÷ 0.4"). Two gates fix this:
#   B1 (always on): the LHS must contain at least one BINARY operator, else it is not a
#      real computation -> return None. Kills "6 = 6" / bare-number LHS.
#   B2 (opt-in via question=...): every numeric leaf in the LHS must be grounded in the
#      question's digits, or be a whitelisted structural constant. Catches fabricated
#      operands. Bounded (digit-reuse can still slip through) — the LLM judge is the backstop.
_GROUND_WHITELIST = {Fraction(0), Fraction(1), Fraction(2), Fraction(10), Fraction(100), Fraction(1000)}


def _has_binary_operator(computation) -> bool:
    """True if the LHS has a binary + - * / ^ (a leading unary sign does not count)."""
    s = str(computation)
    if "=" in s:
        s = s.split("=", 1)[0]
    toks = _tokenize(_preprocess_expr(s))
    if not toks:
        return False
    for i, t in enumerate(toks):
        if t in ("+", "-", "*", "/", "^") and i > 0:
            prev = toks[i - 1]
            if prev == ")" or re.fullmatch(r"[0-9.]+", prev):
                return True
    return False


def _numeric_leaves(computation) -> List[str]:
    s = str(computation)
    if "=" in s:
        s = s.split("=", 1)[0]
    toks = _tokenize(_preprocess_expr(s)) or []
    return [t for t in toks if re.fullmatch(r"[0-9.]+", t)]


def _leaves_grounded(computation, question) -> bool:
    """Anchor check: at least one numeric leaf must appear in the question's digits.

    A fabricated computation (operands wholly unrelated to the question, e.g. "100 × 5 = 500"
    for "0.2 ÷ 0.4") has NO leaf in the question -> rejected. A legitimate error-computation
    starts from the question's numbers and may introduce derived offsets (e.g.
    "875599 + 24401 = 900000") -> the anchor (875599) is present -> accepted. This is a
    deliberately loose screen; digit-reuse can still slip through (the LLM judge is the true
    backstop). We require an anchor rather than all-leaves-grounded so real, correct
    computations that introduce derived constants are not falsely rejected.
    """
    leaves = _numeric_leaves(computation)
    if not leaves:
        return False
    digit_content = "".join(re.findall(r"\d+", str(question)))
    for leaf in leaves:
        digits = leaf.replace(".", "")
        if digits and digits in digit_content:
            return True  # found an anchor operand from the question
    return False


def computation_consistent(
    computation,
    answer,
    question=None,
    *,
    display_units: bool = False,
) -> Optional[bool]:
    """True/False if the computation's LHS evaluates to `answer`; None if unparseable.

    Used by (a) the real-data verifier (quality filter) and (b) the eval harness'
    free `computation_consistency` metric. Compares by exact numeric value, so
    "1/2" and "0.5" are treated as equal.

    Backward-compatible: with `question=None` this is the pre-v5 check (LHS == answer only),
    so legacy dataset builds (v1-v4) are byte-identical. When `question` is provided (v5/eval),
    two HARDENING gates apply so the metric can't be gamed:
      B1: the LHS must contain a binary operator (rejects degenerate "6 = 6" -> None).
      B2: every numeric leaf must be grounded in the question's digits (else False).
    """
    lhs = eval_computation(computation)
    if lhs is None:
        return None
    ans = to_display_value(answer) if display_units else to_value(answer)
    if ans is None:
        return None
    if question is None:
        return lhs == ans  # pre-v5 behavior, preserved for legacy callers
    # --- v5 hardened path ---
    if not _has_binary_operator(computation):
        return None  # B1: not a real computation (e.g. "6 = 6")
    if lhs != ans:
        return False
    if not _leaves_grounded(computation, question):
        return False  # B2: operands not grounded in the question
    return True


def check_synthetic_example(ex: dict, harden: bool = False) -> bool:
    """Every distractor equals its misconception's computed value and none == correct.

    When a distractor also carries a `computation` (v4), that string's LHS must
    evaluate to the distractor's answer too. With harden=True (v5), the computation is
    also required to be operator-bearing and grounded in the question.
    """
    fam, ops = ex["family"], ex["operands"]
    correct = to_value(ex["correct"])
    # v5 opt-in: when harden=True, computations are checked WITH the question (operator-bearing
    # + grounded) -- the same bar eval applies to predictions. Default False keeps the pre-v5
    # behavior so legacy builds (v1-v4) are byte-identical.
    question = ex.get("question") if harden else None
    for d in ex["distractors"]:
        if is_consistent(fam, ops, d["misconception_id"], d["answer"]) is not True:
            return False
        if to_value(d["answer"]) == correct:
            return False
        comp = d.get("computation")
        if comp and computation_consistent(comp, d["answer"], question) is not True:
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
