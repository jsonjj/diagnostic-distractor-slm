"""Calibrate Opus nonnumeric binding judgments against existing human labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .api_cache import run_cached
from .calibrate_opus_v8 import (
    MAX_FALSE_POSITIVE_RATE,
    MIN_AGREEMENT,
)
from .confidence import fit_binary_verdict_calibration
from .config import TFY_JUDGE_MODEL
from .consistency import to_display_value
from .judge_v8 import MAX_OUTPUT_TOKENS, judge_one
from .run_frontier import validate_call_budget


def _load_labeled(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            verdict = str(row.get("human_verdict", "")).strip().upper()
            if verdict not in {"YES", "NO"}:
                continue
            if to_display_value(row.get("answer", "")) is not None:
                continue
            rows.append({**row, "truth": verdict == "YES"})
    return rows


def run_calibration(
    rows: list[dict],
    *,
    model: str,
    calibration_id: str,
    workers: int,
    cache_path: str | Path,
) -> dict:
    def key(row):
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(
            f"{model}:{payload}".encode("utf-8")
        ).hexdigest()

    def work(row):
        result = judge_one(
            row,
            {
                "misconception": row["misconception"],
                "computation": "",
                "answer": row["answer"],
            },
            model=model,
            calibration_id="numeric-not-used",
            nonnumeric_calibration_id="calibration-in-progress",
        )
        return {"binding_valid": result["binding_valid"]}

    cached, cache_stats = run_cached(
        rows,
        key_fn=key,
        worker=work,
        cache_path=cache_path,
        workers=workers,
    )
    verdicts = [row["binding_valid"] for row in cached]
    artifact = fit_binary_verdict_calibration(
        [row["truth"] for row in rows],
        verdicts,
        model=model,
        calibration_id=calibration_id,
        scope="nonnumeric misconception-answer binding",
    )
    artifact["ground_truth"] = "existing single-expert worksheet labels"
    artifact["limitations"] = (
        "Small single-labeler sample; plausibility remains a proxy."
    )
    artifact["acceptance_thresholds"] = {
        "minimum_agreement": MIN_AGREEMENT,
        "maximum_false_positive_rate": MAX_FALSE_POSITIVE_RATE,
    }
    artifact["accepted"] = (
        artifact["n"] >= 10
        and artifact["agreement"] >= MIN_AGREEMENT
        and artifact["false_positive_rate"] <= MAX_FALSE_POSITIVE_RATE
    )
    artifact["cache"] = cache_stats
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worksheet",
        default="judge_calibration_worksheet.jsonl",
    )
    parser.add_argument("--model", default=TFY_JUDGE_MODEL)
    parser.add_argument(
        "--calibration-id",
        default="opus-4-8-nonnumeric-binding-human-v1",
    )
    parser.add_argument(
        "--out",
        default="data/eval_out/opus_nonnumeric_binding_calibration_v8.json",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--cache",
        default="data/eval_out/opus_nonnumeric_calibration_responses_v8.jsonl",
    )
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--estimate-only", action="store_true")
    args = parser.parse_args()
    rows = _load_labeled(args.worksheet)
    estimate = {
        "model": args.model,
        "requests": len(rows),
        "max_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "max_output_tokens_total": len(rows) * MAX_OUTPUT_TOKENS,
        "dollar_cost": (
            "consult TrueFoundry account pricing; no organization-specific "
            "rate is assumed"
        ),
    }
    if args.estimate_only:
        print(json.dumps(estimate, indent=2))
        return
    validate_call_budget(requested=len(rows), max_calls=args.max_calls)
    artifact = run_calibration(
        rows,
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
            "Opus nonnumeric binding judge failed its calibration gate; "
            "full mixed-answer GDR must remain unavailable"
        )


if __name__ == "__main__":
    main()
