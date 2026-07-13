"""Primary v8 benchmark: GDR, Good@3, consistency, quality, confidence, and hard gates.

Alignment-to-one-historical-answer-set metrics remain available in ``src.eval`` as
diagnostics, but they are intentionally absent from this primary benchmark.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Optional, Sequence

from .confidence import validated_confidence
from .consistency import computation_consistent, to_display_value
from .text_utils import normalize_answer


BOOTSTRAP_SEED = 808
BOOTSTRAP_SAMPLES = 2000
GDR_TARGET = 90.0
ERROR_REDUCTION_TARGET = 0.40


def _percentile(values: Sequence[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def wilson_interval(
    numerator: int,
    denominator: int,
    *,
    z: float = 1.959963984540054,
) -> Optional[list[float]]:
    """95% Wilson score interval, returned in percentage points."""
    if denominator <= 0:
        return None
    p = numerator / denominator
    z2 = z * z
    denominator_term = 1 + z2 / denominator
    center = (p + z2 / (2 * denominator)) / denominator_term
    margin = (
        z
        * math.sqrt(
            p * (1 - p) / denominator + z2 / (4 * denominator * denominator)
        )
        / denominator_term
    )
    return [100 * max(0.0, center - margin), 100 * min(1.0, center + margin)]


def _cluster_rate_ci(
    question_values: Sequence[Sequence[bool]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Optional[list[float]]:
    """Question-cluster bootstrap CI for a pair-level rate."""
    if not question_values:
        return None
    rng = random.Random(seed)
    n = len(question_values)
    draws = []
    for _ in range(samples):
        selected = [question_values[rng.randrange(n)] for _ in range(n)]
        total = sum(len(values) for values in selected)
        if total:
            draws.append(
                100
                * sum(sum(bool(value) for value in values) for values in selected)
                / total
            )
    lower = _percentile(draws, 0.025)
    upper = _percentile(draws, 0.975)
    return [lower, upper] if lower is not None and upper is not None else None


def _measured_rate(
    numerator: int,
    denominator: int,
    *,
    method: str,
    ci95: Optional[list[float]] = None,
    note: Optional[str] = None,
) -> dict:
    value = {
        "score": 100 * numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "ci95": ci95 if ci95 is not None else wilson_interval(numerator, denominator),
        "status": "MEASURED" if denominator else "NOT YET RUN",
        "method": method,
        "direction": "higher_is_better",
    }
    if note:
        value["note"] = note
    return value


def _not_run(
    method: str,
    note: str = "",
    *,
    direction: str = "higher_is_better",
    status: str = "NOT YET RUN",
) -> dict:
    value = {
        "score": None,
        "numerator": None,
        "denominator": None,
        "ci95": None,
        "status": status,
        "method": method,
        "direction": direction,
    }
    if note:
        value["note"] = note
    return value


def _verdict_map(verdicts: Optional[Sequence[dict]]) -> dict:
    return {
        (str(row.get("id")), int(row.get("distractor_index", -1))): row
        for row in (verdicts or [])
        if row.get("id") not in (None, "")
        and str(row.get("distractor_index", "")).lstrip("-").isdigit()
    }


def _binding_value(verdict: dict):
    if isinstance(verdict.get("binding_valid"), bool):
        return verdict["binding_valid"]
    if isinstance(verdict.get("valid"), bool):  # legacy consistency sidecar
        return verdict["valid"]
    return None


def _binding_provenance_valid(verdict: dict) -> bool:
    method = str(verdict.get("binding_method", "")).strip()
    if method == "programmatic":
        return True
    return (
        method == "calibrated_opus_judge"
        and bool(str(verdict.get("binding_calibration_id", "")).strip())
        and verdict.get("binding_calibration_scope")
        == verdict.get("answer_type")
    )


def _answer_type(answer, verdict: Optional[dict]) -> str:
    if verdict and verdict.get("answer_type") in {"numeric", "nonnumeric"}:
        return verdict["answer_type"]
    return "numeric" if to_display_value(answer) is not None else "nonnumeric"


def _local_pair_gates(
    distractors: Sequence[dict],
    index: int,
    *,
    question: str,
    correct: str,
    verdict: dict,
) -> dict:
    distractor = distractors[index]
    answer = normalize_answer(distractor.get("answer", ""))
    misconception = str(distractor.get("misconception", "")).strip().casefold()
    answers = [
        normalize_answer(item.get("answer", ""))
        for item in distractors
        if isinstance(item, dict)
    ]
    misconceptions = [
        str(item.get("misconception", "")).strip().casefold()
        for item in distractors
        if isinstance(item, dict)
    ]
    answer_type = _answer_type(answer, verdict)
    computation_gate = (
        computation_consistent(
            distractor.get("computation", ""),
            answer,
            question,
            display_units=True,
        )
        is True
        if answer_type == "numeric"
        else True
    )
    return {
        "answer_safe": bool(answer) and answer != normalize_answer(correct),
        "answer_unique": bool(answer) and answers.count(answer) == 1,
        "misconception_present_distinct": (
            bool(misconception) and misconceptions.count(misconception) == 1
        ),
        "computation_valid_or_not_applicable": computation_gate,
        "binding_valid": _binding_value(verdict) is True,
        "plausibility_proxy_pass": verdict.get("plausibility_pass") is True,
    }


def _gdr_values(
    gold: Sequence[dict],
    predictions: Sequence[dict],
    verdicts: Optional[Sequence[dict]],
) -> tuple[Optional[list[list[bool]]], Optional[list[tuple[bool, Optional[float]]]]]:
    """Return clustered good flags and aligned confidence, or ``None`` if unjudged."""
    if verdicts is None:
        return None, None
    prediction_map = {
        str(row.get("id")): row
        for row in predictions
        if row.get("id") not in (None, "")
    }
    sidecar = _verdict_map(verdicts)
    clustered = []
    confidence_pairs: list[tuple[bool, Optional[float]]] = []
    for item in gold:
        item_id = str(item.get("id"))
        row = prediction_map.get(item_id, {})
        distractors = row.get("distractors", [])
        if not isinstance(distractors, list):
            distractors = []
        # A missing expected slot is a failed pair. Extra slots are also included so
        # emitting four options cannot improve GDR.
        slot_count = max(3, len(distractors))
        item_values = []
        for index in range(slot_count):
            if index >= len(distractors) or not isinstance(distractors[index], dict):
                item_values.append(False)
                confidence_pairs.append((False, None))
                continue
            verdict = sidecar.get((item_id, index))
            if verdict is None:
                return None, None
            if _binding_value(verdict) is None or not isinstance(
                verdict.get("plausibility_pass"),
                bool,
            ):
                return None, None
            if not _binding_provenance_valid(verdict):
                return None, None
            gates = _local_pair_gates(
                distractors,
                index,
                question=item.get("question", ""),
                correct=item.get("correct", ""),
                verdict=verdict,
            )
            good = all(gates.values())
            item_values.append(good)
            confidence = validated_confidence(
                distractors[index].get("confidence"),
                expected_target="misconception_answer_consistency",
            )
            probability = confidence["probability"] if confidence else None
            confidence_pairs.append((good, probability))
        clustered.append(item_values)
    return clustered, confidence_pairs


def _structural_values(
    gold: Sequence[dict],
    predictions: Sequence[dict],
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
            normalize_answer(distractor.get("answer", ""))
            for distractor in distractors
            if isinstance(distractor, dict)
        ]
        misconceptions = [
            str(distractor.get("misconception", "")).strip().casefold()
            for distractor in distractors
            if isinstance(distractor, dict)
        ]
        values["valid_exactly_3_json"].append(bool(valid))
        values["distinct_misconceptions"].append(
            bool(valid and len(set(misconceptions)) == 3)
        )
        values["none_equals_key"].append(
            bool(
                valid
                and all(
                    answer != normalize_answer(item.get("correct", ""))
                    for answer in answers
                )
            )
        )
        values["distinct_answers"].append(
            bool(valid and len(set(answers)) == 3)
        )
    return values


def primary_metrics(
    gold: Sequence[dict],
    predictions: Sequence[dict],
    *,
    verdicts: Optional[Sequence[dict]] = None,
    confidence_calibration: Optional[dict] = None,
) -> dict:
    """Compute the pre-registered small headline metric set."""
    metrics = {}
    clustered, confidence_pairs = _gdr_values(gold, predictions, verdicts)
    if clustered is None:
        metrics["good_distractor_rate"] = _not_run(
            "all-gates pair metric",
            "Requires complete binding and strict plausibility-proxy verdicts.",
        )
        metrics["good_at_3"] = _not_run(
            "all-three all-gates item metric",
            "Requires complete binding and strict plausibility-proxy verdicts.",
        )
    else:
        pair_good = sum(sum(bool(value) for value in values) for values in clustered)
        pair_total = sum(len(values) for values in clustered)
        all_three = [
            len(values) == 3 and all(values)
            for values in clustered
        ]
        metrics["good_distractor_rate"] = _measured_rate(
            pair_good,
            pair_total,
            method="all applicable local + binding + strict plausibility proxy gates",
            ci95=_cluster_rate_ci(clustered),
            note="Plausibility is an expert/Opus proxy, not observed student frequency.",
        )
        metrics["good_at_3"] = _measured_rate(
            sum(all_three),
            len(all_three),
            method="all three distractors pass GDR",
        )

    structure = _structural_values(gold, predictions)
    for name, values in structure.items():
        metrics[name] = _measured_rate(
            sum(values),
            len(values),
            method="deterministic local gate",
        )

    computation_clusters = _computation_clusters(gold, predictions)
    computation_good = sum(
        sum(bool(value) for value in values)
        for values in computation_clusters
    )
    computation_total = sum(len(values) for values in computation_clusters)
    any_computation = any(
        str(distractor.get("computation", "")).strip()
        for row in predictions
        for distractor in (
            row.get("distractors", [])
            if isinstance(row.get("distractors", []), list)
            else []
        )
        if isinstance(distractor, dict)
    )
    has_v8_provenance = any(row.get("generator_model") for row in predictions)
    if not any_computation and not has_v8_provenance:
        metrics["hardened_computation_validity"] = _not_run(
            "legacy prediction schema had no computation field",
            status="NOT APPLICABLE",
        )
    else:
        metrics["hardened_computation_validity"] = _measured_rate(
            computation_good,
            computation_total,
            method="exact arithmetic + question grounding; not a binding verdict",
            ci95=_cluster_rate_ci(computation_clusters),
        )

    verdict_map = _verdict_map(verdicts)
    binding_buckets = {"numeric": [], "nonnumeric": []}
    plausibility = []
    for (item_id, index), verdict in verdict_map.items():
        binding = _binding_value(verdict)
        if isinstance(binding, bool) and _binding_provenance_valid(verdict):
            answer_type = verdict.get("answer_type", "nonnumeric")
            if answer_type not in binding_buckets:
                answer_type = "nonnumeric"
            binding_buckets[answer_type].append(binding)
        if isinstance(verdict.get("plausibility_pass"), bool):
            plausibility.append(verdict["plausibility_pass"])
    for answer_type in ("numeric", "nonnumeric"):
        values = binding_buckets[answer_type]
        key = f"{answer_type}_binding_consistency"
        metrics[key] = (
            _measured_rate(
                sum(values),
                len(values),
                method="verdict_sidecar",
            )
            if values
            else _not_run("verdict_sidecar")
        )
    metrics["diagnostic_quality_proxy"] = (
        _measured_rate(
            sum(plausibility),
            len(plausibility),
            method="strict expert/Opus plausibility proxy",
            note="Not observed student selection frequency.",
        )
        if plausibility
        else _not_run(
            "strict expert/Opus plausibility proxy",
            "No option-pick counts are available.",
        )
    )

    if confidence_calibration and confidence_calibration.get("accepted") is True:
        metrics["confidence_ece"] = {
            "score": 100 * float(confidence_calibration["ece"]),
            "status": "MEASURED",
            "method": "out-of-fold binding calibration artifact",
            "direction": "lower_is_better",
            "n": int(confidence_calibration["n"]),
            "calibration_id": confidence_calibration.get("calibration_id"),
        }
        metrics["confidence_brier"] = {
            "score": float(confidence_calibration["brier"]),
            "status": "MEASURED",
            "method": "out-of-fold binding calibration artifact",
            "direction": "lower_is_better",
            "n": int(confidence_calibration["n"]),
            "calibration_id": confidence_calibration.get("calibration_id"),
        }
    else:
        metrics["confidence_ece"] = _not_run(
            "out-of-fold binding calibration artifact",
            direction="lower_is_better",
        )
        metrics["confidence_brier"] = _not_run(
            "out-of-fold binding calibration artifact",
            direction="lower_is_better",
        )

    if clustered is not None and confidence_pairs is not None:
        if confidence_pairs and all(
            probability is not None for _, probability in confidence_pairs
        ):
            labels = [good for good, _ in confidence_pairs]
            probabilities = [
                float(probability)
                for _, probability in confidence_pairs
                if probability is not None
            ]
            ranked = sorted(
                zip(probabilities, labels),
                key=lambda pair: pair[0],
                reverse=True,
            )
            selected_n = math.ceil(0.80 * len(ranked))
            selected = ranked[:selected_n]
            metrics["selective_gdr_at_80pct_coverage"] = {
                "score": (
                    100 * sum(bool(label) for _, label in selected) / selected_n
                ),
                "coverage": 100 * selected_n / len(ranked),
                "numerator": sum(bool(label) for _, label in selected),
                "denominator": selected_n,
                "ci95": wilson_interval(
                    sum(bool(label) for _, label in selected),
                    selected_n,
                ),
                "status": "MEASURED",
                "method": "top-confidence pairs at >=80% coverage",
                "direction": "higher_is_better",
            }
        else:
            metrics["selective_gdr_at_80pct_coverage"] = _not_run(
                "post-hoc calibration"
            )
    else:
        metrics["selective_gdr_at_80pct_coverage"] = _not_run(
            "post-hoc calibration"
        )
    return metrics


def relative_error_reduction(
    candidate_score: float,
    baseline_score: float,
    *,
    ceiling: float = 100.0,
) -> Optional[float]:
    """Relative reduction in distance to a bounded metric's ceiling."""
    if not 0 <= candidate_score <= ceiling or not 0 <= baseline_score <= ceiling:
        raise ValueError("scores must lie between zero and the metric ceiling")
    baseline_error = ceiling - baseline_score
    if baseline_error == 0:
        return None
    return (baseline_error - (ceiling - candidate_score)) / baseline_error


