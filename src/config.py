"""Central config: paths, TrueFoundry gateway settings, and the locked Number-strand scope."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# --- Paths ---
ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"

# --- TrueFoundry AI Gateway (OpenAI-compatible): teacher + judge + frontier baseline ---
TFY_BASE_URL = os.getenv("TFY_BASE_URL", "https://tfy-eu.promptlens.trilogy.com")
TFY_MODEL = os.getenv("TFY_MODEL", "anthropic-primary/claude-sonnet-5")
TFY_API_KEY = os.getenv("TFY_API_KEY", "")
TFY_EXTRA_HEADERS = {
    "X-TFY-METADATA": "{}",
    "X-TFY-LOGGING-CONFIG": '{"enabled": true}',
}

# --- Locked project scope: the "Number" strand (34 vetted Eedi subjects) ---
NUMBER_SUBJECTS = {
    "BIDMAS",
    "Place Value",
    "Multiplying and Dividing with Decimals",
    "Adding and Subtracting Fractions",
    "Mental Multiplication and Division",
    "Mental Addition and Subtraction",
    "Percentages of an Amount",
    "Squares, Cubes, etc",
    "Adding and Subtracting Negative Numbers",
    "Rounding to the Nearest Whole (10, 100, etc)",
    "Rounding to Decimal Places",
    "Factors and Highest Common Factor",
    "Adding and Subtracting with Decimals",
    "Square Roots, Cube Roots, etc",
    "Converting between Fractions and Percentages",
    "Dividing Fractions",
    "Multiplying and Dividing Negative Numbers",
    "Estimation",
    "Fractions of an Amount",
    "Rounding to Significant Figures",
    "Multiplying Fractions",
    "Converting between Decimals and Percentages",
    "Ordering Fractions",
    "Laws of Indices",
    "Ordering Negative Numbers",
    "Converting between Fractions and Decimals",
    "Converting Mixed Number and Improper Fractions",
    "Ordering Decimals",
    "Simplifying Fractions",
    "Multiples and Lowest Common Multiple",
    "Equivalent Fractions",
    "Standard Form",
    "Recurring Decimals to Fractions",
    "Percentage Increase and Decrease",
}
