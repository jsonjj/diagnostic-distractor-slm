"""Deterministic numeric-only recalibration for the frozen v8 evaluation.

Eligibility is derived only from trusted gold question/correct-answer records.
Model outputs, automatic scores, flags, ratings, and winners are never inputs to
the scope classifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Mapping, Optional, Sequence

from services.wayline_forge.app.safe_numeric import (
    MAX_ABSOLUTE,
    NumericParseError,
    parse_exact_value,
)

from .benchmark_v8 import (
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    ERROR_REDUCTION_TARGET,
    _cluster_rate_ci,
    _computation_clusters,
    paired_bootstrap_ratio_compare,
    wilson_interval,
)
from .score_blinded_review import (
    BOOTSTRAP_SAMPLES as HUMAN_BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED as HUMAN_BOOTSTRAP_SEED,
    DIMENSIONS,
    score_reviews,
)
from .text_utils import normalize_answer


NUMERIC_SCOPE_RULE_VERSION = "v8-gold-numeric-eligibility-v1"
SYSTEM_ORDER = ("opus", "v8_model_only", "v8_best_of_n")
MEASURED_METRICS = (
    "valid_exactly_3_json",
    "none_equals_key",
    "distinct_answers",
    "distinct_misconceptions",
    "hardened_computation_validity",
)

_LATEX_FRACTION = re.compile(
    r"\\(?:d?frac)\s*\{([+-]?[0-9]+)\}\s*\{([0-9]+)\}"
)
_ROMAN_NUMERAL = re.compile(r"[IVXLCDM]+", re.IGNORECASE)
_PLAIN_NUMERICISH = re.compile(r"[+\-0-9.,/%^{}()[\]]+")
_UNIT_SUFFIX = re.compile(
    r"(?P<number>.+?)"
    r"(?P<unit>"
    r"(?:mathrm|text)\{~?(?:km|cm|mm|kg|ml|m|g|l)\}"
    r"|(?:km|cm|mm|kg|ml|m|g|l)"
    r")"
    r"(?:\^\{?[23]\}?)?"
)


@dataclass(frozen=True, slots=True)
class ParsedNumeric:
    """One exact numeric display value and its supported representation."""

    value: Fraction
    kind: str
    normalized: str


def _bounded(value: Fraction) -> Optional[Fraction]:
    value = Fraction(value)
    return value if abs(value) <= MAX_ABSOLUTE else None


def _parse_plain(token: str, *, allow_percent: bool = False) -> Optional[Fraction]:
    try:
        return parse_exact_value(token, allow_percent=allow_percent).value
    except NumericParseError:
        return None


def _mixed_number(value: str) -> Optional[Fraction]:
    preserved = str(value).strip()
    for wrapper in ("\\(", "\\)", "\\[", "\\]", "$"):
        preserved = preserved.replace(wrapper, "")
    preserved = _LATEX_FRACTION.sub(r"\1/\2", preserved)
    preserved = preserved.replace(",", "")
    preserved = re.sub(r"\s+", " ", preserved).strip()
    match = re.fullmatch(r"([+-]?)([0-9]+)\s+([0-9]+)/([0-9]+)", preserved)
    if not match:
        return None
    sign_text, whole_text, numerator_text, denominator_text = match.groups()
    denominator = int(denominator_text)
    numerator = int(numerator_text)
    if denominator == 0 or numerator >= denominator:
        return None
    value_fraction = Fraction(int(whole_text) * denominator + numerator, denominator)
    return -value_fraction if sign_text == "-" else value_fraction


def _integer_nth_root(value: int, degree: int) -> Optional[int]:
    if degree <= 0 or degree > 12:
        return None
    if value < 0:
        if degree % 2 == 0:
            return None
        root = _integer_nth_root(-value, degree)
        return -root if root is not None else None
    low, high = 0, max(1, value)
    while low <= high:
        middle = (low + high) // 2
        powered = middle**degree
        if powered == value:
            return middle
        if powered < value:
            low = middle + 1
        else:
            high = middle - 1
    return None


def _exact_root(value: Fraction, degree: int) -> Optional[Fraction]:
    numerator = _integer_nth_root(value.numerator, degree)
    denominator = _integer_nth_root(value.denominator, degree)
    if numerator is None or denominator is None:
        return None
    return Fraction(numerator, denominator)


def parse_numeric_display(value: object) -> Optional[ParsedNumeric]:
    """Parse one supported gold/option display into an exact comparison value.

    Percent, currency, and measurement units retain their displayed magnitude,
    matching the repository's existing display-value logic. Arbitrary arithmetic
    expressions are deliberately rejected; only scalar values, exact mixed
    numbers, exact repeating decimals, powers, roots, and standard form are
    accepted.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or "![" in raw:
        return None

    mixed = _mixed_number(raw)
    if mixed is not None and _bounded(mixed) is not None:
        return ParsedNumeric(mixed, "mixed_number", normalize_answer(raw))

    compact = normalize_answer(raw)
    if not compact:
        return None

    repeating = re.fullmatch(
        r"([+-]?)([0-9]+)\.([0-9]+)(?:ldots|\.{3}|…)",
        compact,
    )
    if repeating:
        sign_text, whole_text, decimal_text = repeating.groups()
        if len(set(decimal_text)) == 1:
            magnitude = Fraction(int(whole_text)) + Fraction(int(decimal_text[0]), 9)
            exact = -magnitude if sign_text == "-" else magnitude
            if _bounded(exact) is not None:
                return ParsedNumeric(exact, "repeating_decimal", compact)
        return None

    root = re.fullmatch(
        r"sqrt(?:\[([0-9]+)\])?\{([+-]?[0-9]+(?:\.[0-9]+)?(?:/[0-9]+)?)\}",
        compact,
    )
    if root:
        degree = int(root.group(1) or "2")
        radicand = _parse_plain(root.group(2))
        exact = _exact_root(radicand, degree) if radicand is not None else None
        if exact is not None and _bounded(exact) is not None:
            return ParsedNumeric(exact, "root", compact)
        return None

    expression = (
        compact.replace("×", "times")
        .replace("·", "times")
        .replace("cdot", "times")
    )
    standard_form = re.fullmatch(
        r"([+-]?[0-9]+(?:\.[0-9]+)?)times10\^\{?([+-]?[0-9]+)\}?",
        expression,
    )
    if standard_form:
        coefficient = _parse_plain(standard_form.group(1))
        exponent = int(standard_form.group(2))
        if coefficient is None or abs(exponent) > 12:
            return None
        exact = coefficient * (
            Fraction(10**exponent) if exponent >= 0 else Fraction(1, 10 ** (-exponent))
        )
        if _bounded(exact) is not None:
            return ParsedNumeric(exact, "standard_form", compact)
        return None

    power = re.fullmatch(
        r"([+-]?[0-9]+(?:\.[0-9]+)?)\^\{?([+-]?[0-9]+)\}?",
        compact,
    )
    if power:
        base = _parse_plain(power.group(1))
        exponent = int(power.group(2))
        if base is None or abs(exponent) > 12:
            return None
        try:
            exact = base**exponent
        except (OverflowError, ZeroDivisionError):
            return None
        if _bounded(exact) is not None:
            return ParsedNumeric(exact, "power", compact)
        return None

    if compact.startswith(("£", "pounds")):
        prefix = "£" if compact.startswith("£") else "pounds"
        exact = _parse_plain(compact[len(prefix) :])
        if exact is not None:
            return ParsedNumeric(exact, "currency", compact)
        return None

    measurement = _UNIT_SUFFIX.fullmatch(compact)
    if measurement:
        exact = _parse_plain(measurement.group("number"))
        if exact is not None:
            return ParsedNumeric(exact, "measurement", compact)
        return None

    percent = compact.endswith("%")
    exact = _parse_plain(compact, allow_percent=percent)
    if exact is None:
        return None
    if percent:
        kind = "percentage"
    elif "/" in compact:
        kind = "fraction"
    elif "." in compact:
        kind = "decimal"
    else:
        kind = "integer"
    return ParsedNumeric(exact, kind, compact)


