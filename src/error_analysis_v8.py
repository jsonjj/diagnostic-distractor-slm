"""Deterministic error analysis for base, tuned, and frontier predictions."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from .consistency import computation_consistent, to_display_value
from .text_utils import normalize_answer


_COUNT_KEYS = (
    "items",
    "missing_output",
    "wrong_count",
    "key_collision",
    "duplicate_answers",
    "duplicate_misconceptions",
    "numeric_pairs",
    "nonnumeric_pairs",
    "computation_valid",
    "computation_invalid",
    "computation_unparseable",
    "exact_unseen_labels",
)


def _counts() -> dict:
    return {key: 0 for key in _COUNT_KEYS}


def _add(target: dict, source: dict) -> None:
    for key in _COUNT_KEYS:
        target[key] += source[key]


def analyze_predictions(
    gold: Sequence[dict],
    predictions: Sequence[dict],
    *,
    training_labels: Iterable[str] = (),
) -> dict:
    """Quantify observable failure modes without semantic overclaiming.

    ``exact_unseen_labels`` means only that a normalized generated string did not
    appear verbatim in the supplied training targets; it is not evidence that the
    misconception is pedagogically unsupported.
    """
    prediction_map = {
        str(row.get("id")): row
        for row in predictions
        if row.get("id") not in (None, "")
    }
    known_labels = {
        str(label).strip().casefold()
        for label in training_labels
        if str(label).strip()
    }
    totals = _counts()
    by_topic = defaultdict(_counts)

    for item in gold:
        topic = str(item.get("topic", "Unknown") or "Unknown")
        local = _counts()
        local["items"] = 1
        row = prediction_map.get(str(item.get("id")))
        if row is None:
            local["missing_output"] = 1
            distractors = []
        else:
            distractors = row.get("distractors", [])
            if not isinstance(distractors, list):
                distractors = []
        if len(distractors) != 3:
            local["wrong_count"] = 1

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
        correct = normalize_answer(item.get("correct", ""))
        if any(answer and answer == correct for answer in answers):
            local["key_collision"] = 1
        nonempty_answers = [answer for answer in answers if answer]
        if len(nonempty_answers) != len(set(nonempty_answers)):
            local["duplicate_answers"] = 1
        nonempty_misconceptions = [
            misconception for misconception in misconceptions if misconception
        ]
        if len(nonempty_misconceptions) != len(set(nonempty_misconceptions)):
            local["duplicate_misconceptions"] = 1
        if known_labels:
            local["exact_unseen_labels"] = sum(
                1
                for misconception in nonempty_misconceptions
                if misconception not in known_labels
            )

        for distractor in distractors:
            if not isinstance(distractor, dict):
                continue
            answer = distractor.get("answer", "")
            if to_display_value(answer) is None:
                local["nonnumeric_pairs"] += 1
            else:
                local["numeric_pairs"] += 1
            result = computation_consistent(
                distractor.get("computation", ""),
                answer,
                item.get("question", ""),
                display_units=True,
            )
            if result is True:
                local["computation_valid"] += 1
            elif result is False:
                local["computation_invalid"] += 1
            else:
                local["computation_unparseable"] += 1

        _add(totals, local)
        _add(by_topic[topic], local)

    return {
        "totals": totals,
        "by_topic": dict(sorted(by_topic.items())),
        "notes": {
            "exact_unseen_labels": (
                "Exact normalized string absence only; semantic support requires "
                "expert review or a validated label matcher."
            ),
            "computation_valid": (
                "The arithmetic is grounded and evaluates to the answer; this does "
                "not prove that the named misconception caused that arithmetic."
            ),
        },
    }


def _load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _training_labels(path: str | Path) -> set[str]:
    labels = set()
    for record in _load_jsonl(path):
        try:
            distractors = json.loads(record["assistant"]).get("distractors", [])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        labels.update(
            str(item.get("misconception", "")).strip()
            for item in distractors
            if str(item.get("misconception", "")).strip()
        )
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions")
    parser.add_argument(
        "--gold",
        default="data/processed/eval_heldout.jsonl",
    )
    parser.add_argument("--training-data")
    parser.add_argument("--out")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    labels = _training_labels(args.training_data) if args.training_data else ()
    report = analyze_predictions(
        _load_jsonl(args.gold),
        _load_jsonl(args.predictions),
        training_labels=labels,
    )
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    if not args.quiet:
        print(payload)


if __name__ == "__main__":
    main()
