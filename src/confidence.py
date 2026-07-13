"""Externally calibrated confidence schema and calibration metrics.

The generator is not asked to invent confidence numbers. Confidence is attached after
generation by a versioned verifier/judge calibrator. Until such an artifact exists, every
prediction carries an explicit ``not_calibrated`` payload with ``probability=None``.
"""
from __future__ import annotations

import copy
import math
from typing import Iterable, Optional, Sequence


PAIR_TARGET = "misconception_answer_consistency"
QUESTION_TARGET = "all_three_distractors_valid"
_LEVEL_THRESHOLDS = (("high", 0.90), ("medium", 0.70))


def _as_probability(value) -> float:
    if isinstance(value, bool):
        raise ValueError("probability must be a finite number in [0, 1]")
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("probability must be a finite number in [0, 1]") from exc
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be a finite number in [0, 1]")
    return probability


def _level(probability: float) -> str:
    for name, threshold in _LEVEL_THRESHOLDS:
        if probability >= threshold:
            return name
    return "low"


def confidence_payload(
    probability: Optional[float],
    *,
    target: str,
    source: str,
    calibration_id: Optional[str] = None,
) -> dict:
    """Build one confidence payload without accepting model self-reports.

    A numeric probability is legal only when it identifies the post-hoc calibration
    artifact that produced it. This makes accidental arbitrary decimals fail loudly.
    """
    target = str(target or "").strip()
    source = str(source or "").strip()
    calibration_id = str(calibration_id or "").strip() or None
    if not target:
        raise ValueError("target is required")
    if probability is None:
        return {
            "target": target,
            "probability": None,
            "level": "not_calibrated",
            "calibrated": False,
            "source": source or "unavailable",
            "calibration_id": None,
        }
    if not calibration_id:
        raise ValueError("calibration_id is required for a numeric probability")
    if not source or source.lower() in {
        "model",
        "model_self_report",
        "self_report",
        "llm_self_report",
    }:
        raise ValueError("numeric confidence must come from an external calibrator")
    probability = _as_probability(probability)
    return {
        "target": target,
        "probability": probability,
        "level": _level(probability),
        "calibrated": True,
        "source": source,
        "calibration_id": calibration_id,
    }


def validated_confidence(value, *, expected_target: Optional[str] = None) -> Optional[dict]:
    """Return a normalized valid confidence object, otherwise ``None``."""
    if not isinstance(value, dict) or value.get("calibrated") is not True:
        return None
    target = str(value.get("target") or expected_target or "").strip()
    if expected_target and target != expected_target:
        return None
    try:
        return confidence_payload(
            value.get("probability"),
            target=target,
            source=value.get("source", ""),
            calibration_id=value.get("calibration_id"),
        )
    except ValueError:
        return None


def ensure_confidence_schema(prediction: dict) -> dict:
    """Copy a prediction and attach explicit pair/question confidence payloads.

    Existing confidence survives only if it names a valid external calibration
    artifact. Pair probabilities are deliberately not combined into an item
    probability; an all-three probability needs its own item-level calibrator.
    """
    out = copy.deepcopy(prediction)
    distractors = out.get("distractors")
    if not isinstance(distractors, list):
        distractors = []
        out["distractors"] = distractors
    for distractor in distractors:
        if not isinstance(distractor, dict):
            continue
        valid = validated_confidence(
            distractor.get("confidence"),
            expected_target=PAIR_TARGET,
        )
        distractor["confidence"] = valid or confidence_payload(
            None,
            target=PAIR_TARGET,
            source="unavailable",
        )
    question = validated_confidence(
        out.get("question_confidence"),
        expected_target=QUESTION_TARGET,
    )
    out["question_confidence"] = question or confidence_payload(
        None,
        target=QUESTION_TARGET,
        source="unavailable",
    )
    return out


def confidence_metrics(
    labels: Sequence[bool],
    probabilities: Sequence[float],
    *,
    bins: int = 10,
    thresholds: Iterable[float] = (0.5, 0.7, 0.8, 0.9, 0.95),
) -> dict:
    """Compute Brier, equal-width ECE, and selective accuracy/coverage."""
    if len(labels) != len(probabilities) or not labels:
        raise ValueError("labels and probabilities must have equal non-zero length")
    if bins <= 0:
        raise ValueError("bins must be positive")
    probs = [_as_probability(value) for value in probabilities]
    truth = [bool(value) for value in labels]
    n = len(truth)
    brier = sum((probability - float(label)) ** 2 for label, probability in zip(truth, probs)) / n

    buckets: list[list[tuple[bool, float]]] = [[] for _ in range(bins)]
    for label, probability in zip(truth, probs):
        index = min(bins - 1, int(probability * bins))
        buckets[index].append((label, probability))
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        accuracy = sum(float(label) for label, _ in bucket) / len(bucket)
        mean_confidence = sum(probability for _, probability in bucket) / len(bucket)
        ece += (len(bucket) / n) * abs(accuracy - mean_confidence)

    selective = []
    for raw_threshold in thresholds:
        threshold = _as_probability(raw_threshold)
        selected = [
            label
            for label, probability in zip(truth, probs)
            if probability >= threshold
        ]
        selective.append(
            {
                "threshold": threshold,
                "coverage": len(selected) / n,
                "accuracy": (
                    sum(float(label) for label in selected) / len(selected)
                    if selected
                    else None
                ),
                "n": len(selected),
            }
        )
    return {
        "n": n,
        "brier": brier,
        "ece": ece,
        "bins": bins,
        "selective": selective,
    }