def _numeric_value_payload(parsed: ParsedNumeric) -> dict:
    return {
        "numerator": parsed.value.numerator,
        "denominator": parsed.value.denominator,
    }


def classify_numeric_item(item: Mapping[str, object]) -> dict:
    """Classify one item from trusted gold fields only."""
    item_id = str(item.get("id", ""))
    gold_answer = str(item.get("correct", ""))
    parsed = parse_numeric_display(gold_answer)
    if parsed is not None:
        return {
            "id": item_id,
            "gold_answer": gold_answer,
            "included": True,
            "reason": f"numeric_{parsed.kind}",
            "representation": parsed.kind,
            "numeric_value": _numeric_value_payload(parsed),
        }

    compact = normalize_answer(gold_answer)
    folded = compact.casefold()
    if "![" in gold_answer:
        reason = "image_only_answer"
    elif _ROMAN_NUMERAL.fullmatch(compact):
        reason = "roman_numeral_answer"
    elif re.search(r"(?:only|both|neither)", folded):
        reason = "named_person_or_categorical_answer"
    elif re.search(r"(?:sometimes|always|never|true|false|yes|no)", folded):
        reason = "yes_no_or_truth_answer"
    elif "and" in folded and any(char.isdigit() for char in folded):
        reason = "compound_multi_value_answer"
    elif re.search(r"[0-9]\^\{?text\{?(?:st|nd|rd|th)", folded):
        reason = "named_or_ordered_text_answer"
    elif (
        "then" in folded
        or any(symbol in compact for symbol in ("<", ">", "="))
        or re.search(r"[0-9)](?:\+|-|\*|times|×|÷)[(0-9]", folded)
    ):
        reason = "operation_choice_text"
    elif _PLAIN_NUMERICISH.fullmatch(compact):
        reason = "unparseable_numeric_key"
    elif re.search(r"[a-z].*[0-9]|[0-9].*[a-z]", folded):
        reason = "algebraic_or_text_answer"
    else:
        reason = "verbal_or_concept_answer"
    return {
        "id": item_id,
        "gold_answer": gold_answer,
        "included": False,
        "reason": reason,
        "representation": None,
        "numeric_value": None,
    }


