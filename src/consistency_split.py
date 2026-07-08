"""Split judged consistency by numeric vs non-numeric answer subset.

Calibration (data/eval_out/judge_calibration.md) showed the one-shot judge is ~90% reliable on
NUMERIC consistency but only ~50% (35% false-positive) on NON-NUMERIC/conceptual answers. Since
models differ wildly in how often they emit non-numeric distractors (v4/v5 ~2-5%, Sonnet ~34%),
a single full-eval consistency number compares them on unequal, partly-unreliable ground.

This script reports judged consistency SPLIT into:
  - numeric subset   (answer parses to a number) -> judge is reliable here
  - non-numeric subset (conceptual / textual)    -> judge unreliable, report with caveat
  - full eval                                     -> the headline number, for continuity

Usage (API cost, ~1 judge call per distractor):
  python -m src.consistency_split predictions_tuned_v5.jsonl
"""
from __future__ import annotations

import argparse
import json

from .config import DATA_PROCESSED
from .consistency import to_value


def _load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run(pred_path: str):
    from .eval import judge_consistency, _run_judge_concurrent

    gold = {str(r["id"]): r for r in _load(DATA_PROCESSED / "eval_heldout.jsonl")}
    preds = _load(pred_path)

    # Build tasks + remember each pair's subset, then judge concurrently with progress.
    tasks, keys = [], []
    for r in preds:
        g = gold.get(str(r["id"]))
        if not g:
            continue
        for d in r.get("distractors", []):
            ans = d.get("answer", "")
            keys.append("numeric" if to_value(ans) is not None else "nonnumeric")
            tasks.append((g["question"], d.get("misconception", ""), ans, g["correct"]))
    res = _run_judge_concurrent(tasks, judge_consistency, f"split judge [{pred_path}]")

    buckets = {"numeric": [0, 0], "nonnumeric": [0, 0]}  # [ok, n]
    for key, ok in zip(keys, res):
        buckets[key][1] += 1
        buckets[key][0] += 1 if ok else 0

    n_num, n_non = buckets["numeric"][1], buckets["nonnumeric"][1]
    ok_num, ok_non = buckets["numeric"][0], buckets["nonnumeric"][0]
    n_all, ok_all = n_num + n_non, ok_num + ok_non

    def pct(a, b):
        return 100 * a / b if b else 0.0

    print(f"=== judged consistency split: {pred_path} ===")
    print(f"NUMERIC subset      (judge ~90% reliable): {pct(ok_num, n_num):.1f}%  ({ok_num}/{n_num})")
    print(f"NON-NUMERIC subset  (judge ~50%, caveat) : {pct(ok_non, n_non):.1f}%  ({ok_non}/{n_non})")
    print(f"FULL eval (headline, mixed reliability)  : {pct(ok_all, n_all):.1f}%  ({ok_all}/{n_all})")
    print(f"composition: {pct(n_num, n_all):.0f}% numeric / {pct(n_non, n_all):.0f}% non-numeric")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("predictions")
    args = ap.parse_args()
    run(args.predictions)


if __name__ == "__main__":
    main()
