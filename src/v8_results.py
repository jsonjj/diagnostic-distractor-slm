"""Assemble the role-separated final v8 benchmark report."""
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

from .benchmark_v8 import (
    ERROR_REDUCTION_TARGET,
    GDR_TARGET,
    compare_systems,
    primary_metrics,
    render_markdown_table,
)
from .config import OPUS_MODEL_ID


SYSTEM_ORDER = ("opus", "v8_model_only", "v8_best_of_n")
CANDIDATES = ("v8_model_only", "v8_best_of_n")
SELECTED_PRIMARY_METRICS = (
    "good_distractor_rate",
    "good_at_3",
    "numeric_binding_consistency",
    "diagnostic_quality_proxy",
    "selective_gdr_at_80pct_coverage",
)
FINAL_CALL_CAP = 980
FINAL_OUTPUT_TOKEN_CAP = 172_480
FRONTIER_MAX_OUTPUT_TOKENS = 512
UNAVAILABLE_NOTES = {
    "good_distractor_rate": (
        "No accepted independent judge exists for misconception mapping, "
        "specificity, plausibility, and diagnostic usefulness."
    ),
    "good_at_3": "GDR is unavailable, so all-three GDR cannot be computed.",
    "numeric_binding_consistency": (
        "No complete deterministic misconception-procedure mapping or accepted "
        "independent binding judge is available for these free-text outputs."
    ),
    "nonnumeric_binding_consistency": (
        "No accepted scope-specific nonnumeric calibration exists."
    ),
    "diagnostic_quality_proxy": (
        "No accepted independent quality judge exists; student pick-frequency "
        "data is absent."
    ),
    "selective_gdr_at_80pct_coverage": (
        "GDR and calibrated per-pair confidence are unavailable."
    ),
    "confidence_ece": (
        "The registered Opus numeric calibration was rejected; no accepted "
        "independent/programmatic confidence calibration exists."
    ),
    "confidence_brier": (
        "The registered Opus numeric calibration was rejected; no accepted "
        "independent/programmatic confidence calibration exists."
    ),
}


def _coverage(gold: Sequence[dict], predictions: Sequence[dict]) -> dict:
    gold_ids = [str(row.get("id", "")) for row in gold]
    prediction_ids = [str(row.get("id", "")) for row in predictions]
    return {
        "rows": len(predictions),
        "unique_ids": len(set(prediction_ids)),
        "expected_rows": len(gold),
        "ids_match_frozen_in_order": prediction_ids == gold_ids,
        "complete": (
            prediction_ids == gold_ids
            and len(set(prediction_ids)) == len(prediction_ids)
        ),
    }


def _mark_final_unavailable(metrics: dict) -> None:
    for name, note in UNAVAILABLE_NOTES.items():
        metric = metrics.get(name)
        if metric is not None and metric.get("score") is None:
            metric["status"] = "UNAVAILABLE"
            metric["note"] = note


def _mark_comparison_unavailable(comparisons: dict) -> None:
    for name, note in UNAVAILABLE_NOTES.items():
        comparison = comparisons.get(name)
        if comparison is not None and comparison.get("status") != "MEASURED":
            comparison["status"] = "UNAVAILABLE"
            comparison["note"] = note


