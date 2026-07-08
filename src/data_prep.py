"""Build the real Eedi 'Number' SFT seed + eval hold-out + repo-schema test CSV.

Outputs (data/processed/):
  - real_train_seed.jsonl        SFT chat records from real fully-labeled Number MCQs
  - eval_heldout.jsonl           held-out real MCQs (gold distractors + misconceptions)
  - eval_test_repo_schema.csv    same hold-out, in the umass-ml4ed CSV schema for metric reuse
"""
from __future__ import annotations

import csv
import json
import random

from .config import DATA_PROCESSED, DATA_RAW, NUMBER_SUBJECTS
# The real seed is the legacy {misconception, answer} schema; v4's show-the-work
# computations are added later (src.real_computations), so keep the legacy prompt here
# to reproduce real_train_seed.jsonl byte-for-byte.
from .prompts import SYSTEM_PROMPT_LEGACY, build_assistant, build_user
from .text_utils import normalize_answer

SEED = 42
N_EVAL = 150


def load_rows():
    with open(DATA_RAW / "train.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(DATA_RAW / "misconception_mapping.csv", newline="", encoding="utf-8") as f:
        mapping = {r["MisconceptionId"]: r["MisconceptionName"] for r in csv.DictReader(f)}
    return rows, mapping


def _mid(raw: str) -> str:
    raw = (raw or "").strip()
    return raw.split(".")[0] if raw else raw


def labeled_all3(r) -> bool:
    corr = (r.get("CorrectAnswer", "") or "").strip()
    return all(_mid(r.get(f"Misconception{L}Id", "")) not in ("", "NaN", "nan") for L in "ABCD" if L != corr)


def build_record(r, mapping) -> dict:
    corr = (r.get("CorrectAnswer", "") or "").strip()
    distractors = []
    for L in "ABCD":
        if L == corr:
            continue
        mid = _mid(r.get(f"Misconception{L}Id", ""))
        distractors.append(
            {
                "option_idx": "ABCD".index(L) + 1,
                "answer": normalize_answer((r.get(f"Answer{L}Text", "") or "").strip()),
                "misconception": mapping.get(mid, ""),
            }
        )
    return {
        "id": r.get("QuestionId", ""),
        "question": (r.get("QuestionText", "") or "").strip(),
        "correct": normalize_answer((r.get(f"Answer{corr}Text", "") or "").strip()),
        "topic": (r.get("SubjectName", "") or "").strip(),
        "construct": (r.get("ConstructName", "") or "").strip(),
        "subject_id": r.get("SubjectId", ""),
        "construct_id": r.get("ConstructId", ""),
        "distractors": distractors,
    }


def to_sft(rec) -> dict:
    return {
        "system": SYSTEM_PROMPT_LEGACY,
        "user": build_user(rec["question"], rec["correct"], rec["topic"]),
        "assistant": build_assistant(rec["distractors"]),
        "meta": {"id": rec["id"], "topic": rec["topic"], "source": "eedi_real"},
    }


def _num(x):
    return int(x) if str(x).isdigit() else x


def to_repo_schema_row(rec) -> dict:
    return {
        "id": rec["id"],
        "question": rec["question"],
        "correct_option": json.dumps({"option": rec["correct"], "explanation": ""}),
        "construct_info": json.dumps(
            {
                "construct1": [_num(rec["subject_id"]), rec["topic"]],
                "construct2": [_num(rec["construct_id"]), rec["construct"]],
                "construct3": ["", ""],
            }
        ),
        "distractors": json.dumps(
            [
                {"option_idx": d["option_idx"], "option": d["answer"], "explanation": "",
                 "proportion": 0, "misconception": d["misconception"]}
                for d in rec["distractors"]
            ]
        ),
    }


def main():
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    rows, mapping = load_rows()
    num = [r for r in rows if (r.get("SubjectName", "") or "").strip() in NUMBER_SUBJECTS and labeled_all3(r)]
    recs = [build_record(r, mapping) for r in num]
    recs = [
        rc for rc in recs
        if rc["correct"] and len(rc["distractors"]) == 3
        and all(d["answer"] and d["misconception"] for d in rc["distractors"])
    ]

    rng = random.Random(SEED)
    rng.shuffle(recs)
    n_eval = min(N_EVAL, len(recs) // 2)
    eval_recs, train_recs = recs[:n_eval], recs[n_eval:]

    with open(DATA_PROCESSED / "real_train_seed.jsonl", "w", encoding="utf-8") as f:
        for rc in train_recs:
            f.write(json.dumps(to_sft(rc), ensure_ascii=False) + "\n")
    with open(DATA_PROCESSED / "eval_heldout.jsonl", "w", encoding="utf-8") as f:
        for rc in eval_recs:
            f.write(json.dumps(rc, ensure_ascii=False) + "\n")
    with open(DATA_PROCESSED / "eval_test_repo_schema.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "question", "correct_option", "construct_info", "distractors"])
        w.writeheader()
        for rc in eval_recs:
            w.writerow(to_repo_schema_row(rc))

    print(f"usable real Number all-3-labeled records: {len(recs)}")
    print(f"  eval hold-out : {len(eval_recs):4d} -> data/processed/eval_heldout.jsonl (+ eval_test_repo_schema.csv)")
    print(f"  real train seed: {len(train_recs):4d} -> data/processed/real_train_seed.jsonl")
    if train_recs:
        print("\nSample SFT record:\n" + json.dumps(to_sft(train_recs[0]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
