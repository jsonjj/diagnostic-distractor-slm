"""Answer-string normalization: strip LaTeX wrappers, \\frac, thousands commas, whitespace."""
from __future__ import annotations

import re

_FRAC = re.compile(r"\\[a-zA-Z]*frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")


def normalize_answer(s) -> str:
    """Reduce an answer to a clean, comparable plain-text form.

    Examples: '\\( 880,000 \\)' -> '880000', '\\( \\frac{2}{9} \\)' -> '2/9', '0.06' -> '0.06'.
    """
    s = str(s).strip()
    for tok in ("\\(", "\\)", "\\[", "\\]", "$"):
        s = s.replace(tok, "")
    s = _FRAC.sub(r"\1/\2", s)
    s = s.replace("\\", "").replace(",", "")
    s = "".join(s.split())
    return s
