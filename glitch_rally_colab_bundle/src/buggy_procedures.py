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
    # comp(operands) -> the human-readable arithmetic (LHS only) a student with this
    # misconception performs, e.g. "(3 + 4)/(6 + 9)". It MUST evaluate to apply(operands).
    # generate_example appends " = <answer>" to form the full show-the-work computation.
    comp: Optional[Callable[[dict], str]] = None


@dataclass
class Question:
    family: str
    operands: dict
    correct: Fraction
    text: str
    topic: str


REGISTRY: Dict[str, Misconception] = {}


def _reg(id, name, family, fn, comp=None):
    REGISTRY[id] = Misconception(id, name, family, fn, comp)


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


# ---- v5 families: cover the eval's high-count uncovered topics (Place Value, Rounding,
# decimal/percentage conversion, decimal add/sub, indices, factors/HCF, mental arithmetic).
# Every misconception below pairs apply() (exact value) with comp() (arithmetic string) whose
# numeric leaves are digits taken from the question or whitelisted constants (0,1,2,10,100,1000),
# so the hardened consistency check (operator-present + question-grounded) passes.

def _q_place_value(r):
    # value of the (non-zero) hundreds digit of a 4-digit number
    while True:
        N = r.randint(1000, 9999)
        d = (N // 100) % 10
        if d != 0:
            return {"N": N, "d": d}


def _q_round_dp(r):
    # round whole.d1 d2 to 1 decimal place (d1<=8 so rounding never carries into the whole part)
    return {"whole": r.randint(1, 9), "d1": r.randint(1, 8), "d2": r.randint(1, 9)}


def _q_convert_dec_pct(r):
    return {"a": r.randint(1, 9), "b": r.randint(1, 9)}  # convert 0.ab to a percentage


def _q_dec_addsub(r):
    return {"a": r.randint(1, 9), "b": r.randint(1, 9)}  # 0.a + 0.0b


def _q_indices(r):
    m = r.randint(2, 5)
    n = r.randint(2, 5)
    while n == m:
        n = r.randint(2, 5)
    return {"a": r.randint(2, 9), "m": m, "n": n}


def _q_hcf(r):
    g = r.choice([2, 3, 4, 5, 6, 7, 8, 9])
    x, y = r.choice([(2, 5), (3, 5), (2, 7), (3, 7), (4, 7), (2, 9), (4, 9), (5, 7),
                     (5, 8), (3, 8), (3, 4), (2, 3), (4, 5), (5, 6), (6, 7), (7, 8), (7, 9)])
    return {"g": g, "a": g * x, "b": g * y}


def _q_mental_add(r):
    a = r.randint(25, 89)
    b = r.randint(15, a - 1)  # a > b so the "subtract instead" bug stays positive
    return {"a": a, "b": b}


def _q_mental_mult(r):
    return {"a": r.randint(11, 19), "b": r.randint(3, 9)}


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
    "place_value": {
        "topic": "Place Value",
        "gen": _q_place_value,
        "text": lambda o: f"What is the value of the digit {o['d']} in {o['N']}?",
        "correct": lambda o: F(o["d"] * 100),
    },
    "round_dp": {
        "topic": "Rounding to Decimal Places",
        "gen": _q_round_dp,
        "text": lambda o: f"What is {o['whole']}.{o['d1']}{o['d2']} rounded to 1 decimal place?",
        "correct": lambda o: F(o["whole"] * 10 + o["d1"] + (1 if o["d2"] >= 5 else 0), 10),
    },
    "convert_dec_pct": {
        "topic": "Converting between Decimals and Percentages",
        "gen": _q_convert_dec_pct,
        "text": lambda o: f"Convert 0.{o['a']}{o['b']} to a percentage. What is the percentage value?",
        "correct": lambda o: F(o["a"] * 10 + o["b"]),
    },
    "dec_addsub": {
        "topic": "Adding and Subtracting with Decimals",
        "gen": _q_dec_addsub,
        "text": lambda o: f"What is 0.{o['a']} + 0.0{o['b']}?",
        "correct": lambda o: F(o["a"] * 10 + o["b"], 100),
    },
    "indices": {
        "topic": "Laws of Indices",
        "gen": _q_indices,
        "text": lambda o: f"What is {o['a']}^{o['m']} × {o['a']}^{o['n']}? Give the value.",
        "correct": lambda o: F(o["a"] ** (o["m"] + o["n"])),
    },
    "hcf": {
        "topic": "Factors and Highest Common Factor",
        "gen": _q_hcf,
        "text": lambda o: f"What is the highest common factor of {o['a']} and {o['b']}?",
        "correct": lambda o: F(o["g"]),
    },
    "mental_add": {
        "topic": "Mental Addition and Subtraction",
        "gen": _q_mental_add,
        "text": lambda o: f"What is {o['a']} + {o['b']}?",
        "correct": lambda o: F(o["a"] + o["b"]),
    },
    "mental_mult": {
        "topic": "Mental Multiplication and Division",
        "gen": _q_mental_mult,
        "text": lambda o: f"What is {o['a']} × {o['b']}?",
        "correct": lambda o: F(o["a"] * o["b"]),
    },
}


