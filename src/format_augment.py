"""v7 format augmentation: render a plain engine question in real-Eedi STYLE.

The engine emits clean templates ("What is 0.15 × 0.2?"), but the eval questions are real Eedi
items wrapped in LaTeX ("\\( 0.15 \\times 0.2= \\)"), sometimes bold, sometimes with a "What is"
stem, sometimes bare. The model trained on clean text has to transfer to LaTeX at test time --
a real synthetic->real gap that likely caps consistency. This module rewrites a question into a
randomly chosen real-style variant so the model trains on the format it is tested on.

TRAINING-ONLY: this runs offline when building the v7 dataset. It does NOT ship with the model.
It only changes the QUESTION STRING presentation; the operands, answer, misconception, and
computation are untouched, so consistency-by-construction is preserved.
"""
from __future__ import annotations

import re

# map the engine's plain operators to LaTeX
_TEX = [("×", "\\times "), ("÷", "\\div "), ("²", "^{2}")]


def _to_latex_expr(s: str) -> str:
    out = s
    for a, b in _TEX:
        out = out.replace(a, b)
    return re.sub(r"\s{2,}", " ", out).strip()  # collapse double spaces from operator swaps


def _extract_core(question: str):
    """Pull the math 'core' out of a 'What is <core>?' template; else return None.

    Only matches PURE arithmetic cores (digits, operators, fractions, parens, decimals, ²) so
    worded stems ('What is the value of the digit 4 in 3467?') and non-math phrasings
    ('15% of 80') are left untouched -- LaTeX-wrapping words or % reads wrong.
    """
    m = re.match(r"^What is (.+?)\s*\??$", question.strip())
    if not m:
        return None
    core = m.group(1).strip()
    # accept only if the core is arithmetic-only: digits, / + - × ÷ ² ( ) . and spaces
    if re.fullmatch(r"[0-9+\-×÷²/().\s]+", core):
        return core
    return None


def stylize(question: str, r) -> str:
    """Return a real-Eedi-style rendering of a plain engine question. `r` is a random.Random.

    Deterministic given r. Falls back to the original string for questions that don't fit the
    "What is X?" mould (place-value / rounding / worded stems), which already read naturally.
    """
    core = _extract_core(question)
    if core is None:
        # already a worded stem (e.g. "What is the value of the digit 4 in 3467?") -- light touch:
        # occasionally wrap trailing simple numbers, else leave as-is.
        return question

    tex = _to_latex_expr(core)
    # style menu mirrors real Eedi phrasings (see eval_heldout samples)
    styles = [
        lambda: f"\\( {tex} = \\)",                        # bare inline: "\( 0.15 \times 0.2 = \)"
        lambda: f"\\( {tex} \\)",                          # bare, no equals
        lambda: f"What is \\( {tex} \\)?",                 # stem + latex
        lambda: f"Work out \\( {tex} \\)",                 # imperative
        lambda: f"Calculate \\( {tex} \\)",                # imperative variant
        lambda: question,                                  # keep some plain (robustness)
    ]
    return r.choice(styles)()


if __name__ == "__main__":
    import random
    r = random.Random(0)
    for q in ["What is 0.15 × 0.2?", "What is 3/4 + 1/6?", "What is 12²?",
              "What is 15% of 80?", "What is the value of the digit 4 in 3467?"]:
        print(f"{q!r}")
        for _ in range(3):
            print("   ->", repr(stylize(q, r)))