def build_final_report(
    gold: Sequence[dict],
    predictions: Mapping[str, Sequence[dict]],
    *,
    metadata: dict | None = None,
) -> dict:
    """Build final metrics and both v8-vs-Opus paired comparisons."""
    missing = [name for name in SYSTEM_ORDER if name not in predictions]
    if missing:
        raise ValueError(f"missing systems: {missing}")
    coverage = {
        name: _coverage(gold, predictions[name]) for name in SYSTEM_ORDER
    }
    incomplete = [
        name for name, evidence in coverage.items() if not evidence["complete"]
    ]
    if incomplete:
        raise ValueError(f"{incomplete[0]} coverage does not match frozen eval")

    report = {
        "protocol": {
            "frozen_items": len(gold),
            "gdr_target": GDR_TARGET,
            "opus_relative_error_reduction_target": ERROR_REDUCTION_TARGET,
            "bootstrap_samples": 2000,
            "bootstrap_seed": 808,
            "roles": {
                "opus": "frontier generator only; never judge",
                "v8_model_only": "deterministic greedy model output",
                "v8_best_of_n": (
                    "verifier-guided four-candidate system output; not model-only"
                ),
            },
            "plausibility": (
                "UNAVAILABLE; any future score is an independent expert proxy, "
                "not observed student pick frequency"
            ),
        },
        "coverage": coverage,
        "systems": {},
        "comparisons": {},
        "decision_rules": {},
    }
    for name in SYSTEM_ORDER:
        metrics = primary_metrics(gold, predictions[name])
        _mark_final_unavailable(metrics)
        report["systems"][name] = metrics
    for candidate in CANDIDATES:
        key = f"{candidate}_vs_opus"
        comparisons = compare_systems(
            gold,
            predictions[candidate],
            predictions["opus"],
        )
        _mark_comparison_unavailable(comparisons)
        report["comparisons"][key] = comparisons
        gdr = report["systems"][candidate]["good_distractor_rate"]
        gdr_comparison = comparisons["good_distractor_rate"]
        selected_primary = {}
        for metric_name in SELECTED_PRIMARY_METRICS:
            comparison = comparisons[metric_name]
            measured = comparison.get("status") == "MEASURED"
            selected_primary[metric_name] = {
                "status": comparison.get("status"),
                "meets_40pct_error_reduction": (
                    comparison.get("meets_40pct_error_reduction_ci")
                    if measured
                    else None
                ),
                "meets_40pct_error_reduction_point": (
                    comparison.get("meets_40pct_error_reduction_point")
                    if measured
                    else None
                ),
            }
        report["decision_rules"][candidate] = {
            "meets_absolute_90pct_gdr": (
                gdr["score"] >= GDR_TARGET
                if gdr.get("score") is not None
                else None
            ),
            "meets_40pct_gdr_error_reduction_point": (
                gdr_comparison.get("meets_40pct_error_reduction_point")
                if gdr_comparison.get("status") == "MEASURED"
                else None
            ),
            "meets_40pct_gdr_error_reduction_ci": (
                gdr_comparison.get("meets_40pct_error_reduction_ci")
                if gdr_comparison.get("status") == "MEASURED"
                else None
            ),
            "selected_primary_metrics": selected_primary,
            "overall": "NOT DEMONSTRATED",
            "reason": (
                "GDR and Good@3 are unavailable under the registered protocol "
                "because no accepted independent judge exists."
            ),
        }
    if metadata:
        report.update(deepcopy(metadata))
    return report


