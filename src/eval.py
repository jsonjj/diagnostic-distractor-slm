"""Eval harness: alignment@K + structural/spec checks + consistency, base-vs-tuned.

Consumes prediction files (JSONL rows: {"id", "distractors":[{"misconception","answer"}]}) produced
by base and tuned model inference. All metrics here are LOCAL and free except the optional judge
(Claude Sonnet 5 via TrueFoundry), which is only called with --judge.

Usage:
  python -m src.eval                      # local self-validation (no API)
  python -m src.eval preds.jsonl          # score a predictions file vs the real hold-out
  python -m src.eval preds.jsonl --judge  # also run the API judge (costs money)
"""
from __future__ import annotations

import argparse
import copy
import json
import random

from .config import DATA_PROCESSED
from .consistency import is_consistent
from .text_utils import normalize_answer


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------- alignment (vs real human distractors) ----------------
def alignment_metrics(golds, preds, k=3):
    n = len(golds)
    exact = partial = prop = 0.0
    for g, p in zip(golds, preds):
        gset = [normalize_answer(x) for x in g]
        pset = {normalize_answer(x) for x in p[:k]}
        matched = sum(1 for x in gset if x in pset)
        exact += 1 if gset and matched == len(gset) else 0
        partial += 1 if matched > 0 else 0
        prop += matched / len(gset) if gset else 0
    return {f"Exact@{k}": 100 * exact / n, f"Partial@{k}": 100 * partial / n, f"Proportional@{k}": 100 * prop / n}


# ---------------- structural / spec adherence ----------------
def structural_scores(preds, corrects):
    n = len(preds)
    three = distinct_m = none_key = distinct_a = spec = 0
    for p, correct in zip(preds, corrects):
        answers = [normalize_answer(d.get("answer", "")) for d in p]
        miscs = [str(d.get("misconception", "")).strip().lower() for d in p]
        c1 = len(p) == 3
        c2 = len(set(miscs)) == 3 and all(miscs)
        c3 = all(a != normalize_answer(correct) for a in answers) and all(answers)
        c4 = len(set(answers)) == 3 and all(answers)
        three += c1
        distinct_m += c2
        none_key += c3
        distinct_a += c4
        spec += 1 if (c1 and c3 and c4) else 0  # distinct misconceptions (c2) is reported, not required
    return {
        "exactly_3": 100 * three / n,
        "distinct_misconceptions": 100 * distinct_m / n,
        "none_equals_key": 100 * none_key / n,
        "distinct_answers": 100 * distinct_a / n,
        "spec_pass": 100 * spec / n,
    }


# ---------------- consistency: programmatic (structured items) ----------------
def programmatic_consistency(items):
    """items: [{family, operands, distractors:[{misconception_id, answer}]}] -> item/pair consistency %."""
    n = len(items)
    ok = 0
    total = cons = 0
    for it in items:
        all_ok = True
        for d in it["distractors"]:
            total += 1
            if is_consistent(it["family"], it["operands"], d.get("misconception_id", ""), d.get("answer")) is True:
                cons += 1
            else:
                all_ok = False
        ok += 1 if all_ok else 0
    return {
        "item_consistency": 100 * ok / n if n else 0,
        "pair_consistency": 100 * cons / total if total else 0,
    }


# ---------------- consistency: judge (gated; TrueFoundry API) ----------------
def judge_consistency(question, misconception, answer, correct) -> bool:
    from .tfy_client import chat

    sys = "You are a strict mathematics grader. Reply with only YES or NO."
    usr = (
        f"Question: {question}\nCorrect answer: {correct}\n"
        f"Claimed student misconception: {misconception}\nStudent's answer: {answer}\n\n"
        "Is the student's answer exactly the value someone making that specific misconception "
        "would compute for this question? Reply YES or NO."
    )
    out = chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}], max_tokens=5)
    return out.strip().upper().startswith("Y")


# ---------------- reports ----------------
def _self_validate(gold):
    golds = [[d["answer"] for d in r["distractors"]] for r in gold]
    corrects = [r["correct"] for r in gold]
    perfect = [[{"misconception": d["misconception"], "answer": d["answer"]} for d in r["distractors"]] for r in gold]
    rng = random.Random(0)
    idx = list(range(len(gold)))
    rng.shuffle(idx)
    shuffled = [perfect[j] for j in idx]

    print("ALIGNMENT (gold-as-prediction, expect ~100):")
    print("   ", {k: round(v, 1) for k, v in alignment_metrics(golds, [[d["answer"] for d in p] for p in perfect]).items()})
    print("ALIGNMENT (shuffled predictions, expect low):")
    print("   ", {k: round(v, 1) for k, v in alignment_metrics(golds, [[d["answer"] for d in p] for p in shuffled]).items()})
    print("STRUCTURAL (gold-as-prediction, expect ~100):")
    print("   ", {k: round(v, 1) for k, v in structural_scores(perfect, corrects).items()})

    from .buggy_procedures import generate_example

    r = random.Random(3)
    items = []
    for _ in range(200):
        ex = generate_example(r)
        if ex:
            items.append(
                {
                    "family": ex["family"],
                    "operands": ex["operands"],
                    "distractors": [{"misconception_id": d["misconception_id"], "answer": d["answer"]} for d in ex["distractors"]],
                }
            )
    print(f"CONSISTENCY (synthetic consistent, expect 100): {programmatic_consistency(items)}")
    bad = copy.deepcopy(items)
    for it in bad:
        it["distractors"][0]["answer"] = "999999"
    print(f"CONSISTENCY (one distractor perturbed, expect item~0): {programmatic_consistency(bad)}")


def report_predictions(gold, pred_path, use_judge=False):
    preds_raw = load_jsonl(pred_path)
    pmap = {str(r["id"]): r.get("distractors", []) for r in preds_raw}
    golds, corrects, preds = [], [], []
    for r in gold:
        gid = str(r["id"])
        golds.append([d["answer"] for d in r["distractors"]])
        corrects.append(r["correct"])
        preds.append(pmap.get(gid, []))
    print("ALIGNMENT:", {k: round(v, 1) for k, v in alignment_metrics(golds, [[d.get("answer", "") for d in p] for p in preds]).items()})
    print("STRUCTURAL:", {k: round(v, 1) for k, v in structural_scores(preds, corrects).items()})
    if use_judge:
        n = ok = 0
        for r, p in zip(gold, preds):
            for d in p:
                n += 1
                ok += 1 if judge_consistency(r["question"], d.get("misconception", ""), d.get("answer", ""), r["correct"]) else 0
        print(f"JUDGE CONSISTENCY (API): {100 * ok / n if n else 0:.1f}%  ({ok}/{n} pairs)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("predictions", nargs="?", help="predictions JSONL vs the real hold-out")
    ap.add_argument("--judge", action="store_true", help="also run the TrueFoundry judge (API cost)")
    args = ap.parse_args()
    gold = load_jsonl(DATA_PROCESSED / "eval_heldout.jsonl")
    if not args.predictions:
        _self_validate(gold)
    else:
        report_predictions(gold, args.predictions, use_judge=args.judge)


if __name__ == "__main__":
    main()
