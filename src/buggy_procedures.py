"""Buggy-procedure engine: common 'Number' misconceptions as executable procedures.

Brown & Burton (1978): student math errors are systematic, executable 'bugs'. Each
misconception here maps a structured question's operands to the WRONG answer a
student holding that misconception would compute. Used to (a) generate
guaranteed-consistent synthetic training data and (b) programmatically verify
error->distractor consistency in the eval harness.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Callable, Dict, List, Optional

F = Fraction


def fmt(v) -> str:
    """Format a numeric value the way it would appear as an answer option."""
    if isinstance(v, Fraction):
        if v.denominator == 1:
            return str(v.numerator)
        d = v.denominator
        for p in (2, 5):
            while d % p == 0:
                d //= p
        if d == 1:  # terminating decimal
            return str(Decimal(v.numerator) / Decimal(v.denominator))
        return f"{v.numerator}/{v.denominator}"
    return str(v)


@dataclass(frozen=True)
class Misconception:
    id: str
    name: str
    family: str
    apply: Callable[[dict], Fraction]


@dataclass
class Question:
    family: str
    operands: dict
    correct: Fraction
    text: str
    topic: str


REGISTRY: Dict[str, Misconception] = {}


def _reg(id, name, family, fn):
    REGISTRY[id] = Misconception(id, name, family, fn)


# ---------------- question families ----------------
def _q_fraction_add(r):
    return {"a": r.randint(1, 7), "b": r.randint(2, 9), "c": r.randint(1, 7), "d": r.randint(2, 9)}


def _q_fraction_mul(r):
    return {"a": r.randint(1, 7), "b": r.randint(2, 9), "c": r.randint(1, 7), "d": r.randint(2, 9)}


def _q_fraction_div_int(r):
    n = r.randint(2, 6)
    b = n * r.randint(2, 5)  # ensure b divisible by n
    a = r.randint(1, b - 1)
    return {"a": a, "b": b, "n": n}


def _q_order_of_ops(r):
    return {"a": r.randint(2, 9), "b": r.randint(2, 9), "c": r.randint(2, 9)}


def _q_neg_add(r):
    a, b = r.randint(1, 20), r.randint(1, 20)
    while a == b:
        b = r.randint(1, 20)
    return {"a": a, "b": b}


def _q_decimal_mul(r):
    return {"p": r.randint(1, 9), "q": r.randint(1, 9)}


def _q_square(r):
    return {"n": r.randint(2, 20)}


def _q_percent_of(r):
    return {
        "p": r.choice([5, 10, 15, 20, 25, 30, 40, 50, 75]),
        "A": r.choice([16, 20, 24, 30, 32, 36, 40, 48, 50, 60, 64, 80, 90, 100, 120, 150, 200, 240]),
    }


FAMILIES = {
    "fraction_add": {
        "topic": "Adding and Subtracting Fractions",
        "gen": _q_fraction_add,
        "text": lambda o: f"What is {o['a']}/{o['b']} + {o['c']}/{o['d']}?",
        "correct": lambda o: F(o["a"], o["b"]) + F(o["c"], o["d"]),
    },
    "fraction_mul": {
        "topic": "Multiplying Fractions",
        "gen": _q_fraction_mul,
        "text": lambda o: f"What is {o['a']}/{o['b']} \u00d7 {o['c']}/{o['d']}?",
        "correct": lambda o: F(o["a"] * o["c"], o["b"] * o["d"]),
    },
    "fraction_div_int": {
        "topic": "Dividing Fractions",
        "gen": _q_fraction_div_int,
        "text": lambda o: f"What is {o['a']}/{o['b']} \u00f7 {o['n']}?",
        "correct": lambda o: F(o["a"], o["b"] * o["n"]),
    },
    "order_of_ops": {
        "topic": "BIDMAS",
        "gen": _q_order_of_ops,
        "text": lambda o: f"What is {o['a']} + {o['b']} \u00d7 {o['c']}?",
        "correct": lambda o: F(o["a"] + o["b"] * o["c"]),
    },
    "neg_add": {
        "topic": "Adding and Subtracting Negative Numbers",
        "gen": _q_neg_add,
        "text": lambda o: f"What is -{o['a']} + {o['b']}?",
        "correct": lambda o: F(o["b"] - o["a"]),
    },
    "decimal_mul": {
        "topic": "Multiplying and Dividing with Decimals",
        "gen": _q_decimal_mul,
        "text": lambda o: f"What is 0.{o['p']} \u00d7 0.{o['q']}?",
        "correct": lambda o: F(o["p"] * o["q"], 100),
    },
    "square": {
        "topic": "Squares, Cubes, etc",
        "gen": _q_square,
        "text": lambda o: f"What is {o['n']}\u00b2?",
        "correct": lambda o: F(o["n"] * o["n"]),
    },
    "percent_of": {
        "topic": "Percentages of an Amount",
        "gen": _q_percent_of,
        "text": lambda o: f"What is {o['p']}% of {o['A']}?",
        "correct": lambda o: F(o["A"] * o["p"], 100),
    },
}


# ---------------- misconceptions (4-6 distinct executable bugs per family) ----------------
# Each registration below maps operands -> the exact value a student holding that
# misconception would compute. generate_example() guarantees any chosen 3 are
# distinct misconceptions with 3 distinct values (and none equal to the key), so a
# richer pool per family gives the model more misconception variety to learn from.

# fraction_add: a/b + c/d  (correct = (ad+bc)/(bd))
_reg("frac_add_num_den", "Adds the numerators and the denominators", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["b"] + o["d"]))
_reg("frac_add_keep_first_den", "Adds numerators and keeps the first denominator", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["b"]))
_reg("frac_add_mul_den", "Adds numerators but multiplies the denominators", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["b"] * o["d"]))
_reg("frac_add_keep_second_den", "Adds numerators and keeps the second denominator", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["d"]))
_reg("frac_add_multiply_instead", "Multiplies the fractions instead of adding them", "fraction_add",
     lambda o: F(o["a"] * o["c"], o["b"] * o["d"]))

# fraction_mul: a/b x c/d  (correct = ac/bd)
_reg("frac_mul_cross", "Cross-multiplies instead of multiplying straight across", "fraction_mul",
     lambda o: F(o["a"] * o["d"], o["b"] * o["c"]))
_reg("frac_mul_add", "Adds the fractions instead of multiplying", "fraction_mul",
     lambda o: F(o["a"], o["b"]) + F(o["c"], o["d"]))
_reg("frac_mul_num_add_den", "Multiplies numerators but adds denominators", "fraction_mul",
     lambda o: F(o["a"] * o["c"], o["b"] + o["d"]))
_reg("frac_mul_add_num_mul_den", "Adds the numerators but multiplies the denominators", "fraction_mul",
     lambda o: F(o["a"] + o["c"], o["b"] * o["d"]))
_reg("frac_mul_num_keep_first_den", "Multiplies numerators but keeps the first denominator", "fraction_mul",
     lambda o: F(o["a"] * o["c"], o["b"]))

# fraction_div_int: a/b / n  (correct = a/(bn))
_reg("frac_div_den_by_int", "When dividing a fraction by an integer, divides the denominator by the integer",
     "fraction_div_int", lambda o: F(o["a"], o["b"] // o["n"]))
_reg("frac_div_add_int_den", "Adds the integer to the denominator instead of multiplying", "fraction_div_int",
     lambda o: F(o["a"], o["b"] + o["n"]))
_reg("frac_div_num_over_int", "Ignores the denominator and divides the numerator by the integer",
     "fraction_div_int", lambda o: F(o["a"], o["n"]))
_reg("frac_div_mul_num_by_int", "Multiplies the numerator by the integer instead of the denominator",
     "fraction_div_int", lambda o: F(o["a"] * o["n"], o["b"]))
_reg("frac_div_ignore_int", "Ignores the divisor and leaves the fraction unchanged", "fraction_div_int",
     lambda o: F(o["a"], o["b"]))

# order_of_ops: a + b x c  (correct = a + bc)
_reg("ooo_left_to_right", "Carries out operations left to right, ignoring order of operations",
     "order_of_ops", lambda o: F((o["a"] + o["b"]) * o["c"]))
_reg("ooo_add_all", "Adds all the numbers, ignoring the multiplication", "order_of_ops",
     lambda o: F(o["a"] + o["b"] + o["c"]))
_reg("ooo_mul_all", "Multiplies all the numbers together", "order_of_ops",
     lambda o: F(o["a"] * o["b"] * o["c"]))
_reg("ooo_mul_first_two", "Multiplies the first two numbers then adds the third", "order_of_ops",
     lambda o: F(o["a"] * o["b"] + o["c"]))
_reg("ooo_add_last_two_first", "Adds the last two numbers first, then multiplies by the first", "order_of_ops",
     lambda o: F(o["a"] * (o["b"] + o["c"])))

# neg_add: -a + b  (correct = b - a)
_reg("neg_ignore_sign", "Ignores the negative sign and adds the magnitudes", "neg_add",
     lambda o: F(o["a"] + o["b"]))
_reg("neg_both_negative", "Treats both numbers as negative", "neg_add",
     lambda o: F(-(o["a"] + o["b"])))
_reg("neg_subtract_wrong_sign", "Subtracts the numbers but gives the wrong sign", "neg_add",
     lambda o: F(o["a"] - o["b"]))
_reg("neg_ignore_second", "Ignores the number being added and just negates the first", "neg_add",
     lambda o: F(-o["a"]))

# decimal_mul: 0.p x 0.q  (correct = pq/100)
_reg("dec_one_place", "Uses one decimal place instead of counting both", "decimal_mul",
     lambda o: F(o["p"] * o["q"], 10))
_reg("dec_no_point", "Multiplies as whole numbers and ignores the decimal point", "decimal_mul",
     lambda o: F(o["p"] * o["q"]))
_reg("dec_add", "Adds the decimals instead of multiplying", "decimal_mul",
     lambda o: F(o["p"] + o["q"], 10))
_reg("dec_too_many_places", "Multiplies correctly but uses too many decimal places", "decimal_mul",
     lambda o: F(o["p"] * o["q"], 1000))
_reg("dec_add_digits_no_point", "Adds the digits and ignores the decimal point", "decimal_mul",
     lambda o: F(o["p"] + o["q"]))

# square: n^2  (correct = n*n)
_reg("sq_double", "Mixes up squaring with doubling (multiplies by 2)", "square",
     lambda o: F(2 * o["n"]))
_reg("sq_repeat_digit", "Reads the power as repeating the digit", "square",
     lambda o: F(int(str(o["n"]) * 2)))
_reg("sq_add_two", "Adds 2 instead of squaring", "square",
     lambda o: F(o["n"] + 2))
_reg("sq_times_next", "Multiplies by the next number instead of by itself", "square",
     lambda o: F(o["n"] * (o["n"] + 1)))
_reg("sq_cube", "Cubes the number instead of squaring it", "square",
     lambda o: F(o["n"] ** 3))

# percent_of: p% of A  (correct = Ap/100)
_reg("pct_no_div_100", "Multiplies by the percentage but forgets to divide by 100", "percent_of",
     lambda o: F(o["A"] * o["p"]))
_reg("pct_divide_by_p", "Divides the amount by the percentage number", "percent_of",
     lambda o: F(o["A"], o["p"]))
_reg("pct_subtract", "Subtracts the percentage number from the amount", "percent_of",
     lambda o: F(o["A"] - o["p"]))
_reg("pct_div_by_10", "Divides by 10 instead of 100", "percent_of",
     lambda o: F(o["A"] * o["p"], 10))
_reg("pct_add", "Adds the percentage number to the amount", "percent_of",
     lambda o: F(o["A"] + o["p"]))


def _mcs_for(family: str) -> List[Misconception]:
    return [m for m in REGISTRY.values() if m.family == family]


def make_question(family: str, r: random.Random) -> Question:
    spec = FAMILIES[family]
    o = spec["gen"](r)
    return Question(family, o, spec["correct"](o), spec["text"](o), spec["topic"])


def generate_example(r: random.Random, family: Optional[str] = None, tries: int = 40) -> Optional[dict]:
    fams = list(FAMILIES) if family is None else [family]
    for _ in range(tries):
        fam = r.choice(fams)
        q = make_question(fam, r)
        mcs = _mcs_for(fam)
        r.shuffle(mcs)
        picked = []
        seen = {q.correct}
        for m in mcs:
            try:
                v = m.apply(q.operands)
            except Exception:
                continue
            if v in seen:
                continue
            seen.add(v)
            picked.append((m, v))
            if len(picked) == 3:
                break
        if len(picked) < 3:
            continue
        return {
            "family": fam,
            "operands": q.operands,
            "question": q.text,
            "topic": q.topic,
            "correct": fmt(q.correct),
            "distractors": [
                {"misconception_id": m.id, "misconception": m.name, "answer": fmt(v)} for (m, v) in picked
            ],
        }
    return None


if __name__ == "__main__":
    r = random.Random(0)
    print(f"families: {len(FAMILIES)} | misconceptions: {len(REGISTRY)}\n")
    for _ in range(8):
        ex = generate_example(r)
        if not ex:
            continue
        print(f"{ex['question']} = {ex['correct']}   [{ex['topic']}]")
        for d in ex["distractors"]:
            print(f"    {d['answer']:>8}  <-  {d['misconception']}")