def render_final_markdown(report: dict) -> str:
    """Render the final evidence summary plus the registered benchmark tables."""
    table = render_markdown_table(report)
    table = table.replace(
        "# v8 Primary Benchmark Results",
        "## Registered score table",
        1,
    )
    table = table.replace(
        "Diagnostic quality/plausibility is an expert/Opus proxy, not observed "
        "student option frequency.",
        "Diagnostic quality/plausibility would be an independent calibrated "
        "expert-model proxy, not observed student option frequency.",
    )
    usage = report.get("evaluation_usage", {})
    training = report.get("training", {})
    adapter = report.get("adapter", {})
    verification = report.get("verification", {})
    lines = [
        "# v8 Final Evaluation Results",
        "",
        "## Statistical verdict",
        "",
        "- **v8 8B model-only:** NOT DEMONSTRATED for the registered win rule. "
        "GDR/Good@3 are unavailable, so neither ≥90% GDR nor ≥40% GDR "
        "relative error-rate reduction versus Opus can be established.",
        "- **v8 verifier-guided best-of-N:** NOT DEMONSTRATED for the registered "
        "win rule for the same reason. Verifier-guided best-of-N is a system "
        "result, not model-only performance.",
        "- Deterministic hard-gate paired deltas and relative error-rate "
        "reductions are reported below, but they do not substitute for GDR.",
        "- For every selected primary quality metric, the ≥40% relative "
        "error-rate reduction target is NOT DEMONSTRATED because the required "
        "binding/quality judgments are unavailable.",
        "",
        "Student plausibility and diagnostic quality are **UNAVAILABLE**. No "
        "student option-pick frequencies exist, and the registered Opus judge "
        "calibration was rejected.",
    ]
    if training:
        lines.extend(
            [
                "",
                "## Training and artifact handoff",
                "",
                f"- Selected checkpoint: `{training.get('best_checkpoint')}` "
                f"(validation loss {training.get('best_eval_loss')}).",
                f"- Base: `{training.get('base_model')}` at immutable revision "
                f"`{training.get('base_revision')}`.",
                f"- Frozen/train hashes matched the receipt and manifest: "
                f"`{training.get('hashes_verified')}`.",
                f"- Adapter ZIP: `{adapter.get('local_zip')}` "
                f"(SHA-256 `{adapter.get('sha256')}`); archive integrity and "
                "required adapter entries verified.",
                f"- Hugging Face reference: {adapter.get('hf_url')} "
                f"({adapter.get('remote_status')}). The verified local ZIP is "
                "the recovery artifact.",
            ]
        )
    if (
        usage.get("call_cap_usage_pct") is not None
        and usage.get("output_token_cap_usage_pct") is not None
    ):
        lines.extend(
            [
                "",
                "## Paid evaluation budget",
                "",
                f"- Exact frontier model: `{usage.get('model')}`.",
                f"- Completed frontier task calls: "
                f"{usage.get('completed_calls')}/{usage.get('call_cap')} "
                f"({usage.get('call_cap_usage_pct'):.1f}% of cap).",
                f"- Configured output-token ceiling: "
                f"{usage.get('configured_output_token_ceiling'):,}/"
                f"{usage.get('output_token_cap'):,} "
                f"({usage.get('output_token_cap_usage_pct'):.1f}% of cap).",
                "- Actual provider output-token usage and dollar cost are "
                "UNAVAILABLE because the gateway client/cache does not retain "
                "usage and organization-specific TrueFoundry pricing is not "
                "stored in the repository.",
            ]
        )
    if verification:
        lines.extend(
            [
                "",
                "## Verification",
                "",
                f"- {verification.get('full_python_tests', {}).get('passed')} "
                "full Python tests passed.",
                f"- {verification.get('v8_python_tests', {}).get('passed')} "
                "v8-specific tests passed.",
                f"- Frozen data manifest verified: "
                f"`{verification.get('manifest_verified')}`.",
                "- Unity was not run.",
            ]
        )
    lines.extend(["", table.rstrip(), ""])
    evidence = report.get("evidence", {})
    if evidence:
        lines.extend(
            [
                "## Evidence",
                "",
                *[
                    f"- {label.replace('_', ' ')}: `{path}`"
                    for label, path in evidence.items()
                ],
                "",
            ]
        )
    return "\n".join(lines)