# ---------------- misconceptions (4-6 distinct executable bugs per family) ----------------
# Each registration below maps operands -> the exact value a student holding that
# misconception would compute. generate_example() guarantees any chosen 3 are
# distinct misconceptions with 3 distinct values (and none equal to the key), so a
# richer pool per family gives the model more misconception variety to learn from.

# Each _reg also takes a `comp` lambda: the arithmetic (LHS only) a student with that
# misconception performs. It MUST evaluate (via src.consistency.eval_computation) to the
# same value as `apply`. Style: tight fraction bar "a/b"; spaces around + - x /.

# fraction_add: a/b + c/d  (correct = (ad+bc)/(bd))
_reg("frac_add_num_den", "Adds the numerators and the denominators", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["b"] + o["d"]),
     lambda o: f"({o['a']} + {o['c']})/({o['b']} + {o['d']})")
_reg("frac_add_keep_first_den", "Adds numerators and keeps the first denominator", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["b"]),
     lambda o: f"({o['a']} + {o['c']})/{o['b']}")
_reg("frac_add_mul_den", "Adds numerators but multiplies the denominators", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["b"] * o["d"]),
     lambda o: f"({o['a']} + {o['c']})/({o['b']} \u00d7 {o['d']})")
_reg("frac_add_keep_second_den", "Adds numerators and keeps the second denominator", "fraction_add",
     lambda o: F(o["a"] + o["c"], o["d"]),
     lambda o: f"({o['a']} + {o['c']})/{o['d']}")
_reg("frac_add_multiply_instead", "Multiplies the fractions instead of adding them", "fraction_add",
     lambda o: F(o["a"] * o["c"], o["b"] * o["d"]),
     lambda o: f"{o['a']}/{o['b']} \u00d7 {o['c']}/{o['d']}")

# fraction_mul: a/b x c/d  (correct = ac/bd)
_reg("frac_mul_cross", "Cross-multiplies instead of multiplying straight across", "fraction_mul",
     lambda o: F(o["a"] * o["d"], o["b"] * o["c"]),
     lambda o: f"({o['a']} \u00d7 {o['d']})/({o['b']} \u00d7 {o['c']})")
_reg("frac_mul_add", "Adds the fractions instead of multiplying", "fraction_mul",
     lambda o: F(o["a"], o["b"]) + F(o["c"], o["d"]),
     lambda o: f"{o['a']}/{o['b']} + {o['c']}/{o['d']}")
_reg("frac_mul_num_add_den", "Multiplies numerators but adds denominators", "fraction_mul",
     lambda o: F(o["a"] * o["c"], o["b"] + o["d"]),
     lambda o: f"({o['a']} \u00d7 {o['c']})/({o['b']} + {o['d']})")
