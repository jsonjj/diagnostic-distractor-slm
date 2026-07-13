"""Create auditable v8 binding/diagnostic-quality verdict sidecars.

This is a paid gateway operation. The CLI requires an explicit call cap and a
calibration artifact ID; ``--estimate-only`` performs no network calls.
Plausibility is an expert/Opus proxy because observed option-pick counts are absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Optional, Sequence

from .api_cache import run_cached
from .config import TFY_JUDGE_MODEL
from .consistency import to_display_value
from .run_frontier import validate_call_budget
from .tfy_client import chat


QUALITY_THRESHOLD = 3
MAX_OUTPUT_TOKENS = 120
_SYSTEM = (
    "You are a strict middle-school mathematics assessment reviewer. Evaluate one "
    "candidate distractor. Think silently and return only compact JSON."
)


def _extract_json(text: str) -> Optional[dict]:
    try:
        value = json.loads(
            str(text)[str(text).index("{") : str(text).rindex("}") + 1]
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_quality_verdict(text: str) -> Optional[dict]:
    """Parse objective binding plus strict 0-4 proxy-quality scores."""
    value = _extract_json(text)
    if value is None:
        return None
    if not isinstance(value.get("binding_valid"), bool):
        return None
    if not isinstance(value.get("misconception_specific"), bool):
        return None
    try:
        plausibility = int(value["plausibility_score"])
        diagnostic = int(value["diagnostic_value_score"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= plausibility <= 4 or not 0 <= diagnostic <= 4:
        return None
    return {
        "binding_valid": value["binding_valid"],
        "misconception_specific": value["misconception_specific"],
        "plausibility_score": plausibility,
        "diagnostic_value_score": diagnostic,
        "plausibility_pass": (
            value["misconception_specific"]
            and plausibility >= QUALITY_THRESHOLD
            and diagnostic >= QUALITY_THRESHOLD
        ),
        "quality_threshold": f"both_scores>={QUALITY_THRESHOLD}/4",
    }


def estimate_quality_run(
    predictions: Sequence[dict],
    *,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
) -> dict:
    requests = sum(
        len(row.get("distractors", []))
        for row in predictions
        if isinstance(row.get("distractors", []), list)
    )
    return {
        "requests": requests,
        "max_output_tokens_per_request": max_output_tokens,
        "max_output_tokens_total": requests * max_output_tokens,
        "dollar_cost": (
            "consult TrueFoundry account pricing; the repository does not assume "
            "an organization-specific rate"
        ),
    }


def _prompt(question: dict, distractor: dict) -> str:
    return (
        f"Question: {question.get('question', '')}\n"
        f"Correct answer: {question.get('correct', '')}\n"
        f"Topic: {question.get('topic', '')}\n"
        f"Claimed misconception: {distractor.get('misconception', '')}\n"
        f"Shown computation: {distractor.get('computation', '')}\n"
        f"Candidate wrong answer: {distractor.get('answer', '')}\n\n"
        "Apply these strict definitions:\n"
        "1. binding_valid is true only if that exact named misconception, applied "
        "to this question, genuinely produces the candidate answer.\n"
        "2. misconception_specific is true only for a concrete, diagnostic error "
        "rather than generic carelessness or restating the answer.\n"
        "3. plausibility_score (0-4) rates how believable this is as a tempting "
        "middle-school error. This is an expert proxy, not observed pick frequency.\n"
        "4. diagnostic_value_score (0-4) rates whether selecting it would support "
        "a specific reteaching decision.\n\n"
        'Return only {"binding_valid":true|false,'
        '"misconception_specific":true|false,'
        '"plausibility_score":0,"diagnostic_value_score":0}.'
    )


def judge_one(
    question: dict,
    distractor: dict,
    *,
    model: str,
    calibration_id: str,
    nonnumeric_calibration_id: Optional[str] = None,
    chat_fn=chat,
) -> dict:
    response = chat_fn(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(question, distractor)},
        ],
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    parsed = parse_quality_verdict(response)
    if parsed is None:
        raise ValueError("unparseable quality-judge response")
    answer_type = (
        "numeric"
        if to_display_value(distractor.get("answer", "")) is not None
        else "nonnumeric"
    )
    active_calibration_id = (
        calibration_id
        if answer_type == "numeric"
        else nonnumeric_calibration_id
    )
    return {
        **parsed,
        "answer_type": answer_type,
        "binding_method": (
            "calibrated_opus_judge"
            if active_calibration_id
            else "uncalibrated_opus_judge"
        ),
        "binding_calibration_id": active_calibration_id,
        "binding_calibration_scope": (
            answer_type if active_calibration_id else None
        ),
        "plausibility_method": "strict_opus_proxy",
        "judge_model": model,
    }


def _load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _accepted_calibration(
    path: str | Path,
    *,
    model: str,
    scope: str,
) -> dict:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if artifact.get("accepted") is not True:
        raise ValueError(f"{scope} calibration artifact is not accepted")
    if artifact.get("model") != model:
        raise ValueError(f"{scope} calibration model does not match judge model")
    artifact_scope = str(artifact.get("scope", "")).casefold()
    if not artifact_scope.startswith(f"{scope} "):
        raise ValueError(f"{scope} calibration artifact has the wrong scope")
    if not str(artifact.get("calibration_id", "")).strip():
        raise ValueError(f"{scope} calibration artifact has no ID")
    return artifact


def run(
    gold: Sequence[dict],
    predictions: Sequence[dict],
    *,
    model: str,
    calibration_id: str,
    nonnumeric_calibration_id: Optional[str],
    workers: int,
    cache_path: str | Path,
) -> tuple[list[dict], dict]:
    gold_map = {str(item.get("id")): item for item in gold}
    tasks = []
    for row in predictions:
        item_id = str(row.get("id"))
        question = gold_map.get(item_id)
        if question is None:
            continue
        for index, distractor in enumerate(row.get("distractors", [])):
            tasks.append(
                {
                    "id": item_id,
                    "distractor_index": index,
                    "question": question,
                    "distractor": distractor,
                }
            )

    def work(task):
        return judge_one(
            task["question"],
            task["distractor"],
            model=model,
            calibration_id=calibration_id,
            nonnumeric_calibration_id=nonnumeric_calibration_id,
        )

    def key(task):
        payload = json.dumps(
            {
                "model": model,
                "calibration_id": calibration_id,
                "nonnumeric_calibration_id": nonnumeric_calibration_id,
                "id": task["id"],
                "distractor_index": task["distractor_index"],
                "question": task["question"],
                "distractor": task["distractor"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    results, cache_stats = run_cached(
        tasks,
        key_fn=key,
        worker=work,
        cache_path=cache_path,
        workers=workers,
    )
    return (
        [
            {
                "id": task["id"],
                "distractor_index": task["distractor_index"],
                **result,
            }
            for task, result in zip(tasks, results)
        ],
        cache_stats,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions")
    parser.add_argument(
        "--gold",
        default="data/processed/eval_v8_frozen.jsonl",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default=TFY_JUDGE_MODEL)
    parser.add_argument(
        "--binding-calibration",
        default="data/eval_out/opus_binding_calibration_v8.json",
        help="accepted numeric Opus-vs-ground-truth calibration artifact",
    )
    parser.add_argument(
        "--nonnumeric-binding-calibration",
        default="data/eval_out/opus_nonnumeric_binding_calibration_v8.json",
        help="accepted Opus-vs-human nonnumeric calibration artifact",
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--cache",
        help="resumable paid-call cache (defaults to OUT.cache.jsonl)",
    )
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--estimate-only", action="store_true")
    args = parser.parse_args()
    predictions = _load_jsonl(args.predictions)
    estimate = estimate_quality_run(predictions)
    estimate["model"] = args.model
    if args.estimate_only:
        print(json.dumps(estimate, indent=2))
        return
    numeric_calibration = _accepted_calibration(
        args.binding_calibration,
        model=args.model,
        scope="numeric",
    )
    nonnumeric_calibration = _accepted_calibration(
        args.nonnumeric_binding_calibration,
        model=args.model,
        scope="nonnumeric",
    )
    validate_call_budget(
        requested=estimate["requests"],
        max_calls=args.max_calls,
    )
    verdicts, cache_stats = run(
        _load_jsonl(args.gold),
        predictions,
        model=args.model,
        calibration_id=numeric_calibration["calibration_id"],
        nonnumeric_calibration_id=nonnumeric_calibration["calibration_id"],
        workers=args.workers,
        cache_path=args.cache or f"{args.out}.cache.jsonl",
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in verdicts:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"wrote {len(verdicts)} verdicts from {args.model} -> {output}; "
        f"paid calls this run={cache_stats['api_calls']}, resumed={cache_stats['resumed']}"
    )


if __name__ == "__main__":
    main()
