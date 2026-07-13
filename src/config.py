"""Central config: paths, TrueFoundry gateway settings, and the locked Number-strand scope."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

# --- Paths ---
ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"


def resolve_project_environment(
    process_values: Mapping[str, str],
    dotenv_values: Mapping[str, Optional[str]],
) -> dict:
    """Merge configuration with the repository ``.env`` taking precedence.

    Cursor/terminal processes can retain an old exported token after the owner
    rotates ``.env``. The project-local file is the documented credential source,
    so non-empty values from it must replace stale inherited values.
    """
    resolved = {
        str(key): str(value)
        for key, value in process_values.items()
        if value is not None
    }
    for key, value in dotenv_values.items():
        if value is not None and str(value).strip():
            resolved[str(key)] = str(value)
    return resolved


def _read_project_dotenv() -> dict:
    path = ROOT / ".env"
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise RuntimeError(
            "A project .env exists but python-dotenv is not installed. "
            "Install requirements.txt rather than silently using inherited secrets."
        ) from exc
    return dict(dotenv_values(path))


_DOTENV_VALUES = _read_project_dotenv()
_CONFIG_ENV = resolve_project_environment(os.environ, _DOTENV_VALUES)

# --- TrueFoundry AI Gateway (OpenAI-compatible): teacher + judge + frontier baseline ---
SONNET_BASELINE_MODEL_ID = "claude-sonnet-5"
OPUS_MODEL_ID = "anthropic-primary/claude-opus-4-8"


def resolve_tfy_models(env: Optional[Mapping[str, str]] = None) -> dict:
    """Resolve role-specific models while preserving the legacy single-model setting."""
    values = _CONFIG_ENV if env is None else env
    legacy = values.get("TFY_MODEL", SONNET_BASELINE_MODEL_ID)
    return {
        "legacy": legacy,
        "teacher": values.get("TFY_TEACHER_MODEL", legacy),
        "judge": values.get("TFY_JUDGE_MODEL", legacy),
        "frontier": values.get("TFY_FRONTIER_MODEL", legacy),
    }


_TFY_MODELS = resolve_tfy_models()
TFY_BASE_URL = _CONFIG_ENV.get(
    "TFY_BASE_URL",
    "https://tfy-eu.promptlens.trilogy.com",
)
TFY_MODEL = _TFY_MODELS["legacy"]
TFY_TEACHER_MODEL = _TFY_MODELS["teacher"]
TFY_JUDGE_MODEL = _TFY_MODELS["judge"]
TFY_FRONTIER_MODEL = _TFY_MODELS["frontier"]
TFY_API_KEY = _CONFIG_ENV.get("TFY_API_KEY", "")
TFY_CREDENTIAL_SOURCE = (
    "dotenv"
    if _DOTENV_VALUES.get("TFY_API_KEY")
    else ("environment" if TFY_API_KEY else "unset")
)
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