def fit_binary_verdict_calibration(
    labels: Sequence[bool],
    verdicts: Sequence[bool],
    *,
    model: str,
    calibration_id: str,
    scope: str = "numeric misconception-answer binding",
) -> dict:
    """Calibrate a boolean judge verdict with Beta(1,1) group smoothing.

    The output estimates P(valid | judge YES) and P(valid | judge NO) on a
    labeled calibration set. ``scope`` records which answer population was labeled.
    """
    if len(labels) != len(verdicts) or not labels:
        raise ValueError("labels and verdicts must have equal non-zero length")
    if not str(model).strip() or not str(calibration_id).strip():
        raise ValueError("model and calibration_id are required")
    truth = [bool(value) for value in labels]
    judged = [bool(value) for value in verdicts]
    yes_labels = [
        label for label, verdict in zip(truth, judged) if verdict
    ]
    no_labels = [
        label for label, verdict in zip(truth, judged) if not verdict
    ]
    p_yes = (sum(yes_labels) + 1) / (len(yes_labels) + 2)
    p_no = (sum(no_labels) + 1) / (len(no_labels) + 2)
    # Evaluate calibration out-of-fold with leave-one-out group estimates so the
    # reported Brier/ECE are not measured on the exact label used to fit its own
    # probability.
    probabilities = []
    for label, verdict in zip(truth, judged):
        group = yes_labels if verdict else no_labels
        probabilities.append(
            (sum(group) - int(label) + 1) / (len(group) - 1 + 2)
        )
    metrics = confidence_metrics(truth, probabilities)
    tp = sum(
        label and verdict
        for label, verdict in zip(truth, judged)
    )
    tn = sum(
        (not label) and (not verdict)
        for label, verdict in zip(truth, judged)
    )
    fp = sum(
        (not label) and verdict
        for label, verdict in zip(truth, judged)
    )
    fn = sum(
        label and (not verdict)
        for label, verdict in zip(truth, judged)
    )
    return {
        "schema_version": "binary-verdict-calibration-v1",
        "calibration_id": str(calibration_id),
        "model": str(model),
        "target": PAIR_TARGET,
        "scope": str(scope),
        "n": len(truth),
        "agreement": (tp + tn) / len(truth),
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
        "false_negative_rate": fn / (fn + tp) if fn + tp else 0.0,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "p_valid_given_yes": p_yes,
        "p_valid_given_no": p_no,
        "brier": metrics["brier"],
        "ece": metrics["ece"],
        "smoothing": "Beta(1,1) within verdict groups",
        "calibration_evaluation": "leave-one-out",
    }


def apply_binding_calibration(
    predictions: Sequence[dict],
    verdicts: Sequence[dict],
    artifact: dict,
    *,
    nonnumeric_artifact: Optional[dict] = None,
) -> list[dict]:
    """Attach scoped binding confidence from verified calibration artifacts."""
    calibration_id = str(artifact.get("calibration_id", "")).strip()
    if not calibration_id:
        raise ValueError("calibration artifact has no calibration_id")
    p_yes = _as_probability(artifact.get("p_valid_given_yes"))
    p_no = _as_probability(artifact.get("p_valid_given_no"))
    nonnumeric_id = (
        str(nonnumeric_artifact.get("calibration_id", "")).strip()
        if nonnumeric_artifact
        else ""
    )
    if nonnumeric_artifact and not nonnumeric_id:
        raise ValueError("nonnumeric calibration artifact has no calibration_id")
    nonnumeric_p_yes = (
        _as_probability(nonnumeric_artifact.get("p_valid_given_yes"))
        if nonnumeric_artifact
        else None
    )
    nonnumeric_p_no = (
        _as_probability(nonnumeric_artifact.get("p_valid_given_no"))
        if nonnumeric_artifact
        else None
    )
    sidecar = {
        (str(row.get("id")), int(row.get("distractor_index", -1))): row
        for row in verdicts
        if row.get("id") not in (None, "")
    }
    enriched = []
    for prediction in predictions:
        row = copy.deepcopy(prediction)
        item_id = str(row.get("id"))
        distractors = row.get("distractors", [])
        if not isinstance(distractors, list):
            distractors = []
            row["distractors"] = distractors
        for index, distractor in enumerate(distractors):
            if not isinstance(distractor, dict):
                continue
            verdict = sidecar.get((item_id, index))
            if verdict and isinstance(verdict.get("binding_valid"), bool):
                answer_type = verdict.get("answer_type")
                if answer_type == "numeric":
                    probability = p_yes if verdict["binding_valid"] else p_no
                    active_id = calibration_id
                elif answer_type == "nonnumeric" and nonnumeric_artifact:
                    probability = (
                        nonnumeric_p_yes
                        if verdict["binding_valid"]
                        else nonnumeric_p_no
                    )
                    active_id = nonnumeric_id
                else:
                    continue
                distractor["confidence"] = confidence_payload(
                    probability,
                    target=PAIR_TARGET,
                    source=f"calibrated_opus_{answer_type}_binding_verdict",
                    calibration_id=active_id,
                )
        enriched.append(ensure_confidence_schema(row))
    return enriched
