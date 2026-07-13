"""Run a configured TrueFoundry frontier model as a distractor generator.

Produces predictions_frontier.jsonl in the SAME schema as the base/tuned model predictions, so it can be
scored with the identical eval harness for an apples-to-apples "small model vs frontier" comparison:

    python -m src.eval predictions_frontier.jsonl

COSTS API on the TrueFoundry gateway. Calls run concurrently (default 8 workers) with a per-request
timeout so a single slow call can't stall the whole run. Run explicitly:

    python -m src.run_frontier --estimate-only
    python -m src.run_frontier --max-calls 140
    python -m src.run_frontier --n 30 --max-calls 30
    python -m src.run_frontier --workers 12    # more concurrency

Requires `openai` installed and TFY_API_KEY in env/.env.
"""
from __future__ import annotations

import argparse
import hashlib
import json

from .api_cache import run_cached
from .confidence import ensure_confidence_schema
from .config import DATA_PROCESSED, TFY_FRONTIER_MODEL
from .prompts import SYSTEM_PROMPT, build_user, parse_distractors
from .tfy_client import chat
from .v8_teacher import (
    DETERMINISTIC_TEACHER_ROUTE,
    supported_procedure_labels,
)


def load_gold(path=None):
    source = path or (DATA_PROCESSED / "eval_heldout.jsonl")
    with open(source, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def validate_call_budget(*, requested: int, max_calls: int) -> None:
    if max_calls <= 0:
        raise ValueError(
            "paid frontier generation requires an explicit --max-calls cap"
        )
    if requested > max_calls:
        raise ValueError(
            f"requested {requested} calls exceeds --max-calls {max_calls}"
        )


def _predict(
    r,
    *,
    model=TFY_FRONTIER_MODEL,
    chat_fn=chat,
    deterministic_teacher=False,
):
    user_content = build_user(
        r["question"],
        r["correct"],
        r["topic"],
    )
    if deterministic_teacher:
        labels = supported_procedure_labels(r["topic"])
        label_lines = "\n".join(f"- {label}" for label in labels)
        user_content += (
            "\n\nDeterministic teacher route: Use only these exact registered "
            "labels, each at most once. Do not paraphrase them. If fewer than "
            "three genuinely apply, still return the required candidate JSON; "
            "the deterministic filter will reject unsupported rows.\n"
            f"{label_lines or '- No registered label for this topic.'}"
        )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        text = chat_fn(messages, model=model, max_tokens=512)
        err = None
    except Exception as e:  # noqa: BLE001
        text, err = "", str(e)[:140]
    row = ensure_confidence_schema(
        {
            "id": r["id"],
            "generator_model": model,
            "distractors": parse_distractors(text),
            **(
                {"generation_route": DETERMINISTIC_TEACHER_ROUTE}
                if deterministic_teacher
                else {}
            ),
        }
    )
    return row, err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="limit to first N questions (0 = all)")
    ap.add_argument(
        "--input",
        default=str(DATA_PROCESSED / "eval_heldout.jsonl"),
        help="question JSONL containing id/question/correct/topic",
    )
    ap.add_argument("--out", default="predictions_frontier.jsonl")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--max-calls",
        type=int,
        default=0,
        help="required hard cap on paid requests",
    )
    ap.add_argument(
        "--estimate-only",
        action="store_true",
        help="print bounded request/token volume without calling the gateway",
    )
    ap.add_argument(
        "--model",
        default=TFY_FRONTIER_MODEL,
        help="gateway model ID (defaults to TFY_FRONTIER_MODEL, then legacy TFY_MODEL)",
    )
    ap.add_argument(
        "--deterministic-teacher",
        action="store_true",
        help=(
            "constrain labels to the audited procedure catalog and mark output "
            "for deterministic teacher filtering"
        ),
    )
    ap.add_argument(
        "--cache",
        help="resumable paid-call cache (defaults to OUT.cache.jsonl)",
    )
    args = ap.parse_args()

    gold = load_gold(args.input)
    if args.n:
        gold = gold[: args.n]
    estimate = {
        "model": args.model,
        "requests": len(gold),
        "max_output_tokens_per_request": 512,
        "max_output_tokens_total": 512 * len(gold),
        "generation_route": (
            DETERMINISTIC_TEACHER_ROUTE
            if args.deterministic_teacher
            else "frontier_generation"
        ),
        "dollar_cost": "consult TrueFoundry account pricing; no repository rate is assumed",
    }
    if args.estimate_only:
        print(json.dumps(estimate, indent=2))
        return
    validate_call_budget(requested=len(gold), max_calls=args.max_calls)

    print(f"Generating distractors with {args.model} for {len(gold)} questions "
          f"({args.workers} workers, TrueFoundry API)...", flush=True)

    def cache_key(record):
        payload = json.dumps(
            {
                "model": args.model,
                "id": record.get("id"),
                "question": record.get("question"),
                "correct": record.get("correct"),
                "topic": record.get("topic"),
                "generation_route": estimate["generation_route"],
                "supported_labels": (
                    supported_procedure_labels(record.get("topic", ""))
                    if args.deterministic_teacher
                    else ()
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def predict(record):
        row, error = _predict(
            record,
            model=args.model,
            deterministic_teacher=args.deterministic_teacher,
        )
        if error:
            raise RuntimeError(error)
        return row

    cache_path = args.cache or f"{args.out}.cache.jsonl"
    rows, cache_stats = run_cached(
        gold,
        key_fn=cache_key,
        worker=predict,
        cache_path=cache_path,
        workers=args.workers,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(
        f"done: wrote {len(rows)} rows -> {args.out}; "
        f"paid calls this run={cache_stats['api_calls']}, resumed={cache_stats['resumed']}",
        flush=True,
    )
    print(f"score with:  python -m src.eval {args.out}", flush=True)


if __name__ == "__main__":
    main()