def paired_bootstrap_compare(
    candidate_values: Sequence[float],
    baseline_values: Sequence[float],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Paired question bootstrap for score delta and relative error reduction."""
    if len(candidate_values) != len(baseline_values) or not candidate_values:
        raise ValueError("paired values must have equal non-zero length")
    n = len(candidate_values)
    candidate_score = 100 * sum(candidate_values) / n
    baseline_score = 100 * sum(baseline_values) / n
    rng = random.Random(seed)
    deltas = []
    reductions = []
    for _ in range(samples):
        indices = [rng.randrange(n) for _ in range(n)]
        candidate = 100 * sum(candidate_values[index] for index in indices) / n
        baseline = 100 * sum(baseline_values[index] for index in indices) / n
        deltas.append(candidate - baseline)
        reduction = relative_error_reduction(candidate, baseline)
        if reduction is not None:
            reductions.append(reduction)
    result = {
        "candidate_score": candidate_score,
        "baseline_score": baseline_score,
        "absolute_delta": candidate_score - baseline_score,
        "absolute_delta_ci95": [
            _percentile(deltas, 0.025),
            _percentile(deltas, 0.975),
        ],
        "error_reduction": relative_error_reduction(
            candidate_score,
            baseline_score,
        ),
        "error_reduction_ci95": (
            [
                _percentile(reductions, 0.025),
                _percentile(reductions, 0.975),
            ]
            if reductions
            else None
        ),
    }
    return result


def paired_bootstrap_ratio_compare(
    candidate_clusters: Sequence[Sequence[bool]],
    baseline_clusters: Sequence[Sequence[bool]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Paired question bootstrap for rates with variable pair denominators."""
    if (
        len(candidate_clusters) != len(baseline_clusters)
        or not candidate_clusters
    ):
        raise ValueError("paired clusters must have equal non-zero length")

    def score(clusters, indices):
        denominator = sum(len(clusters[index]) for index in indices)
        if denominator == 0:
            return None
        numerator = sum(
            sum(bool(value) for value in clusters[index])
            for index in indices
        )
        return 100 * numerator / denominator

    indices = list(range(len(candidate_clusters)))
    candidate_score = score(candidate_clusters, indices)
    baseline_score = score(baseline_clusters, indices)
    if candidate_score is None or baseline_score is None:
        raise ValueError("both systems need at least one scored pair")
    rng = random.Random(seed)
    deltas = []
    reductions = []
    for _ in range(samples):
        draw = [rng.randrange(len(indices)) for _ in indices]
        candidate = score(candidate_clusters, draw)
        baseline = score(baseline_clusters, draw)
        if candidate is None or baseline is None:
            continue
        deltas.append(candidate - baseline)
        reduction = relative_error_reduction(candidate, baseline)
        if reduction is not None:
            reductions.append(reduction)
    return {
        "candidate_score": candidate_score,
        "baseline_score": baseline_score,
        "absolute_delta": candidate_score - baseline_score,
        "absolute_delta_ci95": [
            _percentile(deltas, 0.025),
            _percentile(deltas, 0.975),
        ],
        "error_reduction": relative_error_reduction(
            candidate_score,
            baseline_score,
        ),
        "error_reduction_ci95": (
            [
                _percentile(reductions, 0.025),
                _percentile(reductions, 0.975),
            ]
            if reductions
            else None
        ),
    }


def paired_bootstrap_selective_compare(
    candidate_clusters: Sequence[Sequence[tuple[bool, float]]],
    baseline_clusters: Sequence[Sequence[tuple[bool, float]]],
    *,
    coverage: float = 0.80,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Paired bootstrap for top-confidence accuracy at fixed coverage."""
    if (
        len(candidate_clusters) != len(baseline_clusters)
        or not candidate_clusters
        or not 0 < coverage <= 1
    ):
        raise ValueError("invalid paired selective clusters or coverage")

    def score(clusters, indices):
        pairs = [
            pair
            for index in indices
            for pair in clusters[index]
        ]
        if not pairs:
            return None
        selected_n = math.ceil(coverage * len(pairs))
        selected = sorted(
            pairs,
            key=lambda pair: pair[1],
            reverse=True,
        )[:selected_n]
        return 100 * sum(bool(label) for label, _ in selected) / selected_n

    indices = list(range(len(candidate_clusters)))
    candidate_score = score(candidate_clusters, indices)
    baseline_score = score(baseline_clusters, indices)
    if candidate_score is None or baseline_score is None:
        raise ValueError("both systems need calibrated confidence pairs")
    rng = random.Random(seed)
    deltas = []
    reductions = []
    for _ in range(samples):
        draw = [rng.randrange(len(indices)) for _ in indices]
        candidate = score(candidate_clusters, draw)
        baseline = score(baseline_clusters, draw)
        if candidate is None or baseline is None:
            continue
        deltas.append(candidate - baseline)
        reduction = relative_error_reduction(candidate, baseline)
        if reduction is not None:
            reductions.append(reduction)
    return {
        "candidate_score": candidate_score,
        "baseline_score": baseline_score,
        "coverage": 100 * coverage,
        "absolute_delta": candidate_score - baseline_score,
        "absolute_delta_ci95": [
            _percentile(deltas, 0.025),
            _percentile(deltas, 0.975),
        ],
        "error_reduction": relative_error_reduction(
            candidate_score,
            baseline_score,
        ),
        "error_reduction_ci95": (
            [
                _percentile(reductions, 0.025),
                _percentile(reductions, 0.975),
            ]
            if reductions
            else None
        ),
    }


def _computation_clusters(
    gold: Sequence[dict],
    predictions: Sequence[dict],
) -> list[list[bool]]:
    prediction_map = {
        str(row.get("id")): row
        for row in predictions
        if row.get("id") not in (None, "")
    }
    clusters = []
    for item in gold:
        distractors = prediction_map.get(str(item.get("id")), {}).get(
            "distractors",
            [],
        )
        if not isinstance(distractors, list):
            distractors = []
        clusters.append(
            [
                (
                    index < len(distractors)
                    and isinstance(distractors[index], dict)
                    and computation_consistent(
                        distractors[index].get("computation", ""),
                        distractors[index].get("answer", ""),
                        item.get("question", ""),
                        display_units=True,
                    )
                    is True
                )
                for index in range(max(3, len(distractors)))
            ]
        )
    return clusters


def _verdict_clusters(
    gold: Sequence[dict],
    verdicts: Sequence[dict],
    *,
    metric: str,
) -> list[list[bool]]:
    by_id = {str(item.get("id")): [] for item in gold}
    for verdict in verdicts:
        item_id = str(verdict.get("id"))
        if item_id not in by_id:
            continue
        if metric == "numeric_binding":
            value = _binding_value(verdict)
            if (
                verdict.get("answer_type") != "numeric"
                or not isinstance(value, bool)
                or not _binding_provenance_valid(verdict)
            ):
                continue
        elif metric == "diagnostic_quality":
            value = verdict.get("plausibility_pass")
            if not isinstance(value, bool):
                continue
        else:
            raise ValueError(f"unknown verdict metric: {metric}")
        by_id[item_id].append(
            (int(verdict.get("distractor_index", -1)), bool(value))
        )
    return [
        [
            value
            for _, value in sorted(by_id[str(item.get("id"))])
        ]
        for item in gold
    ]


def compare_systems(
    gold: Sequence[dict],
    candidate_predictions: Sequence[dict],
    baseline_predictions: Sequence[dict],
    *,
    candidate_verdicts: Optional[Sequence[dict]] = None,
    baseline_verdicts: Optional[Sequence[dict]] = None,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Compare selected primary metrics with paired question bootstrap."""
    vectors: dict[str, tuple[Optional[list[list[bool]]], Optional[list[list[bool]]]]] = {}
    candidate_gdr, candidate_confidence = _gdr_values(
        gold,
        candidate_predictions,
        candidate_verdicts,
    )
    baseline_gdr, baseline_confidence = _gdr_values(
        gold,
        baseline_predictions,
        baseline_verdicts,
    )
    vectors["good_distractor_rate"] = (candidate_gdr, baseline_gdr)
    vectors["good_at_3"] = (
        (
            [[len(values) == 3 and all(values)] for values in candidate_gdr]
            if candidate_gdr is not None
            else None
        ),
        (
            [[len(values) == 3 and all(values)] for values in baseline_gdr]
            if baseline_gdr is not None
            else None
        ),
    )

    candidate_structure = _structural_values(gold, candidate_predictions)
    baseline_structure = _structural_values(gold, baseline_predictions)
    for name in (
        "valid_exactly_3_json",
        "distinct_misconceptions",
        "none_equals_key",
        "distinct_answers",
    ):
        vectors[name] = (
            [[value] for value in candidate_structure[name]],
            [[value] for value in baseline_structure[name]],
        )
    def supports_computation_metric(predictions):
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

    if supports_computation_metric(
        candidate_predictions
    ) and supports_computation_metric(baseline_predictions):
        vectors["hardened_computation_validity"] = (
            _computation_clusters(gold, candidate_predictions),
            _computation_clusters(gold, baseline_predictions),
        )
    else:
        vectors["hardened_computation_validity"] = (None, None)
    if candidate_verdicts is not None and baseline_verdicts is not None:
        vectors["numeric_binding_consistency"] = (
            _verdict_clusters(
                gold,
                candidate_verdicts,
                metric="numeric_binding",
            ),
            _verdict_clusters(
                gold,
                baseline_verdicts,
                metric="numeric_binding",
            ),
        )
        vectors["diagnostic_quality_proxy"] = (
            _verdict_clusters(
                gold,
                candidate_verdicts,
                metric="diagnostic_quality",
            ),
            _verdict_clusters(
                gold,
                baseline_verdicts,
                metric="diagnostic_quality",
            ),
        )
    else:
        vectors["numeric_binding_consistency"] = (None, None)
        vectors["diagnostic_quality_proxy"] = (None, None)

    comparisons = {}
    for name, (candidate, baseline) in vectors.items():
        if (
            candidate is None
            or baseline is None
            or not any(candidate)
            or not any(baseline)
        ):
            comparisons[name] = {"status": "NOT YET RUN"}
            continue
        result = paired_bootstrap_ratio_compare(
            candidate,
            baseline,
            samples=samples,
            seed=seed,
        )
        reduction = result["error_reduction"]
        interval = result["error_reduction_ci95"]
        result["status"] = "MEASURED"
        result["meets_40pct_error_reduction_point"] = (
            reduction is not None and reduction >= ERROR_REDUCTION_TARGET
        )
        result["meets_40pct_error_reduction_ci"] = (
            interval is not None
            and interval[0] is not None
            and interval[0] >= ERROR_REDUCTION_TARGET
        )
        if name == "good_distractor_rate":
            result["meets_absolute_90pct_gdr"] = (
                result["candidate_score"] >= GDR_TARGET
            )
        comparisons[name] = result

    def confidence_clusters(gdr, pairs):
        if gdr is None or pairs is None:
            return None
        clusters = []
        offset = 0
        for values in gdr:
            current = pairs[offset : offset + len(values)]
            offset += len(values)
            if any(probability is None for _, probability in current):
                return None
            clusters.append(
                [
                    (good, float(probability))
                    for good, probability in current
                    if probability is not None
                ]
            )
        return clusters

    candidate_selective = confidence_clusters(
        candidate_gdr,
        candidate_confidence,
    )
    baseline_selective = confidence_clusters(
        baseline_gdr,
        baseline_confidence,
    )
    if candidate_selective is not None and baseline_selective is not None:
        selective = paired_bootstrap_selective_compare(
            candidate_selective,
            baseline_selective,
            samples=samples,
            seed=seed,
        )
        reduction = selective["error_reduction"]
        interval = selective["error_reduction_ci95"]
        selective["status"] = "MEASURED"
        selective["meets_40pct_error_reduction_point"] = (
            reduction is not None and reduction >= ERROR_REDUCTION_TARGET
        )
        selective["meets_40pct_error_reduction_ci"] = (
            interval is not None
            and interval[0] is not None
            and interval[0] >= ERROR_REDUCTION_TARGET
        )
        comparisons["selective_gdr_at_80pct_coverage"] = selective
    else:
        comparisons["selective_gdr_at_80pct_coverage"] = {
            "status": "NOT YET RUN"
        }
    return comparisons


def _load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _named_paths(values: Sequence[str]) -> dict[str, str]:
    out = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected NAME=PATH, got {value!r}")
        name, path = value.split("=", 1)
        out[name] = path
    return out


def _format_metric_cell(name: str, metric: dict) -> str:
    if metric.get("score") is None:
        return metric.get("status", "NOT YET RUN")
    score = metric["score"]
    if name == "confidence_brier":
        return f"{score:.4f}"
    text = f"{score:.1f}%"
    if (
        metric.get("numerator") is not None
        and metric.get("denominator") is not None
    ):
        text += f" ({metric['numerator']}/{metric['denominator']})"
    ci = metric.get("ci95")
    if (
        isinstance(ci, list)
        and len(ci) == 2
        and ci[0] is not None
        and ci[1] is not None
    ):
        text += f" [{ci[0]:.1f}, {ci[1]:.1f}]"
    return text


def render_markdown_table(report: dict) -> str:
    """Render only the pre-registered headline and hard-gate metrics."""
    systems = report.get("systems", {})
    names = list(systems)
    rows = [
        ("good_distractor_rate", "Good Distractor Rate"),
        ("good_at_3", "Good@3"),
        (
            "numeric_binding_consistency",
            "Numeric misconception→answer consistency",
        ),
        ("diagnostic_quality_proxy", "Diagnostic-quality proxy pass"),
        (
            "selective_gdr_at_80pct_coverage",
            "Selective GDR at ≥80% coverage",
        ),
        ("confidence_ece", "Numeric binding confidence ECE"),
        ("confidence_brier", "Numeric binding confidence Brier"),
        ("valid_exactly_3_json", "Valid exactly-3 output"),
        ("none_equals_key", "No answer equals key"),
        ("distinct_answers", "Three distinct answers"),
        ("distinct_misconceptions", "Three distinct misconceptions"),
        (
            "hardened_computation_validity",
            "Hardened computation validity",
        ),
    ]
    lines = [
        "# v8 Primary Benchmark Results",
        "",
        "Scores are percentages unless noted. Brackets are 95% intervals.",
        "",
        "| Metric | " + " | ".join(names) + " |",
        "|---|" + "|".join("---:" for _ in names) + "|",
    ]
    for key, label in rows:
        cells = [
            _format_metric_cell(
                key,
                systems.get(name, {}).get(
                    key,
                    {"score": None, "status": "NOT YET RUN"},
                ),
            )
            for name in names
        ]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "Diagnostic quality/plausibility is an expert/Opus proxy, not observed "
            "student option frequency.",
            "",
            "Exact/Partial/Proportional@3 are diagnostics and are intentionally "
            "excluded from this headline table.",
        ]
    )
    for comparison_name, comparisons in report.get("comparisons", {}).items():
        lines.extend(
            [
                "",
                f"## Paired comparison: {comparison_name}",
                "",
                "| Metric | Absolute delta [95% CI] | Relative error reduction [95% CI] | 40% target |",
                "|---|---:|---:|---:|",
            ]
        )
        for key, label in rows:
            comparison = comparisons.get(key, {})
            if comparison.get("status") != "MEASURED":
                continue
            delta_ci = comparison.get("absolute_delta_ci95")
            reduction_ci = comparison.get("error_reduction_ci95")
            delta = (
                f"{comparison['absolute_delta']:.1f} "
                f"[{delta_ci[0]:.1f}, {delta_ci[1]:.1f}]"
            )
            reduction = comparison.get("error_reduction")
            reduction_text = (
                "undefined (baseline at ceiling)"
                if reduction is None
                else (
                    f"{100 * reduction:.1f}% "
                    f"[{100 * reduction_ci[0]:.1f}%, "
                    f"{100 * reduction_ci[1]:.1f}%]"
                    if reduction_ci is not None
                    else f"{100 * reduction:.1f}%"
                )
            )
            target_pass = comparison.get(
                "meets_40pct_error_reduction_ci"
            )
            if key == "good_distractor_rate":
                target_pass = target_pass and comparison.get(
                    "meets_absolute_90pct_gdr"
                )
            target = "PASS" if target_pass else "NOT DEMONSTRATED"
            lines.append(
                f"| {label} | {delta} | {reduction_text} | {target} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold",
        default="data/processed/eval_v8_frozen.jsonl",
    )
    parser.add_argument(
        "--system",
        action="append",
        default=[],
        help="NAME=predictions.jsonl (repeatable)",
    )
    parser.add_argument(
        "--verdicts",
        action="append",
        default=[],
        help="NAME=quality_verdicts.jsonl (repeatable)",
    )
    parser.add_argument(
        "--candidate",
        default="v8",
        help="system name compared against --baseline",
    )
    parser.add_argument(
        "--baseline",
        default="opus",
        help="frontier system name used for paired comparisons",
    )
    parser.add_argument(
        "--confidence-calibration",
        default="data/eval_out/opus_binding_calibration_v8.json",
        help="accepted out-of-fold numeric binding calibration artifact",
    )
    parser.add_argument("--out", default="data/eval_out/benchmark_v8.json")
    parser.add_argument(
        "--markdown-out",
        default="TABLE_V8_RESULTS.md",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    systems = _named_paths(args.system)
    verdict_paths = _named_paths(args.verdicts)
    gold = _load_jsonl(args.gold)
    calibration_path = Path(args.confidence_calibration)
    confidence_calibration = (
        json.loads(calibration_path.read_text(encoding="utf-8"))
        if calibration_path.exists()
        else None
    )
    report = {
        "protocol": {
            "gdr_target": GDR_TARGET,
            "opus_relative_error_reduction_target": ERROR_REDUCTION_TARGET,
            "plausibility": "expert/Opus proxy; no observed student pick rates",
        },
        "systems": {},
        "comparisons": {},
    }
    loaded_predictions = {}
    loaded_verdicts = {}
    for name, path in systems.items():
        sidecar = (
            _load_jsonl(verdict_paths[name])
            if name in verdict_paths
            else None
        )
        predictions = _load_jsonl(path)
        loaded_predictions[name] = predictions
        loaded_verdicts[name] = sidecar
        report["systems"][name] = primary_metrics(
            gold,
            predictions,
            verdicts=sidecar,
            confidence_calibration=confidence_calibration,
        )
    if args.candidate in systems and args.baseline in systems:
        report["comparisons"][
            f"{args.candidate}_vs_{args.baseline}"
        ] = compare_systems(
            gold,
            loaded_predictions[args.candidate],
            loaded_predictions[args.baseline],
            candidate_verdicts=loaded_verdicts[args.candidate],
            baseline_verdicts=loaded_verdicts[args.baseline],
        )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    Path(args.markdown_out).write_text(
        render_markdown_table(report),
        encoding="utf-8",
    )
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
