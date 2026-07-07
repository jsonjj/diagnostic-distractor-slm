"""Run the frontier model (Claude Sonnet 5 via TrueFoundry) as a distractor generator on the eval hold-out.

Produces predictions_frontier.jsonl in the SAME schema as the base/tuned model predictions, so it can be
scored with the identical eval harness for an apples-to-apples "small model vs frontier" comparison:

    python -m src.eval predictions_frontier.jsonl

COSTS API on the TrueFoundry gateway. Calls run concurrently (default 8 workers) with a per-request
timeout so a single slow call can't stall the whole run. Run explicitly:

    python -m src.run_frontier                 # all hold-out questions
    python -m src.run_frontier --n 30          # cheaper smoke run
    python -m src.run_frontier --workers 12    # more concurrency

Requires `openai` installed and TFY_API_KEY in env/.env.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from .config import DATA_PROCESSED, TFY_MODEL
from .prompts import SYSTEM_PROMPT, build_user, parse_distractors
from .tfy_client import chat


def load_gold():
    with open(DATA_PROCESSED / "eval_heldout.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _predict(r):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user(r["question"], r["correct"], r["topic"])},
    ]
    try:
        text = chat(messages, max_tokens=512)
        err = None
    except Exception as e:  # noqa: BLE001
        text, err = "", str(e)[:140]
    return {"id": r["id"], "distractors": parse_distractors(text)}, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="limit to first N questions (0 = all)")
    ap.add_argument("--out", default="predictions_frontier.jsonl")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    gold = load_gold()
    if args.n:
        gold = gold[: args.n]

    print(f"Generating distractors with {TFY_MODEL} for {len(gold)} questions "
          f"({args.workers} workers, TrueFoundry API)...", flush=True)

    rows, errors, done = [None] * len(gold), 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (res, err) in enumerate(ex.map(_predict, gold)):
            rows[i] = res
            done += 1
            if err:
                errors += 1
            if done % 20 == 0:
                print(f"  {done}/{len(gold)} (errors so far: {errors})", flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(f"done: wrote {len(rows)} rows ({errors} errors) -> {args.out}", flush=True)
    print(f"score with:  python -m src.eval {args.out}", flush=True)


if __name__ == "__main__":
    main()