def _load_json(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: str | Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON objects: {path}")
    return rows


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
        "--artifact-validation",
        default="data/eval_out/v8_artifact_validation.json",
    )
    parser.add_argument(
        "--receipt",
        default="v8_training_receipt.json",
    )
    parser.add_argument(
        "--calibration",
        default="data/eval_out/opus_binding_calibration_v8.json",
    )
    parser.add_argument(
        "--cache",
        default="data/eval_out/opus_frontier_v8.cache.jsonl",
    )
    parser.add_argument(
        "--out",
        default="data/eval_out/benchmark_v8_final.json",
    )
    parser.add_argument("--markdown-out", default="TABLE_V8_RESULTS.md")
    parser.add_argument("--pre-gateway-failed-tasks", type=int, default=0)
    parser.add_argument(
        "--hf-remote-status",
        default="not independently verified",
    )
    parser.add_argument("--full-tests-passed", type=int)
    parser.add_argument("--v8-tests-passed", type=int)
    parser.add_argument("--manifest-verified", action="store_true")
    args = parser.parse_args()

    gold = _load_jsonl(args.gold)
    predictions = {
        "opus": _load_jsonl(args.opus),
        "v8_model_only": _load_jsonl(args.model_only),
        "v8_best_of_n": _load_jsonl(args.best_of_n),
    }
    if not all(
        row.get("generator_model") == OPUS_MODEL_ID
        for row in predictions["opus"]
    ):
        raise SystemExit("Opus predictions contain the wrong generator model")
    cache_rows = _load_jsonl(args.cache)
    validation = _load_json(args.artifact_validation)
    if validation.get("ok") is not True:
        raise SystemExit("artifact validation is not passing")
    receipt = _load_json(args.receipt)
    calibration = _load_json(args.calibration)
    adapter = validation.get("adapter", {})
    completed_calls = len(cache_rows)
    token_ceiling = len(gold) * FRONTIER_MAX_OUTPUT_TOKENS
    metadata = {
        "training": {
            "base_model": receipt.get("base_model"),
            "base_revision": receipt.get("base_revision"),
            "best_checkpoint": receipt.get("best_checkpoint"),
            "best_eval_loss": receipt.get("best_eval_loss"),
            "epochs_planned": receipt.get("epochs_planned"),
            "seed": receipt.get("seed"),
            "hashes_verified": all(
                validation.get("receipt", {}).get("checks", {}).values()
            ),
        },
        "adapter": {
            "local_zip": "qwen3-8b-distractor-lora-v8.zip",
            "bytes": adapter.get("local_zip_evidence", {}).get("bytes"),
            "sha256": adapter.get("local_zip_evidence", {}).get("sha256"),
            "zip_integrity": adapter.get("zip_integrity"),
            "required_entries_present": adapter.get(
                "required_entries_present"
            ),
            "hf_url": adapter.get("hf_url"),
            "remote_verified": adapter.get("remote_verified"),
            "remote_status": args.hf_remote_status,
        },
        "judge_status": {
            "opus_used_as_judge": False,
            "registered_calibration_accepted": calibration.get("accepted"),
            "agreement": calibration.get("agreement"),
            "false_positive_rate": calibration.get("false_positive_rate"),
            "independent_judge_used": False,
            "reason": (
                "No accepted independent calibration exists. Judging all three "
                "systems would also require 1,260 pair calls before calibration, "
                "which exceeds the final 980-call cap."
            ),
        },
        "evaluation_usage": {
            "estimate_completed_before_paid_run": True,
            "model": OPUS_MODEL_ID,
            "completed_calls": completed_calls,
            "resumed_calls": 0,
            "call_cap": FINAL_CALL_CAP,
            "call_cap_usage_pct": 100 * completed_calls / FINAL_CALL_CAP,
            "configured_output_tokens_per_call": (
                FRONTIER_MAX_OUTPUT_TOKENS
            ),
            "configured_output_token_ceiling": token_ceiling,
            "output_token_cap": FINAL_OUTPUT_TOKEN_CAP,
            "output_token_cap_usage_pct": (
                100 * token_ceiling / FINAL_OUTPUT_TOKEN_CAP
            ),
            "actual_output_tokens": None,
            "dollar_cost": None,
            "cost_note": (
                "Consult TrueFoundry account pricing; no repository rate is "
                "assumed."
            ),
            "pre_gateway_failed_tasks": args.pre_gateway_failed_tasks,
            "pre_gateway_failure_paid_completions": 0,
        },
        "artifacts": {
            "opus_predictions": {
                "path": args.opus,
                "bytes": Path(args.opus).stat().st_size,
                "sha256": _sha256(args.opus),
            },
            "artifact_validation_ok": True,
            "best_of_n_complete": validation.get("tracks", {})
            .get("best_of_n", {})
            .get("complete"),
        },
        "verification": {
            "full_python_tests": {"passed": args.full_tests_passed},
            "v8_python_tests": {"passed": args.v8_tests_passed},
            "manifest_verified": args.manifest_verified,
            "unity_run": False,
        },
        "evidence": {
            "artifact_validation": args.artifact_validation,
            "frontier_estimate": (
                "data/eval_out/opus_frontier_estimate_v8.json"
            ),
            "frontier_generation_log": (
                "data/eval_out/opus_frontier_generation_v8.log"
            ),
            "frontier_cache": args.cache,
            "rejected_opus_calibration": args.calibration,
            "python_test_log": (
                "data/eval_out/python_tests_v8_final.log"
            ),
        },
    }
    report = build_final_report(gold, predictions, metadata=metadata)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    Path(args.markdown_out).write_text(
        render_final_markdown(report),
        encoding="utf-8",
    )
    print(f"wrote final JSON -> {output}")
    print(f"wrote final table -> {args.markdown_out}")


if __name__ == "__main__":
    main()