def _canonical_gold_hash(gold: Sequence[Mapping[str, object]]) -> str:
    payload = [
        {
            "id": str(item.get("id", "")),
            "question": str(item.get("question", "")),
            "correct": str(item.get("correct", "")),
        }
        for item in sorted(gold, key=lambda row: str(row.get("id", "")))
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_scope_manifest(
    gold: Sequence[Mapping[str, object]],
    hidden_key: Optional[Mapping[str, object]] = None,
) -> dict:
    """Build an order-independent audit manifest for frozen and blind items."""
    ids = [str(item.get("id", "")) for item in gold]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("gold IDs must be non-empty and unique")
    gold_by_id = {str(item["id"]): item for item in gold}
    frozen_items = [
        {
            **classify_numeric_item(item),
            "topic": str(item.get("topic", "")),
        }
        for item in sorted(gold, key=lambda row: str(row.get("id", "")))
    ]
    included_ids = [item["id"] for item in frozen_items if item["included"]]
    excluded_ids = [item["id"] for item in frozen_items if not item["included"]]
    manifest = {
        "schema_version": "v8-numeric-scope-manifest-v1",
        "eligibility_rule": {
            "version": NUMERIC_SCOPE_RULE_VERSION,
            "source_fields": ["id", "question", "correct", "topic"],
            "uses_model_outputs": False,
            "uses_flags_ratings_or_winner": False,
            "included_representations": [
                "integer",
                "signed integer",
                "decimal",
                "fraction",
                "mixed number",
                "percentage display magnitude",
                "currency/measurement numeric magnitude",
                "exact repeating decimal",
                "bounded integer power",
                "exact root",
                "standard-form numeric expression",
            ],
            "excluded_classes": [
                "named-person, categorical, verbal, yes/no, and truth answers",
                "operation-choice and comparison-sign text",
                "image-only answers",
                "compound multi-value and ordered-label answers",
                "Roman-numeral keys",
                "algebraic or otherwise unparseable nonnumeric keys",
            ],
            "numeric_equivalence": (
                "Exact Fraction equality after deterministic display parsing; "
                "percent/currency/measurement suffixes retain displayed magnitude."
            ),
        },
        "trusted_gold_sha256": _canonical_gold_hash(gold),
        "frozen_scope": {
            "total": len(frozen_items),
            "included": len(included_ids),
            "excluded": len(excluded_ids),
            "included_ids": included_ids,
            "excluded_ids": excluded_ids,
            "items": frozen_items,
        },
    }

    if hidden_key is not None:
        key_items = hidden_key.get("items")
        if not isinstance(key_items, list):
            raise ValueError("hidden key items must be a list")
        blind_items = []
        for key_row in sorted(
            key_items,
            key=lambda row: str(row.get("review_item_id", "")),
        ):
            review_item_id = str(key_row.get("review_item_id", ""))
            source_id = str(key_row.get("source_id", ""))
            if not review_item_id or source_id not in gold_by_id:
                raise ValueError("blind-review item is missing trusted gold")
            classification = classify_numeric_item(gold_by_id[source_id])
            blind_items.append(
                {
                    "review_item_id": review_item_id,
                    "source_id": source_id,
                    "gold_answer": classification["gold_answer"],
                    "included": classification["included"],
                    "reason": classification["reason"],
                    "representation": classification["representation"],
                    "numeric_value": classification["numeric_value"],
                }
            )
        blind_included = [
            item["review_item_id"] for item in blind_items if item["included"]
        ]
        blind_excluded = [
            item["review_item_id"] for item in blind_items if not item["included"]
        ]
        manifest["blind_review_scope"] = {
            "total": len(blind_items),
            "included": len(blind_included),
            "excluded": len(blind_excluded),
            "included_review_item_ids": blind_included,
            "excluded_review_item_ids": blind_excluded,
            "included_source_ids": [
                item["source_id"] for item in blind_items if item["included"]
            ],
            "excluded_source_ids": [
                item["source_id"] for item in blind_items if not item["included"]
            ],
            "items": blind_items,
        }
    return manifest


def _equivalence_key(value: object) -> tuple[str, object]:
    parsed = parse_numeric_display(value)
    if parsed is not None:
        return ("numeric", parsed.value)
    return ("text", normalize_answer(value).casefold())


def _numeric_structural_values(
    gold: Sequence[Mapping[str, object]],
    predictions: Sequence[Mapping[str, object]],
) -> dict[str, list[bool]]:
    prediction_map = {
        str(row.get("id")): row
        for row in predictions
        if row.get("id") not in (None, "")
    }
    values = {
        "valid_exactly_3_json": [],
        "distinct_misconceptions": [],
        "none_equals_key": [],
        "distinct_answers": [],
    }
    for item in gold:
        row = prediction_map.get(str(item.get("id")), {})
        distractors = row.get("distractors", [])
        if not isinstance(distractors, list):
            distractors = []
        valid = len(distractors) == 3 and all(
            isinstance(distractor, dict)
            and str(distractor.get("misconception", "")).strip()
            and normalize_answer(distractor.get("answer", ""))
            for distractor in distractors
        )
        answers = [
            distractor.get("answer", "")
            for distractor in distractors
            if isinstance(distractor, dict)
        ]
        answer_keys = [_equivalence_key(answer) for answer in answers]
        misconceptions = [
            str(distractor.get("misconception", "")).strip().casefold()
            for distractor in distractors
            if isinstance(distractor, dict)
        ]
        correct_key = _equivalence_key(item.get("correct", ""))
        values["valid_exactly_3_json"].append(bool(valid))
        values["distinct_misconceptions"].append(
            bool(valid and len(set(misconceptions)) == 3)
        )
        values["none_equals_key"].append(
            bool(valid and all(answer_key != correct_key for answer_key in answer_keys))
        )
        values["distinct_answers"].append(
            bool(valid and len(set(answer_keys)) == 3)
        )
    return values


def _measured_item_rate(values: Sequence[bool], *, method: str) -> dict:
    numerator = sum(bool(value) for value in values)
    denominator = len(values)
    return {
        "score": 100 * numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "ci95": wilson_interval(numerator, denominator),
        "status": "MEASURED" if denominator else "UNAVAILABLE",
        "method": method,
        "direction": "higher_is_better",
    }


def _unavailable_metric(
    method: str,
    note: str,
    *,
    direction: str = "higher_is_better",
) -> dict:
    return {
        "score": None,
        "numerator": None,
        "denominator": None,
        "ci95": None,
        "status": "UNAVAILABLE",
        "method": method,
        "direction": direction,
        "note": note,
    }


def _unavailable_metrics() -> dict:
    return {
        "good_distractor_rate": _unavailable_metric(
            "all-gates pair metric",
            "No complete independent binding and diagnostic-quality verdicts exist.",
        ),
        "good_at_3": _unavailable_metric(
            "all-three all-gates item metric",
            "GDR is unavailable, so Good@3 cannot be computed.",
        ),
        "numeric_binding_consistency": _unavailable_metric(
            "accepted programmatic or independently calibrated binding verdicts",
            "No complete legitimate binding sidecar exists for all three systems.",
        ),
        "diagnostic_quality_proxy": _unavailable_metric(
            "independent diagnostic-quality proxy",
            "No accepted independent pair-level quality verdicts or student pick rates exist.",
        ),
        "selective_gdr_at_80pct_coverage": _unavailable_metric(
            "calibrated confidence over GDR",
            "GDR and accepted calibrated pair confidence are unavailable.",
        ),
        "confidence_ece": _unavailable_metric(
            "accepted out-of-fold binding calibration",
            "The registered calibration was rejected; ECE is not fabricated.",
            direction="lower_is_better",
        ),
        "confidence_brier": _unavailable_metric(
            "accepted out-of-fold binding calibration",
            "The registered calibration was rejected; Brier score is not fabricated.",
            direction="lower_is_better",
        ),
    }


def _supports_computation_metric(predictions: Sequence[Mapping[str, object]]) -> bool:
    return any(row.get("generator_model") for row in predictions) or any(
        str(distractor.get("computation", "")).strip()
        for row in predictions
        for distractor in (
            row.get("distractors", [])
            if isinstance(row.get("distractors", []), list)
            else []
        )
        if isinstance(distractor, dict)
    )


def _numeric_gold(gold: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [item for item in gold if classify_numeric_item(item)["included"]]


def numeric_primary_metrics(
    gold: Sequence[Mapping[str, object]],
    predictions: Sequence[Mapping[str, object]],
) -> dict:
    """Compute deterministic metrics on the gold-defined numeric subset."""
    eligible = _numeric_gold(gold)
    metrics = _unavailable_metrics()
    structure = _numeric_structural_values(eligible, predictions)
    for name, values in structure.items():
        method = (
            "deterministic local gate with exact numeric answer equivalence"
            if name in {"none_equals_key", "distinct_answers"}
            else "deterministic local gate"
        )
        metrics[name] = _measured_item_rate(values, method=method)

    if _supports_computation_metric(predictions):
        clusters = _computation_clusters(eligible, predictions)
        numerator = sum(
            sum(bool(value) for value in cluster) for cluster in clusters
        )
        denominator = sum(len(cluster) for cluster in clusters)
        metrics["hardened_computation_validity"] = {
            "score": 100 * numerator / denominator if denominator else None,
            "numerator": numerator,
            "denominator": denominator,
            "ci95": _cluster_rate_ci(clusters),
            "status": "MEASURED" if denominator else "UNAVAILABLE",
            "method": (
                "exact arithmetic + question grounding; "
                "question-cluster bootstrap interval"
            ),
            "direction": "higher_is_better",
        }
    else:
        metrics["hardened_computation_validity"] = _unavailable_metric(
            "exact arithmetic + question grounding",
            "Prediction schema has no computation field.",
        )
    return metrics


def numeric_compare_systems(
    gold: Sequence[Mapping[str, object]],
    candidate_predictions: Sequence[Mapping[str, object]],
    baseline_predictions: Sequence[Mapping[str, object]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Paired question-bootstrap comparison on numeric-scope deterministic gates."""
    eligible = _numeric_gold(gold)
    candidate_structure = _numeric_structural_values(eligible, candidate_predictions)
    baseline_structure = _numeric_structural_values(eligible, baseline_predictions)
    vectors: dict[str, tuple[Optional[list[list[bool]]], Optional[list[list[bool]]]]] = {
        name: (
            [[value] for value in candidate_structure[name]],
            [[value] for value in baseline_structure[name]],
        )
        for name in candidate_structure
    }
    if _supports_computation_metric(candidate_predictions) and _supports_computation_metric(
        baseline_predictions
    ):
        vectors["hardened_computation_validity"] = (
            _computation_clusters(eligible, candidate_predictions),
            _computation_clusters(eligible, baseline_predictions),
        )
    else:
        vectors["hardened_computation_validity"] = (None, None)

    comparisons = {
        name: {
            "status": "UNAVAILABLE",
            "note": metric["note"],
        }
        for name, metric in _unavailable_metrics().items()
    }
    for name, (candidate, baseline) in vectors.items():
        if candidate is None or baseline is None or not candidate or not baseline:
            comparisons[name] = {"status": "UNAVAILABLE"}
            continue
        result = paired_bootstrap_ratio_compare(
            candidate,
            baseline,
            samples=samples,
            seed=seed,
        )
        result["status"] = "MEASURED"
        reduction = result["error_reduction"]
        interval = result["error_reduction_ci95"]
        result["meets_40pct_error_reduction_point"] = (
            reduction is not None and reduction >= ERROR_REDUCTION_TARGET
        )
        result["meets_40pct_error_reduction_ci"] = (
            interval is not None
            and interval[0] is not None
            and interval[0] >= ERROR_REDUCTION_TARGET
        )
        comparisons[name] = result
    return comparisons


def _reconstructed_rating_payloads(
    blind_result: Mapping[str, object],
    key_by_item: Mapping[str, Mapping[str, object]],
    included_review_ids: set[str],
    sources: Sequence[str],
) -> list[dict]:
    item_results = blind_result.get("item_results")
    if not isinstance(item_results, list):
        raise ValueError("blind result has no item results")
    grouped: dict[str, dict[str, Mapping[str, object]]] = defaultdict(dict)
    for item in item_results:
        reviewer = str(item.get("reviewer_code", "")).strip()
        review_item_id = str(item.get("review_item_id", ""))
        if review_item_id in included_review_ids:
            grouped[reviewer][review_item_id] = item
    if not grouped or "" in grouped:
        raise ValueError("blind result has invalid reviewer codes")

    payloads = []
    for reviewer in sorted(grouped):
        rows = []
        if set(grouped[reviewer]) != included_review_ids:
            raise ValueError(f"{reviewer}: numeric blind subset is incomplete")
        for review_item_id in sorted(included_review_ids):
            item = grouped[reviewer][review_item_id]
            key_row = key_by_item[review_item_id]
            systems = item.get("systems")
            if not isinstance(systems, dict) or not all(
                source in systems for source in sources
            ):
                raise ValueError(f"{review_item_id}: source ratings are incomplete")

            def candidate(label: str) -> dict:
                source = str(key_row[f"candidate_{label}_source"])
                values = systems[source]
                return {
                    dimension: values[dimension] for dimension in DIMENSIONS
                } | {"issues": list(values.get("issues", []))}

            rows.append(
                {
                    "review_item_id": review_item_id,
                    "preference": str(item.get("blind_preference", "")),
                    "candidate_a": candidate("a"),
                    "candidate_b": candidate("b"),
                    "note": str(item.get("note", "")),
                }
            )
        payloads.append(
            {
                "schema_version": "blinded-ratings-v1",
                "reviewer_code": reviewer,
                "ratings": rows,
            }
        )
    return payloads


def build_human_numeric_subset(
    gold: Sequence[Mapping[str, object]],
    blind_result: Mapping[str, object],
    hidden_key: Mapping[str, object],
    *,
    bootstrap_samples: int = HUMAN_BOOTSTRAP_SAMPLES,
    seed: int = HUMAN_BOOTSTRAP_SEED,
) -> dict:
    """Filter the completed blind pack by gold eligibility, then rescore it."""
    manifest = build_scope_manifest(gold, hidden_key)
    blind_scope = manifest["blind_review_scope"]
    key_items = hidden_key.get("items")
    sources = list(hidden_key.get("source_labels") or [])
    if not isinstance(key_items, list) or len(sources) != 2:
        raise ValueError("hidden key must identify two sources and item mappings")
    key_by_item = {
        str(item.get("review_item_id", "")): item for item in key_items
    }
    included_review_ids = set(blind_scope["included_review_item_ids"])
    subset_key = dict(hidden_key)
    subset_key["items"] = [
        key_by_item[item_id] for item_id in sorted(included_review_ids)
    ]
    payloads = _reconstructed_rating_payloads(
        blind_result,
        key_by_item,
        included_review_ids,
        sources,
    )
    result = score_reviews(
        payloads,
        subset_key,
        bootstrap_samples=bootstrap_samples,
        seed=seed,
    )
    gold_answer_by_id = {
        str(item.get("id")): str(item.get("correct", "")) for item in gold
    }
    for item in result["item_results"]:
        item["gold_answer"] = gold_answer_by_id[item["source_id"]]
    result["scope"] = {
        "rule_version": NUMERIC_SCOPE_RULE_VERSION,
        "classification_uses_model_outputs": False,
        "total": blind_scope["total"],
        "included": blind_scope["included"],
        "excluded": blind_scope["excluded"],
        "included_review_item_ids": blind_scope["included_review_item_ids"],
        "included_source_ids": blind_scope["included_source_ids"],
        "excluded_review_item_ids": blind_scope["excluded_review_item_ids"],
        "excluded_source_ids": blind_scope["excluded_source_ids"],
        "excluded_items": [
            item for item in blind_scope["items"] if not item["included"]
        ],
    }
    first_source, second_source = result["design"]["source_order"]
    overall = result["paired_differences"]["overall"]
    if first_source == "v8_best_of_n" and second_source == "opus":
        opus_advantage = -overall["mean_difference"]
        ci = [
            -overall["bootstrap_ci95"][1],
            -overall["bootstrap_ci95"][0],
        ]
    elif first_source == "opus" and second_source == "v8_best_of_n":
        opus_advantage = overall["mean_difference"]
        ci = list(overall["bootstrap_ci95"])
    else:
        opus_advantage = None
        ci = None
    result["numeric_scope_interpretation"] = {
        "contrast": "opus minus v8_best_of_n",
        "overall_mean_advantage": opus_advantage,
        "bootstrap_ci95": ci,
        "previous_opus_human_quality_advantage_persists": (
            opus_advantage is not None
            and opus_advantage > 0
            and ci is not None
            and ci[0] > 0
        ),
        "exploratory": True,
        "note": (
            "One reviewer and a post-review gold-defined subset; uncertainty is "
            "exploratory even when the paired interval excludes zero."
        ),
    }
    return result


def _coverage(
    gold: Sequence[Mapping[str, object]],
    predictions: Sequence[Mapping[str, object]],
) -> dict:
    gold_ids = [str(item.get("id", "")) for item in gold]
    prediction_ids = [str(item.get("id", "")) for item in predictions]
    return {
        "rows": len(predictions),
        "unique_ids": len(set(prediction_ids)),
        "expected_rows": len(gold),
        "ids_match_frozen_in_order": prediction_ids == gold_ids,
        "complete": (
            prediction_ids == gold_ids
            and len(prediction_ids) == len(set(prediction_ids))
        ),
    }


def _opus_advantage_from_result(result: Mapping[str, object]) -> Optional[float]:
    design = result.get("design")
    paired = result.get("paired_differences")
    if not isinstance(design, dict) or not isinstance(paired, dict):
        return None
    source_order = list(design.get("source_order") or [])
    overall = paired.get("overall")
    if len(source_order) != 2 or not isinstance(overall, dict):
        return None
    difference = overall.get("mean_difference")
    if not isinstance(difference, (int, float)):
        return None
    first, second = source_order
    if first == "opus" and second == "v8_best_of_n":
        return float(difference)
    if first == "v8_best_of_n" and second == "opus":
        return -float(difference)
    return None


def build_numeric_report(
    gold: Sequence[Mapping[str, object]],
    predictions: Mapping[str, Sequence[Mapping[str, object]]],
    blind_result: Mapping[str, object],
    hidden_key: Mapping[str, object],
    *,
    benchmark_source: Optional[Mapping[str, object]] = None,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    human_bootstrap_samples: int = HUMAN_BOOTSTRAP_SAMPLES,
    human_bootstrap_seed: int = HUMAN_BOOTSTRAP_SEED,
) -> tuple[dict, dict]:
    """Build the numeric-only benchmark and its separately saved scope manifest."""
    missing = [name for name in SYSTEM_ORDER if name not in predictions]
    if missing:
        raise ValueError(f"missing systems: {missing}")
    coverage = {
        name: _coverage(gold, predictions[name]) for name in SYSTEM_ORDER
    }
    incomplete = [name for name, value in coverage.items() if not value["complete"]]
    if incomplete:
        raise ValueError(f"{incomplete[0]} coverage does not match frozen eval")
    if benchmark_source is not None:
        source_items = (
            benchmark_source.get("protocol", {}).get("frozen_items")
            if isinstance(benchmark_source.get("protocol"), dict)
            else None
        )
        if source_items is not None and int(source_items) != len(gold):
            raise ValueError("all-scope benchmark does not match frozen eval")

    manifest = build_scope_manifest(gold, hidden_key)
    frozen_scope = manifest["frozen_scope"]
    human = build_human_numeric_subset(
        gold,
        blind_result,
        hidden_key,
        bootstrap_samples=human_bootstrap_samples,
        seed=human_bootstrap_seed,
    )
    systems = {
        name: numeric_primary_metrics(gold, predictions[name])
        for name in SYSTEM_ORDER
    }
    comparisons = {
        f"{candidate}_vs_opus": numeric_compare_systems(
            gold,
            predictions[candidate],
            predictions["opus"],
            samples=bootstrap_samples,
            seed=bootstrap_seed,
        )
        for candidate in ("v8_model_only", "v8_best_of_n")
    }
    prior_opus_advantage = _opus_advantage_from_result(blind_result)
    numeric_opus_advantage = human["numeric_scope_interpretation"][
        "overall_mean_advantage"
    ]
    advantage_change = (
        numeric_opus_advantage - prior_opus_advantage
        if numeric_opus_advantage is not None
        and prior_opus_advantage is not None
        else None
    )
    human["comparison_to_all_scope"] = {
        "all_scope_items": (
            blind_result.get("design", {}).get("items")
            if isinstance(blind_result.get("design"), dict)
            else None
        ),
        "all_scope_opus_overall_advantage": prior_opus_advantage,
        "numeric_scope_opus_overall_advantage": numeric_opus_advantage,
        "change_in_opus_advantage": advantage_change,
        "interpretation": (
            "Positive values favor Opus. The numeric-scope estimate is the fair "
            "trained-domain human comparison; the all-scope estimate remains "
            "the original, broader exploratory result."
        ),
    }

    report = {
        "schema_version": "v8-numeric-benchmark-v1",
        "protocol": {
            "scope": "gold-defined numeric-eligible frozen questions",
            "eligibility_rule_version": NUMERIC_SCOPE_RULE_VERSION,
            "frozen_items": len(gold),
            "numeric_items": frozen_scope["included"],
            "excluded_items": frozen_scope["excluded"],
            "classification_uses_model_outputs": False,
            "classification_uses_flags_ratings_or_winner": False,
            "bootstrap_samples": bootstrap_samples,
            "bootstrap_seed": bootstrap_seed,
            "human_bootstrap_samples": human_bootstrap_samples,
            "human_bootstrap_seed": human_bootstrap_seed,
            "numeric_equivalence": manifest["eligibility_rule"][
                "numeric_equivalence"
            ],
            "roles": {
                "opus": "frontier generator reference",
                "v8_model_only": "deterministic greedy tuned 8B output",
                "v8_best_of_n": (
                    "four candidates plus deterministic verifier selection; "
                    "a system result, not model-only"
                ),
            },
        },
        "scope": {
            "manifest_schema_version": manifest["schema_version"],
            "trusted_gold_sha256": manifest["trusted_gold_sha256"],
            "included_ids": frozen_scope["included_ids"],
            "excluded_ids": frozen_scope["excluded_ids"],
            "excluded_reason_counts": dict(
                sorted(
                    Counter(
                        item["reason"]
                        for item in frozen_scope["items"]
                        if not item["included"]
                    ).items()
                )
            ),
        },
        "coverage": coverage,
        "systems": systems,
        "comparisons": comparisons,
        "human_review": human,
        "quality_metrics": {
            "gdr": {
                "status": "UNAVAILABLE",
                "note": "No complete legitimate pair-level all-gates judgments exist.",
            },
            "good_at_3": {
                "status": "UNAVAILABLE",
                "note": "GDR is unavailable.",
            },
            "diagnostic_quality_proxy": {
                "status": "UNAVAILABLE",
                "note": "No accepted independent pair-level proxy or student pick rates exist.",
            },
            "confidence_calibration": {
                "status": "UNAVAILABLE",
                "note": "The registered calibration was rejected.",
            },
        },
        "verdict": {
            "overall_numeric_scope": "NO HOLISTIC WINNER DEMONSTRATED",
            "deterministic_hard_gates": (
                "v8 best-of-N is the numeric deterministic hard-gate winner: "
                "higher on schema, distinct answers, misconception-label "
                "distinctness, and computation, but lower on key safety. "
                "Model-only v8 has a demonstrated computation advantage and "
                "lower key safety; its answer-diversity difference is uncertain."
            ),
            "human_ratings": (
                "NO DEMONSTRATED HUMAN-RATING WINNER. Opus leads the overall "
                "point estimate by 0.33/5, but the paired 95% interval crosses zero."
            ),
            "fair_scope": (
                "Numeric-only is the intended trained-domain comparison because "
                "eligibility is fixed from trusted gold before examining outputs."
            ),
            "claim_boundary": (
                "Written, categorical, operation-choice, image-answer, compound, "
                "and unparseable keys are declared exclusions. Results are not "
                "overall MCQ superiority outside this numeric scope."
            ),
            "human_evidence": (
                "The numeric blind subset remains exploratory: one reviewer, "
                "post-review gold-defined filtering, and an all-B response anomaly."
            ),
            "registered_win_rule": "NOT DEMONSTRATED because GDR/Good@3 remain unavailable.",
        },
    }
    return report, manifest


def _format_metric(metric: Mapping[str, object]) -> str:
    score = metric.get("score")
    if not isinstance(score, (int, float)):
        return str(metric.get("status", "UNAVAILABLE"))
    text = (
        f"{score:.1f}% "
        f"({metric.get('numerator')}/{metric.get('denominator')})"
    )
    ci = metric.get("ci95")
    if (
        isinstance(ci, list)
        and len(ci) == 2
        and all(isinstance(value, (int, float)) for value in ci)
    ):
        text += f" [{ci[0]:.1f}, {ci[1]:.1f}]"
    return text


def _format_comparison(comparison: Mapping[str, object]) -> tuple[str, str, str]:
    if comparison.get("status") != "MEASURED":
        return ("UNAVAILABLE", "UNAVAILABLE", "NOT DEMONSTRATED")
    delta = float(comparison["absolute_delta"])
    delta_ci = comparison["absolute_delta_ci95"]
    delta_text = f"{delta:+.1f} pp [{delta_ci[0]:+.1f}, {delta_ci[1]:+.1f}]"
    reduction = comparison.get("error_reduction")
    reduction_ci = comparison.get("error_reduction_ci95")
    if not isinstance(reduction, (int, float)):
        reduction_text = "undefined (Opus at ceiling)"
    elif isinstance(reduction_ci, list):
        reduction_text = (
            f"{100 * reduction:+.1f}% "
            f"[{100 * reduction_ci[0]:+.1f}%, {100 * reduction_ci[1]:+.1f}%]"
        )
    else:
        reduction_text = f"{100 * reduction:+.1f}%"
    target = (
        "DEMONSTRATED"
        if comparison.get("meets_40pct_error_reduction_ci")
        else "NOT DEMONSTRATED"
    )
    return delta_text, reduction_text, target


def _opus_difference(
    human: Mapping[str, object],
    dimension: str,
) -> tuple[float, list[float]]:
    paired = human["paired_differences"][dimension]
    difference = float(paired["mean_difference"])
    ci = list(paired["bootstrap_ci95"])
    if paired["contrast"] == "v8_best_of_n minus opus":
        return -difference, [-ci[1], -ci[0]]
    if paired["contrast"] == "opus minus v8_best_of_n":
        return difference, ci
    raise ValueError("human contrast does not compare Opus and v8 best-of-N")


def _format_signed_optional(value: object) -> str:
    return f"{float(value):+.2f}" if isinstance(value, (int, float)) else "unavailable"


def render_numeric_markdown(report: Mapping[str, object], manifest: Mapping[str, object]) -> str:
    """Render the complete intended-domain evidence without replacing all-scope results."""
    protocol = report["protocol"]
    scope = report["scope"]
    systems = report["systems"]
    comparisons = report["comparisons"]
    human = report["human_review"]
    blind_scope = manifest["blind_review_scope"]
    frozen_scope = manifest["frozen_scope"]
    lines = [
        "# v8 Numeric-only intended-domain view",
        "",
        "This is an additional trained-domain recalibration. It preserves the original "
        "all-scope evidence and is **not overall MCQ superiority** outside the declared "
        "numeric-answer scope.",
        "",
        "## Eligibility rule and fixed scope",
        "",
        f"- Frozen scope: **{protocol['numeric_items']} included / "
        f"{protocol['excluded_items']} excluded** from {protocol['frozen_items']}.",
        f"- Blind-review scope: **{blind_scope['included']} included / "
        f"{blind_scope['excluded']} excluded** from {blind_scope['total']}.",
        "- Classification uses only trusted gold `question`/`correct` fields and "
        "deterministic parser/type logic; it does not inspect model outputs, automatic "
        "scores, flags, ratings, or winners.",
        "- Included: exact integers/signed values, decimals, fractions/mixed numbers, "
        "percentages, numeric money/measurement displays, exact repeating decimals, "
        "bounded powers, exact roots, and standard-form values.",
        "- Excluded: named-person/categorical/verbal/truth answers, operation or sign "
        "choices, image-only answers, ordered/compound answers, Roman-numeral keys, "
        "algebraic keys, and malformed/unparseable keys.",
        f"- Rule: `{protocol['eligibility_rule_version']}`. Full per-ID decisions are in "
        "`data/eval_out/v8_numeric_scope_manifest.json`.",
        "",
        f"- Included frozen IDs ({frozen_scope['included']}): "
        + ", ".join(f"`{item_id}`" for item_id in frozen_scope["included_ids"]),
        f"- Excluded frozen IDs ({frozen_scope['excluded']}): "
        + ", ".join(f"`{item_id}`" for item_id in frozen_scope["excluded_ids"]),
        "",
        "### Exclusion reasons",
        "",
    ]
    for reason, count in scope["excluded_reason_counts"].items():
        lines.append(f"- `{reason}`: {count}")

    lines.extend(
        [
            "",
            "## Frozen numeric-subset deterministic metrics",
            "",
            "Scores are pass percentages. Brackets are 95% intervals: Wilson for "
            "question-level gates and question-cluster bootstrap for computation.",
            "",
            "| Metric | Opus | v8 model-only | v8 best-of-N |",
            "|---|---:|---:|---:|",
        ]
    )
    metric_labels = {
        "valid_exactly_3_json": "Valid exactly-3/schema",
        "none_equals_key": "No distractor equals key (numeric equivalence)",
        "distinct_answers": "Three distinct answers (numeric equivalence)",
        "distinct_misconceptions": "Three distinct misconception labels",
        "hardened_computation_validity": "Hardened computation validity",
    }
    for metric_name in MEASURED_METRICS:
        lines.append(
            f"| {metric_labels[metric_name]} | "
            + " | ".join(
                _format_metric(systems[name][metric_name]) for name in SYSTEM_ORDER
            )
            + " |"
        )

    for candidate, label in (
        ("v8_model_only", "v8 model-only vs Opus"),
        ("v8_best_of_n", "v8 verifier-guided best-of-N vs Opus"),
    ):
        comparison = comparisons[f"{candidate}_vs_opus"]
        lines.extend(
            [
                "",
                f"### Paired comparison: {label}",
                "",
                "| Metric | Absolute difference [95% CI] | Relative error-rate reduction [95% CI] | 40% interval target |",
                "|---|---:|---:|---:|",
            ]
        )
        for metric_name in MEASURED_METRICS:
            delta, reduction, target = _format_comparison(comparison[metric_name])
            lines.append(
                f"| {metric_labels[metric_name]} | {delta} | {reduction} | {target} |"
            )

    lines.extend(
        [
            "",
            "GDR, Good@3, accepted numeric binding, diagnostic-quality proxy, "
            "selective GDR, ECE, and Brier remain **UNAVAILABLE**; the numeric scope "
            "does not manufacture missing judgments.",
            "",
            "## Numeric-eligible blind human subset",
            "",
            f"- Included review items ({blind_scope['included']}): "
            + ", ".join(
                f"`{item_id}`" for item_id in blind_scope["included_review_item_ids"]
            ),
            f"- Excluded review items ({blind_scope['excluded']}): "
            + ", ".join(
                f"`{item_id}`" for item_id in blind_scope["excluded_review_item_ids"]
            ),
            "- This remains exploratory: one reviewer, a smaller fixed gold-defined "
            "subset, and no inter-rater reliability.",
            "",
            "### Excluded blind items",
            "",
            "| Review item | Frozen ID | Gold answer | Reason |",
            "|---|---:|---|---|",
        ]
    )
    for item in blind_scope["items"]:
        if not item["included"]:
            lines.append(
                f"| {item['review_item_id']} | {item['source_id']} | "
                f"`{item['gold_answer']}` | `{item['reason']}` |"
            )

    lines.extend(
        [
            "",
            "### Human 1–5 ratings",
            "",
            "| Dimension | Opus mean · median | v8 best-of-N mean · median | Opus − v8 [paired bootstrap 95% CI] |",
            "|---|---:|---:|---:|",
        ]
    )
    dimension_labels = {
        "diagnostic_usefulness": "Diagnostic usefulness",
        "student_plausibility": "Student plausibility",
        "teacher_actionability": "Teacher actionability",
        "overall": "Equal-weight overall",
    }
    for dimension in (*DIMENSIONS, "overall"):
        if dimension == "overall":
            opus_values = human["systems"]["opus"]["overall_rating"]
            v8_values = human["systems"]["v8_best_of_n"]["overall_rating"]
        else:
            opus_values = human["systems"]["opus"]["ratings"][dimension]
            v8_values = human["systems"]["v8_best_of_n"]["ratings"][dimension]
        difference, ci = _opus_difference(human, dimension)
        lines.append(
            f"| {dimension_labels[dimension]} | "
            f"{opus_values['mean']:.2f} · {opus_values['median']:.2f} | "
            f"{v8_values['mean']:.2f} · {v8_values['median']:.2f} | "
            f"{difference:+.2f} [{ci[0]:+.2f}, {ci[1]:+.2f}] |"
        )

    audit = human["response_consistency_audit"]
    lines.extend(
        [
            "",
            "### Recorded votes and response anomaly",
            "",
        ]
    )
    for source in ("v8_best_of_n", "opus"):
        preference = human["systems"][source]["preference"]
        lines.append(
            f"- **{source}:** {preference['wins']} wins, {preference['ties']} ties, "
            f"{preference['losses']} losses; {preference['win_rate_pct']:.1f}% win rate."
        )
    blind_counts = audit["blind_preference_counts"]
    direction_counts = audit["preference_rating_direction_counts"]
    lines.extend(
        [
            f"- Blind labels: A {blind_counts['A']}, Tie {blind_counts['Tie']}, "
            f"B {blind_counts['B']}. All responses same label: "
            f"`{audit['all_preferences_same_blind_label']}`; status `{audit['status']}`.",
            "- Selected candidate rating direction: "
            f"higher {direction_counts['selected_higher']}, "
            f"equal {direction_counts['selected_equal']}, "
            f"lower {direction_counts['selected_lower']}, "
            f"tie {direction_counts['tie']}.",
            "- The all-B anomaly is retained and the nominal vote is not treated as "
            "clean preference evidence.",
            "",
            "### Reviewer issue flags",
            "",
            "| Issue | Opus | v8 best-of-N |",
            "|---|---:|---:|",
        ]
    )
    for issue in (
        "any",
        "mathematically_inconsistent",
        "correct_answer_collision",
        "duplicate",
        "nonsense",
    ):
        opus_issue = human["systems"]["opus"]["issues"][issue]
        v8_issue = human["systems"]["v8_best_of_n"]["issues"][issue]
        lines.append(
            f"| {issue.replace('_', ' ')} | "
            f"{opus_issue['count']}/{human['design']['items']} "
            f"({opus_issue['rate_pct']:.1f}%) | "
            f"{v8_issue['count']}/{human['design']['items']} "
            f"({v8_issue['rate_pct']:.1f}%) |"
        )

    lines.extend(
        [
            "",
            "### Included item-level results",
            "",
            "Each item lists `diagnostic/plausibility/actionability`; issue flags are "
            "candidate-level reviewer observations.",
            "",
        ]
    )
    for item in human["item_results"]:
        v8 = item["systems"]["v8_best_of_n"]
        opus = item["systems"]["opus"]
        v8_flags = ", ".join(v8["issues"]) or "none"
        opus_flags = ", ".join(opus["issues"]) or "none"
        lines.extend(
            [
                f"- **{item['review_item_id']} / frozen {item['source_id']} / "
                f"gold `{item['gold_answer']}`:** recorded `{item['blind_preference']}` "
                f"→ `{item['selected_source']}`; "
                f"v8 {v8['diagnostic_usefulness']}/{v8['student_plausibility']}/"
                f"{v8['teacher_actionability']} ({v8_flags}); "
                f"Opus {opus['diagnostic_usefulness']}/{opus['student_plausibility']}/"
                f"{opus['teacher_actionability']} ({opus_flags}).",
            ]
        )

    interpretation = human["numeric_scope_interpretation"]
    comparison_to_all = human["comparison_to_all_scope"]
    lines.extend(
        [
            "",
            "## Product interpretation and verdict",
            "",
            "- **No holistic numeric-scope winner is demonstrated.** Best-of-N v8 "
            "wins the numeric deterministic hard-gate comparison, but key safety is "
            "lower and the required holistic GDR/quality evidence is unavailable.",
            "- **Model-only:** demonstrated +28.7 pp computation advantage; −5.0 pp "
            "key safety; +1.0 pp answer diversity with an interval spanning zero.",
            "- **Verifier-guided best-of-N:** +35.4 pp computation and +14.0 pp "
            "answer diversity, both demonstrated; −2.0 pp key safety with an interval "
            "spanning zero.",
            "- **Human ratings:** no demonstrated winner on the numeric subset. Opus "
            "has the +0.33/5 overall point estimate, but its interval crosses zero.",
            f"- Numeric-only is the fair trained-domain comparison; "
            f"{protocol['excluded_items']} written/categorical/operation/image/compound/"
            "unparseable-key items are explicitly outside scope.",
            f"- Numeric human overall Opus advantage: "
            f"{interpretation['overall_mean_advantage']:+.2f} points "
            f"[{interpretation['bootstrap_ci95'][0]:+.2f}, "
            f"{interpretation['bootstrap_ci95'][1]:+.2f}]. Previous all-scope "
            f"advantage: "
            f"{_format_signed_optional(comparison_to_all['all_scope_opus_overall_advantage'])}; "
            f"change: "
            f"{_format_signed_optional(comparison_to_all['change_in_opus_advantage'])}.",
            f"- Previous Opus human-quality advantage persists with its numeric-subset "
            f"interval above zero: "
            f"`{interpretation['previous_opus_human_quality_advantage_persists']}`.",
            "- Model-only and verifier-guided effects are reported separately above. "
            "Best-of-N gains are system-level and cannot be attributed wholly to the 8B model.",
            "- Do not describe these results as overall MCQ superiority. GDR/Good@3, "
            "student response frequency, model-only human ratings, and inter-rater "
            "agreement remain unavailable.",
            "",
            "Original all-scope files remain unchanged; this report is an additional "
            "intended-domain view.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_jsonl(path: str | Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON objects: {path}")
    return rows


def _load_json(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _file_evidence(path: str | Path) -> dict:
    source = Path(path)
    return {
        "path": str(source),
        "bytes": source.stat().st_size,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def _write_json(path: str | Path, value: Mapping[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_numeric_artifacts(
    *,
    gold_path: str | Path,
    prediction_paths: Mapping[str, str | Path],
    benchmark_path: str | Path,
    blind_result_path: str | Path,
    hidden_key_path: str | Path,
    json_out: str | Path,
    manifest_out: str | Path,
    markdown_out: str | Path,
    human_summary_path: str | Path | None = None,
    review_html_path: str | Path | None = None,
    protocol_path: str | Path | None = None,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    human_bootstrap_samples: int = HUMAN_BOOTSTRAP_SAMPLES,
    human_bootstrap_seed: int = HUMAN_BOOTSTRAP_SEED,
) -> dict:
    """Recompute and save all numeric-only artifacts without changing sources."""
    missing = [name for name in SYSTEM_ORDER if name not in prediction_paths]
    if missing:
        raise ValueError(f"missing prediction paths: {missing}")
    gold = _load_jsonl(gold_path)
    predictions = {
        name: _load_jsonl(prediction_paths[name]) for name in SYSTEM_ORDER
    }
    benchmark = _load_json(benchmark_path)
    blind_result = _load_json(blind_result_path)
    hidden_key = _load_json(hidden_key_path)
    report, manifest = build_numeric_report(
        gold,
        predictions,
        blind_result,
        hidden_key,
        benchmark_source=benchmark,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
        human_bootstrap_samples=human_bootstrap_samples,
        human_bootstrap_seed=human_bootstrap_seed,
    )

    source_paths: dict[str, str | Path] = {
        "frozen_gold": gold_path,
        "all_scope_benchmark": benchmark_path,
        "all_scope_blind_review": blind_result_path,
        "hidden_key": hidden_key_path,
        **{
            f"predictions_{name}": prediction_paths[name]
            for name in SYSTEM_ORDER
        },
    }
    optional_paths = {
        "all_scope_human_summary": human_summary_path,
        "review_html": review_html_path,
        "review_protocol": protocol_path,
    }
    source_paths.update(
        {
            name: path
            for name, path in optional_paths.items()
            if path is not None and Path(path).exists()
        }
    )
    evidence = {
        name: _file_evidence(path) for name, path in sorted(source_paths.items())
    }
    evidence["hidden_key"]["contents_included_in_outputs"] = False
    manifest["provenance"] = {
        "sources": evidence,
        "classification_uses_model_outputs": False,
        "source_evidence_modified": False,
    }
    _write_json(manifest_out, manifest)
    report["provenance"] = {
        "sources": evidence,
        "scope_manifest": _file_evidence(manifest_out),
        "original_all_scope_evidence_modified": False,
        "paid_api_calls": 0,
        "unity_run": False,
    }
    report["artifacts"] = {
        "benchmark_json": str(json_out),
        "scope_manifest": str(manifest_out),
        "markdown_table": str(markdown_out),
    }
    markdown = render_numeric_markdown(report, manifest)
    _write_json(json_out, report)
    markdown_output = Path(markdown_out)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(markdown, encoding="utf-8")
    return {
        "report": report,
        "manifest": manifest,
        "json_out": str(json_out),
        "manifest_out": str(manifest_out),
        "markdown_out": str(markdown_out),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        default="data/processed/eval_v8_frozen.jsonl",
    )
    parser.add_argument("--opus", default="predictions_opus_v8.jsonl")
    parser.add_argument(
        "--model-only",
        default="predictions_v8_model_only.jsonl",
    )
    parser.add_argument(
        "--best-of-n",
        default="predictions_v8_best_of_n.jsonl",
    )
    parser.add_argument(
        "--benchmark",
        default="data/eval_out/benchmark_v8_final.json",
    )
    parser.add_argument(
        "--blind-result",
        default="data/eval_out/blind_review_v8_opus_final.json",
    )
    parser.add_argument(
        "--hidden-key",
        default=(
            "data/eval_out/"
            "OWNER_ONLY_DO_NOT_OPEN_UNTIL_REVIEW_COMPLETE.json"
        ),
    )
    parser.add_argument(
        "--human-summary",
        default="HUMAN_REVIEW_V8_OPUS_RESULTS.md",
    )
    parser.add_argument(
        "--review-html",
        default="human_review/final_round/review.html",
    )
    parser.add_argument(
        "--review-protocol",
        default="human_review/final_round/PROTOCOL.md",
    )
    parser.add_argument(
        "--json-out",
        default="data/eval_out/benchmark_v8_numeric_final.json",
    )
    parser.add_argument(
        "--manifest-out",
        default="data/eval_out/v8_numeric_scope_manifest.json",
    )
    parser.add_argument(
        "--markdown-out",
        default="TABLE_V8_NUMERIC_RESULTS.md",
    )
    args = parser.parse_args()
    result = write_numeric_artifacts(
        gold_path=args.gold,
        prediction_paths={
            "opus": args.opus,
            "v8_model_only": args.model_only,
            "v8_best_of_n": args.best_of_n,
        },
        benchmark_path=args.benchmark,
        blind_result_path=args.blind_result,
        hidden_key_path=args.hidden_key,
        human_summary_path=args.human_summary,
        review_html_path=args.review_html,
        protocol_path=args.review_protocol,
        json_out=args.json_out,
        manifest_out=args.manifest_out,
        markdown_out=args.markdown_out,
    )
    print(
        f"wrote numeric benchmark -> {result['json_out']}\n"
        f"wrote numeric manifest -> {result['manifest_out']}\n"
        f"wrote numeric table -> {result['markdown_out']}"
    )


if __name__ == "__main__":
    main()
