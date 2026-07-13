"""Calibrate the v8 Opus binding judge on deterministic numeric ground truth."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .api_cache import run_cached
from .calibrate_judge import build_deterministic_pairs
from .confidence import fit_binary_verdict_calibration
from .config import TFY_JUDGE_MODEL
from .judge_v8 import MAX_OUTPUT_TOKENS, judge_one
from .run_frontier import validate_call_budget


MIN_AGREEMENT = 0.80
MAX_FALSE_POSITIVE_RATE = 0.10


def run_calibration(
    *,
    n: int,
    model: str,
    calibration_id: str,
    workers: int,
    cache_path: str | Path,
) -> dict:
    pairs = build_deterministic_pairs(n)

    def key(pair):
        payload = json.dumps(pair, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(
            f"{model}:{payload}".encode("utf-8")
        ).hexdigest()

    def work(pair):
        result = judge_one(
            {
                "question": pair["question"],
                "correct": pair["correct"],
                "topic": "Number",
            },
            {
                "misconception": pair["misconception"],
                "computation": "",
                "answer": pair["answer"],
            },
            model=model,
            calibration_id="calibration-in-progress",
        )
        return {"binding_valid": result["binding_valid"]}

    cached, cache_stats = run_cached(
        pairs,
        key_fn=key,
        worker=work,
        cache_path=cache_path,
        workers=workers,
    )
    verdicts = [row["binding_valid"] for row in cached]
    artifact = fit_binary_verdict_calibration(
        [pair["truth"] for pair in pairs],
        verdicts,
        model=model,
        calibration_id=calibration_id,
    )
    artifact["ground_truth"] = "buggy-procedure engine, balanced hard negatives"
    artifact["acceptance_thresholds"] = {
        "minimum_agreement": MIN_AGREEMENT,
        "maximum_false_positive_rate": MAX_FALSE_POSITIVE_RATE,
    }
    artifact["accepted"] = (
        artifact["agreement"] >= MIN_AGREEMENT
        and artifact["false_positive_rate"] <= MAX_FALSE_POSITIVE_RATE
    )
    artifact["cache"] = cache_stats
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=80)
    parser.add_argument("--model", default=TFY_JUDGE_MODEL)
    parser.add_argument(
        "--calibration-id",
        default="opus-4-8-numeric-binding-engine80-v1",
    )
    parser.add_argument(
        "--out",
        default="data/eval_out/opus_binding_calibration_v8.json",
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--cache",
        default="data/eval_out/opus_numeric_calibration_responses_v8.jsonl",
    )
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--estimate-only", action="store_true")
    args = parser.parse_args()
    estimate = {
        "model": args.model,
        "requests": args.n,
        "max_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "max_output_tokens_total": args.n * MAX_OUTPUT_TOKENS,
        "dollar_cost": (
            "consult TrueFoundry account pricing; no organization-specific "
            "rate is assumed"
        ),
    }
    if args.estimate_only:
        print(json.dumps(estimate, indent=2))
        return
    validate_call_budget(requested=args.n, max_calls=args.max_calls)
    artifact = run_calibration(
        n=args.n,
        model=args.model,
        calibration_id=args.calibration_id,
        workers=args.workers,
        cache_path=args.cache,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifact, indent=2, sort_keys=True))
    if not artifact["accepted"]:
        raise SystemExit(
            "Opus binding judge failed the pre-registered calibration gate"
        )


if __name__ == "__main__":
    main()