_reg("frac_mul_add_num_mul_den", "Adds the numerators but multiplies the denominators", "fraction_mul",
     lambda o: F(o["a"] + o["c"], o["b"] * o["d"]),
     lambda o: f"({o['a']} + {o['c']})/({o['b']} \u00d7 {o['d']})")
_reg("frac_mul_num_keep_first_den", "Multiplies numerators but keeps the first denominator", "fraction_mul",
     lambda o: F(o["a"] * o["c"], o["b"]),
     lambda o: f"({o['a']} \u00d7 {o['c']})/{o['b']}")

# fraction_div_int: a/b / n  (correct = a/(bn))
_reg("frac_div_den_by_int", "When dividing a fraction by an integer, divides the denominator by the integer",
     "fraction_div_int", lambda o: F(o["a"], o["b"] // o["n"]),
     lambda o: f"{o['a']}/({o['b']} \u00f7 {o['n']})")
_reg("frac_div_add_int_den", "Adds the integer to the denominator instead of multiplying", "fraction_div_int",
     lambda o: F(o["a"], o["b"] + o["n"]),
     lambda o: f"{o['a']}/({o['b']} + {o['n']})")
_reg("frac_div_num_over_int", "Ignores the denominator and divides the numerator by the integer",
     "fraction_div_int", lambda o: F(o["a"], o["n"]),
     lambda o: f"{o['a']}/{o['n']}")
_reg("frac_div_mul_num_by_int", "Multiplies the numerator by the integer instead of the denominator",
     "fraction_div_int", lambda o: F(o["a"] * o["n"], o["b"]),
     lambda o: f"({o['a']} \u00d7 {o['n']})/{o['b']}")
_reg("frac_div_ignore_int", "Ignores the divisor and leaves the fraction unchanged", "fraction_div_int",
     lambda o: F(o["a"], o["b"]),
     lambda o: f"{o['a']}/{o['b']}")

# order_of_ops: a + b x c  (correct = a + bc)
_reg("ooo_left_to_right", "Carries out operations left to right, ignoring order of operations",
     "order_of_ops", lambda o: F((o["a"] + o["b"]) * o["c"]),
     lambda o: f"({o['a']} + {o['b']}) \u00d7 {o['c']}")
_reg("ooo_add_all", "Adds all the numbers, ignoring the multiplication", "order_of_ops",
     lambda o: F(o["a"] + o["b"] + o["c"]),
     lambda o: f"{o['a']} + {o['b']} + {o['c']}")
_reg("ooo_mul_all", "Multiplies all the numbers together", "order_of_ops",
     lambda o: F(o["a"] * o["b"] * o["c"]),
     lambda o: f"{o['a']} \u00d7 {o['b']} \u00d7 {o['c']}")
_reg("ooo_mul_first_two", "Multiplies the first two numbers then adds the third", "order_of_ops",
     lambda o: F(o["a"] * o["b"] + o["c"]),
     lambda o: f"{o['a']} \u00d7 {o['b']} + {o['c']}")
_reg("ooo_add_last_two_first", "Adds the last two numbers first, then multiplies by the first", "order_of_ops",
     lambda o: F(o["a"] * (o["b"] + o["c"])),
     lambda o: f"{o['a']} \u00d7 ({o['b']} + {o['c']})")

# neg_add: -a + b  (correct = b - a)
_reg("neg_ignore_sign", "Ignores the negative sign and adds the magnitudes", "neg_add",
     lambda o: F(o["a"] + o["b"]),
     lambda o: f"{o['a']} + {o['b']}")
_reg("neg_both_negative", "Treats both numbers as negative", "neg_add",
     lambda o: F(-(o["a"] + o["b"])),
     lambda o: f"-{o['a']} - {o['b']}")
_reg("neg_subtract_wrong_sign", "Subtracts the numbers but gives the wrong sign", "neg_add",
     lambda o: F(o["a"] - o["b"]),
     lambda o: f"{o['a']} - {o['b']}")
_reg("neg_ignore_second", "Ignores the number being added and just negates the first", "neg_add",
     lambda o: F(-o["a"]),
     lambda o: f"-{o['a']}")  # operator-free: fine for legacy (v4); v5's hardened filter drops it

# decimal_mul: 0.p x 0.q  (correct = pq/100)
_reg("dec_one_place", "Uses one decimal place instead of counting both", "decimal_mul",
     lambda o: F(o["p"] * o["q"], 10),
     lambda o: f"({o['p']} \u00d7 {o['q']})/10")
_reg("dec_no_point", "Multiplies as whole numbers and ignores the decimal point", "decimal_mul",
     lambda o: F(o["p"] * o["q"]),
     lambda o: f"{o['p']} \u00d7 {o['q']}")
_reg("dec_add", "Adds the decimals instead of multiplying", "decimal_mul",
     lambda o: F(o["p"] + o["q"], 10),
     lambda o: f"0.{o['p']} + 0.{o['q']}")
_reg("dec_too_many_places", "Multiplies correctly but uses too many decimal places", "decimal_mul",
     lambda o: F(o["p"] * o["q"], 1000),
     lambda o: f"({o['p']} \u00d7 {o['q']})/1000")
_reg("dec_add_digits_no_point", "Adds the digits and ignores the decimal point", "decimal_mul",
     lambda o: F(o["p"] + o["q"]),
     lambda o: f"{o['p']} + {o['q']}")

# square: n^2  (correct = n*n)
_reg("sq_double", "Mixes up squaring with doubling (multiplies by 2)", "square",
     lambda o: F(2 * o["n"]),
     lambda o: f"2 \u00d7 {o['n']}")
_reg("sq_repeat_digit", "Reads the power as repeating the digit", "square",
     lambda o: F(int(str(o["n"]) * 2)),
     lambda o: f"{o['n']} \u00d7 {10 ** len(str(o['n']))} + {o['n']}")
_reg("sq_add_two", "Adds 2 instead of squaring", "square",
     lambda o: F(o["n"] + 2),
     lambda o: f"{o['n']} + 2")
_reg("sq_times_next", "Multiplies by the next number instead of by itself", "square",
     lambda o: F(o["n"] * (o["n"] + 1)),
     lambda o: f"{o['n']} \u00d7 {o['n'] + 1}")  # legacy form (n+1 folded); v5 hardening drops if ungrounded
_reg("sq_cube", "Cubes the number instead of squaring it", "square",
     lambda o: F(o["n"] ** 3),
     lambda o: f"{o['n']} \u00d7 {o['n']} \u00d7 {o['n']}")

# percent_of: p% of A  (correct = Ap/100)
_reg("pct_no_div_100", "Multiplies by the percentage but forgets to divide by 100", "percent_of",
     lambda o: F(o["A"] * o["p"]),
     lambda o: f"{o['A']} \u00d7 {o['p']}")
_reg("pct_divide_by_p", "Divides the amount by the percentage number", "percent_of",
     lambda o: F(o["A"], o["p"]),
     lambda o: f"{o['A']} \u00f7 {o['p']}")
_reg("pct_subtract", "Subtracts the percentage number from the amount", "percent_of",
     lambda o: F(o["A"] - o["p"]),
     lambda o: f"{o['A']} - {o['p']}")
_reg("pct_div_by_10", "Divides by 10 instead of 100", "percent_of",
     lambda o: F(o["A"] * o["p"], 10),
     lambda o: f"({o['A']} \u00d7 {o['p']})/10")
_reg("pct_add", "Adds the percentage number to the amount", "percent_of",
     lambda o: F(o["A"] + o["p"]),
     lambda o: f"{o['A']} + {o['p']}")

# place_value: value of the hundreds digit d in N  (correct = d*100)
_reg("pv_face_value", "Gives the digit's face value, ignoring its place value", "place_value",
     lambda o: F(o["d"]),
     lambda o: f"{o['d']} × 1")
_reg("pv_one_place_low", "Reads the digit one place value too low (tens instead of hundreds)", "place_value",
     lambda o: F(o["d"] * 10),
     lambda o: f"{o['d']} × 10")
_reg("pv_one_place_high", "Reads the digit one place value too high (thousands instead of hundreds)", "place_value",
     lambda o: F(o["d"] * 1000),
     lambda o: f"{o['d']} × 1000")
_reg("pv_reads_whole", "States the whole number instead of the digit's place value", "place_value",
     lambda o: F(o["N"]),
     lambda o: f"{o['N']} × 1")

# round_dp: round whole.d1 d2 to 1 dp  (correct = round-half-up)
_reg("rd_truncate", "Truncates the extra digit instead of rounding", "round_dp",
     lambda o: F(o["whole"] * 10 + o["d1"], 10),
     lambda o: f"({o['whole']} × 10 + {o['d1']})/10")
_reg("rd_round_up_always", "Always rounds the last digit up regardless of its value", "round_dp",
     lambda o: F(o["whole"] * 10 + o["d1"] + 1, 10),
     lambda o: f"({o['whole']} × 10 + {o['d1']} + 1)/10")
_reg("rd_drops_to_whole", "Drops the decimal part entirely, giving the whole number", "round_dp",
     lambda o: F(o["whole"]),
     lambda o: f"{o['whole']} × 1")
_reg("rd_keeps_all_digits", "Does not round and keeps all the decimal digits", "round_dp",
     lambda o: F(o["whole"] * 100 + o["d1"] * 10 + o["d2"], 100),
     lambda o: f"({o['whole']} × 100 + {o['d1']} × 10 + {o['d2']})/100")

# convert_dec_pct: 0.ab -> percentage  (correct = 10a+b)
_reg("cp_move_one_place", "Moves the decimal one place instead of two", "convert_dec_pct",
     lambda o: F(o["a"] * 10 + o["b"], 10),
     lambda o: f"({o['a']} × 10 + {o['b']})/10")
_reg("cp_move_three_places", "Moves the decimal three places instead of two", "convert_dec_pct",
     lambda o: F((o["a"] * 10 + o["b"]) * 10),
     lambda o: f"({o['a']} × 10 + {o['b']}) × 10")
_reg("cp_first_digit_only", "Only scales the first decimal digit", "convert_dec_pct",
     lambda o: F(o["a"] * 10),
     lambda o: f"{o['a']} × 10")
_reg("cp_divide_instead", "Divides by 100 instead of multiplying", "convert_dec_pct",
     lambda o: F(o["a"] * 10 + o["b"], 1000),
     lambda o: f"({o['a']} × 10 + {o['b']})/1000")

# dec_addsub: 0.a + 0.0b  (correct = (10a+b)/100)
_reg("da_align_wrong", "Does not align place values and adds both as tenths", "dec_addsub",
     lambda o: F(o["a"] + o["b"], 10),
     lambda o: f"({o['a']} + {o['b']})/10")
_reg("da_ignore_point", "Ignores the decimal points and adds as whole numbers", "dec_addsub",
     lambda o: F(o["a"] + o["b"]),
     lambda o: f"{o['a']} + {o['b']}")
_reg("da_subtract_instead", "Subtracts instead of adding", "dec_addsub",
     lambda o: F(o["a"] * 10 - o["b"], 100),
     lambda o: f"({o['a']} × 10 - {o['b']})/100")

# indices: a^m × a^n  (correct = a^(m+n))
_reg("idx_mul_exponents", "Multiplies the exponents instead of adding them", "indices",
     lambda o: F(o["a"] ** (o["m"] * o["n"])),
     lambda o: f"{o['a']}^({o['m']} × {o['n']})")
_reg("idx_add_all", "Adds the base and both exponents", "indices",
     lambda o: F(o["a"] + o["m"] + o["n"]),
     lambda o: f"{o['a']} + {o['m']} + {o['n']}")
_reg("idx_base_times_sum", "Multiplies the base by the sum of the exponents", "indices",
     lambda o: F(o["a"] * (o["m"] + o["n"])),
     lambda o: f"{o['a']} × ({o['m']} + {o['n']})")

# hcf: highest common factor of a, b  (correct = g)
_reg("hcf_product", "Multiplies the two numbers instead of finding the common factor", "hcf",
     lambda o: F(o["a"] * o["b"]),
     lambda o: f"{o['a']} × {o['b']}")
_reg("hcf_sum", "Adds the two numbers", "hcf",
     lambda o: F(o["a"] + o["b"]),
     lambda o: f"{o['a']} + {o['b']}")
_reg("hcf_difference", "Gives the difference of the two numbers", "hcf",
     lambda o: F(abs(o["a"] - o["b"])),
     lambda o: f"{max(o['a'], o['b'])} - {min(o['a'], o['b'])}")

# mental_add: a + b, a>b, b!=5  (correct = a+b)
_reg("ma_subtract_instead", "Subtracts the second number instead of adding it", "mental_add",
     lambda o: F(o["a"] - o["b"]),
     lambda o: f"{o['a']} - {o['b']}")
_reg("ma_forgets_ten", "Forgets to carry a ten", "mental_add",
     lambda o: F(o["a"] + o["b"] - 10),
     lambda o: f"{o['a']} + {o['b']} - 10")
_reg("ma_adds_extra_ten", "Carries an extra ten by mistake", "mental_add",
     lambda o: F(o["a"] + o["b"] + 10),
     lambda o: f"{o['a']} + {o['b']} + 10")

# mental_mult: a × b, a in 11..19  (correct = a*b)
_reg("mm_add_instead", "Adds the numbers instead of multiplying", "mental_mult",
     lambda o: F(o["a"] + o["b"]),
     lambda o: f"{o['a']} + {o['b']}")
_reg("mm_ignore_tens", "Multiplies only the units digit, ignoring the tens", "mental_mult",
     lambda o: F((o["a"] - 10) * o["b"]),
     lambda o: f"({o['a']} - 10) × {o['b']}")
_reg("mm_off_by_one_factor", "Multiplies by one less than the second number", "mental_mult",
     lambda o: F(o["a"] * (o["b"] - 1)),
     lambda o: f"{o['a']} × ({o['b']} - 1)")


# The original 8 families (v1-v4). generate() is pinned to these so legacy dataset builds
# stay byte-identical after v5 added 8 more families to FAMILIES. v5 uses all of FAMILIES.
LEGACY_FAMILIES = [
    "fraction_add", "fraction_mul", "fraction_div_int", "order_of_ops",
    "neg_add", "decimal_mul", "square", "percent_of",
]


def _mcs_for(family: str) -> List[Misconception]:
    return [m for m in REGISTRY.values() if m.family == family]


def make_question(family: str, r: random.Random) -> Question:
    spec = FAMILIES[family]
    o = spec["gen"](r)
    return Question(family, o, spec["correct"](o), spec["text"](o), spec["topic"])


def generate_example(r: random.Random, family: Optional[str] = None, tries: int = 40,
                     families: Optional[List[str]] = None) -> Optional[dict]:
    # `family` (single) takes priority; else `families` (list); else ALL families.
    if family is not None:
        fams = [family]
    elif families is not None:
        fams = families
    else:
        fams = list(FAMILIES)
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
                {
                    "misconception_id": m.id,
                    "misconception": m.name,
                    "computation": f"{m.comp(q.operands)} = {fmt(v)}" if m.comp else "",
                    "answer": fmt(v),
                }
                for (m, v) in picked
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
            print(f"    {d['computation']:>22}   <-  {d['misconception']}")
