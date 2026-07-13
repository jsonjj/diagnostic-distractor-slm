"""Deterministically filter Opus teacher generations into verifier-gated v8 SFT rows."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from services.wayline_forge.app.procedure_registry import ProcedureRegistry

from .config import OPUS_MODEL_ID
from .consistency import computation_consistent
from .prompts import SYSTEM_PROMPT, build_assistant, build_user
from .text_utils import normalize_answer
from .v8_data import (
    DETERMINISTIC_TEACHER_FILTER_VERSION,
    DETERMINISTIC_TEACHER_ROUTE,
    MIN_DETERMINISTIC_TEACHER_SURVIVORS,
    question_fingerprint,
    validate_training_records,
)


DEFAULT_REPORT_PATH = Path(
    "data/processed/v8_teacher_filter_report.json"
)


def _load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@lru_cache(maxsize=1)
def _procedure_registry() -> ProcedureRegistry:
    return ProcedureRegistry.packaged_v1()


def supported_procedure_labels(topic: str) -> tuple[str, ...]:
    """Return audited canonical labels available for one exact Eedi topic."""
    return tuple(
        entry.canonical_label
        for entry in _procedure_registry().entries
        if entry.topic == str(topic)
    )


def _supported_procedure_id(topic: str, label: str) -> tuple[str | None, str | None]:
    matches = [
        entry.procedure_id
        for entry in _procedure_registry().entries
        if entry.topic == str(topic)
        and _procedure_registry().matches_alias(entry.procedure_id, label)
    ]
    if not matches:
        return None, "unsupported_procedure_mapping"
    if len(matches) != 1:
        return None, "ambiguous_procedure_mapping"
    return matches[0], None


def filter_teacher_records(
    pool: Sequence[dict],
    predictions: Sequence[dict],
    *,
    forbidden_questions: Sequence[dict] = (),
    minimum_survivors: int = MIN_DETERMINISTIC_TEACHER_SURVIVORS,
) -> tuple[list[dict], dict]:
    """Keep only rows passing repository-owned gates; no model judges are used."""
    if minimum_survivors <= 0:
        raise ValueError("minimum_survivors must be positive")
    prediction_groups: dict[str, list[dict]] = {}
    for row in predictions:
        item_id = str(row.get("id", ""))
        if item_id:
            prediction_groups.setdefault(item_id, []).append(row)
    forbidden_ids = {
        str(row.get("id"))
        for row in forbidden_questions
        if row.get("id") not in (None, "")
    }
    forbidden_fingerprints = {
        question_fingerprint(row.get("question", ""))
        for row in forbidden_questions
        if row.get("question")
    }
    survivors = []
    failures = Counter()
    seen_ids = set()
    seen_questions = set()
    for question in pool:
        item_id = str(question.get("id"))
        fingerprint = question_fingerprint(question.get("question", ""))
        if (
            not item_id
            or item_id in seen_ids
            or fingerprint in seen_questions
        ):
            failures["duplicate_candidate"] += 1
            continue
        seen_ids.add(item_id)
        seen_questions.add(fingerprint)
        if (
            item_id in forbidden_ids
            or fingerprint in forbidden_fingerprints
        ):
            failures["leakage"] += 1
            continue
        matching_predictions = prediction_groups.get(item_id, [])
        if not matching_predictions:
            failures["missing_prediction"] += 1
            continue
        if len(matching_predictions) != 1:
            failures["duplicate_prediction"] += 1
            continue
        prediction = matching_predictions[0]
        if prediction.get("generator_model") != OPUS_MODEL_ID:
            failures["wrong_teacher_model"] += 1
            continue
        if prediction.get("generation_route") != DETERMINISTIC_TEACHER_ROUTE:
            failures["wrong_generation_route"] += 1
            continue
        distractors = prediction.get("distractors", [])
        if not isinstance(distractors, list) or len(distractors) != 3:
            failures["wrong_count"] += 1
            continue
        if any(
            not isinstance(distractor, dict)
            or not all(
                isinstance(distractor.get(field), str)
                and distractor.get(field, "").strip()
                for field in ("misconception", "computation", "answer")
            )
            for distractor in distractors
        ):
            failures["invalid_schema"] += 1
            continue
        answers = [
            normalize_answer(distractor.get("answer", ""))
            for distractor in distractors
        ]
        misconceptions = [
            str(distractor.get("misconception", "")).strip().casefold()
            for distractor in distractors
        ]
        if not all(answers) or len(set(answers)) != 3:
            failures["answer_shape"] += 1
            continue
        if any(
            answer == normalize_answer(question.get("correct", ""))
            for answer in answers
        ):
            failures["key_collision"] += 1
            continue
        if not all(misconceptions) or len(set(misconceptions)) != 3:
            failures["misconception_shape"] += 1
            continue
        if not all(
            computation_consistent(
                distractor.get("computation", ""),
                distractor.get("answer", ""),
                question.get("question", ""),
                display_units=True,
            )
            is True
            for distractor in distractors
        ):
            failures["computation"] += 1
            continue
        procedure_ids = []
        mapping_error = None
        for distractor in distractors:
            procedure_id, error = _supported_procedure_id(
                question.get("topic", ""),
                distractor.get("misconception", ""),
            )
            if error:
                mapping_error = error
                break
            procedure_ids.append(str(procedure_id))
        if mapping_error:
            failures[mapping_error] += 1
            continue
        if len(set(procedure_ids)) != 3:
            failures["duplicate_procedure_mapping"] += 1
            continue
        teacher_model = str(
            prediction.get("generator_model", "unknown")
        )
        survivors.append(
            {
                "system": SYSTEM_PROMPT,
                "user": build_user(
                    question.get("question", ""),
                    question.get("correct", ""),
                    question.get("topic", ""),
                ),
                "assistant": build_assistant(distractors),
                "meta": {
                    "id": item_id,
                    "topic": question.get("topic", ""),
                    "source": "opus_distilled_real_question",
                    "teacher_model": teacher_model,
                    "teacher_filter_route": DETERMINISTIC_TEACHER_ROUTE,
                    "teacher_filter_version": (
                        DETERMINISTIC_TEACHER_FILTER_VERSION
                    ),
                    "procedure_registry_id": (
                        _procedure_registry().registry_id
                    ),
                    "procedure_ids": procedure_ids,
                    "opus_judge_used": False,
                    "task_quality_proxy": "not_available",
                    "student_option_counts_available": False,
                },
            }
        )
    if survivors:
        validate_training_records(survivors)
    report = {
        "route": DETERMINISTIC_TEACHER_ROUTE,
        "filter_version": DETERMINISTIC_TEACHER_FILTER_VERSION,
        "procedure_registry_id": _procedure_registry().registry_id,
        "minimum_survivors": minimum_survivors,
        "candidates": len(pool),
        "survivors": len(survivors),
        "rejected": len(pool) - len(survivors),
        "failure_reasons": dict(sorted(failures.items())),
        "opus_judge_used": False,
        "task_quality_proxy": "not_available",
        "student_option_frequency_claimed": False,
    }
    report["ready"] = len(survivors) >= minimum_survivors
    return survivors, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pool",
        default="data/processed/v8_teacher_pool.jsonl",
    )
    parser.add_argument("--predictions", required=True)
    parser.add_argument(
        "--out",
        default="data/processed/real_train_seed_v8_opus.jsonl",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT_PATH),
    )
    parser.add_argument(
        "--minimum-survivors",
        type=int,
        default=MIN_DETERMINISTIC_TEACHER_SURVIVORS,
    )
    parser.add_argument(
        "--forbidden",
        action="append",
        default=[
            "data/processed/eval_heldout.jsonl",
            "data/processed/eval_v8_frozen.jsonl",
        ],
        help="JSONL boundary forbidden from teacher training",
    )
    args = parser.parse_args()
    forbidden = []
    for path in args.forbidden:
        source = Path(path)
        if source.exists():
            forbidden.extend(_load_jsonl(source))
    records, report = filter_teacher_records(
        _load_jsonl(args.pool),
        _load_jsonl(args.predictions),
        forbidden_questions=forbidden,
        minimum_survivors=args.minimum_survivors,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {len(records)} verified teacher rows -> {output}")
    if not report["ready"]:
        raise SystemExit(
            "deterministic teacher survivor floor was not met"
        )


if __name__ == "__main__":
    main()
