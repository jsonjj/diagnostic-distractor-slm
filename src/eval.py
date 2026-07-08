"""Eval harness: alignment@K + structural/spec checks + consistency, base-vs-tuned.

Consumes prediction files (JSONL rows: {"id", "distractors":[{"misconception","answer"}]}) produced
by base and tuned model inference. All metrics here are LOCAL and free except the optional judges
(Claude Sonnet 5 via TrueFoundry), which are only called with --judge or --rubric.

Usage:
  python -m src.eval                       # local self-validation (no API)
  python -m src.eval preds.jsonl           # score a predictions file vs the real hold-out
  python -m src.eval preds.jsonl --judge   # also run the API YES/NO consistency judge (costs money)
  python -m src.eval preds.jsonl --rubric  # also run the Appendix-A LLM-judge rubric (0-2 x4, API cost)
"""
from __future__ import annotations

import argparse
import copy
import json
import random

from .config import DATA_PROCESSED
from .consistency import computation_consistent, is_consistent
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


# ---------------- consistency: computation (free; works on ANY computation-bearing output) ----------------
def computation_consistency(preds):
    """Free (no-API) consistency signal for computation-bearing predictions (v4 targets).

    For each predicted distractor, parse its `computation`, evaluate the left-hand side, and
    check it equals normalize_answer(answer). Backward-compatible: predictions WITHOUT a
    `computation` (old prediction files) score as not-consistent (0) and never crash.

    Returns item% (all 3 in an item consistent), pair% (per-distractor), and how many pairs
    actually carried a parseable computation (so a 0% from an old file is legible, not silent).
    """
    n = len(preds)
    item_ok = pair_ok = pairs = with_comp = 0
    for p in preds:
        all_ok = bool(p)
        for d in p:
            pairs += 1
            res = computation_consistent(d.get("computation", ""), d.get("answer", ""))
            if res is not None:
                with_comp += 1
            if res is True:
                pair_ok += 1
            else:
                all_ok = False
        item_ok += 1 if all_ok else 0
    return {
        "item_consistency": 100 * item_ok / n if n else 0,
        "pair_consistency": 100 * pair_ok / pairs if pairs else 0,
        "pairs_with_computation": with_comp,
        "pairs_total": pairs,
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


# ---------------- LLM-as-judge rubric (Appendix A; gated; TrueFoundry API) ----------------
RUBRIC_DIMS = ("spec_adherence", "robustness", "task_quality", "consistency")

_RUBRIC_SYSTEM = (
    "You are a strict evaluator of AI-generated diagnostic distractors for middle-school "
    '"Number" multiple-choice math questions. You score one item at a time on four dimensions '
    "using an integer 0-2 scale and reply with ONLY compact JSON (no prose, no code fences)."
)

# Behavior spec the generator is contracted to satisfy (mirrors src.prompts.SYSTEM_PROMPT).
_RUBRIC_SPEC = (
    "BEHAVIOR SPEC (a correct output must satisfy ALL of these):\n"
    "- Exactly 3 distractors (wrong answers).\n"
    "- Each distractor is tagged to a specific, named student misconception or procedural error.\n"
    "- Each distractor's value is exactly what a student making that stated misconception would "
    "compute for THIS question (numerically consistent with the misconception).\n"
    "- No distractor equals the correct answer.\n"
    "- The 3 distractor answers are all distinct from one another."
)


def _extract_json_obj(text):
    """Best-effort: pull the first {...} object out of a model reply. Returns dict or None."""
    text = (text or "").strip()
    try:
        start = text.index("{")
        end = text.rindex("}")
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def judge_rubric(question, correct, distractors):
    """Score one item on the four Appendix-A dimensions via the frontier judge.

    Returns {dim: int in 0..2 for dim in RUBRIC_DIMS} or None if the reply can't be parsed.

    NOTE: "robustness" is only meaningful to the extent the input set stresses the model. Scored
    here on the clean hold-out it mostly reflects "are these plausible, non-arbitrary wrong options"
    rather than true adversarial robustness; a real robustness number needs a dedicated adversarial /
    perturbed input set (stretch goal), not this clean eval.
    """
    from .tfy_client import chat

    payload = json.dumps(
        [{"misconception": d.get("misconception", ""), "answer": d.get("answer", "")} for d in distractors],
        ensure_ascii=False,
    )
    usr = (
        f"{_RUBRIC_SPEC}\n\n"
        "SCORING DIMENSIONS (each an integer 0, 1, or 2 -- 2 = fully meets, 1 = partial, 0 = fails):\n"
        "- spec_adherence: structural conformance to the spec (exactly 3, 3 distinct answers, none "
        "equals the correct answer, each has a named misconception).\n"
        "- robustness: are these plausible, non-trivial wrong options a real student could pick, rather "
        "than arbitrary/careless numbers, duplicates, or obvious giveaways.\n"
        "- task_quality: pedagogical quality -- are the tagged misconceptions realistic and diagnostic "
        "for THIS specific question, and are the answers the kind of mistakes real students actually make.\n"
        "- consistency: does each answer numerically match the misconception it is tagged with, given "
        "this question and correct answer.\n\n"
        f"QUESTION: {question}\n"
        f"CORRECT ANSWER: {correct}\n"
        f"DISTRACTORS (JSON): {payload}\n\n"
        'Return ONLY compact JSON exactly like: {"spec_adherence":0,"robustness":0,"task_quality":0,"consistency":0}'
    )
    out = chat(
        [{"role": "system", "content": _RUBRIC_SYSTEM}, {"role": "user", "content": usr}],
        max_tokens=200,
    )
    obj = _extract_json_obj(out)
    if obj is None:
        return None
    scores = {}
    for dim in RUBRIC_DIMS:
        try:
            v = int(round(float(obj[dim])))
        except (KeyError, TypeError, ValueError):
            return None
        scores[dim] = max(0, min(2, v))
    return scores


def rubric_scores(gold, preds):
    """Mean 0-2 rubric score per dimension over the items the judge scored successfully.

    Returns (means: {dim: float 0..2}, n_scored: int). Items whose reply can't be parsed are
    skipped (not counted), so means reflect only successfully-judged items.
    """
    sums = {d: 0.0 for d in RUBRIC_DIMS}
    n = 0
    for r, p in zip(gold, preds):
        s = judge_rubric(r["question"], r["correct"], p)
        if s is None:
            continue
        for d in RUBRIC_DIMS:
            sums[d] += s[d]
        n += 1
    means = {d: (sums[d] / n if n else 0.0) for d in RUBRIC_DIMS}
    return means, n


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
                    "distractors": [
                        {"misconception_id": d["misconception_id"], "computation": d.get("computation", ""), "answer": d["answer"]}
                        for d in ex["distractors"]
                    ],
                }
            )
    print(f"CONSISTENCY (synthetic consistent, expect 100): {programmatic_consistency(items)}")
    bad = copy.deepcopy(items)
    for it in bad:
        it["distractors"][0]["answer"] = "999999"
    print(f"CONSISTENCY (one distractor perturbed, expect item~0): {programmatic_consistency(bad)}")

    # computation-consistency (free, no API): same items scored via their show-the-work strings
    comp_preds = [it["distractors"] for it in items]
    cc = computation_consistency(comp_preds)
    print(f"COMPUTATION-CONSISTENCY (synthetic, expect item~100): "
          f"item {cc['item_consistency']:.1f}% | pair {cc['pair_consistency']:.1f}%")
    bad_c = copy.deepcopy(comp_preds)
    for p in bad_c:
        p[0]["computation"] = "0 + 0 = 0"  # LHS no longer equals the answer
    ccb = computation_consistency(bad_c)
    print(f"COMPUTATION-CONSISTENCY (one computation broken, expect item~0): "
          f"item {ccb['item_consistency']:.1f}% | pair {ccb['pair_consistency']:.1f}%")


def report_predictions(gold, pred_path, use_judge=False, use_rubric=False):
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
    cc = computation_consistency(preds)
    print(f"COMPUTATION CONSISTENCY (free, no API): item {cc['item_consistency']:.1f}% | pair {cc['pair_consistency']:.1f}%  "
          f"({cc['pairs_with_computation']}/{cc['pairs_total']} predicted distractors carried a parseable computation)")
    if use_judge:
        n = ok = 0
        for r, p in zip(gold, preds):
            for d in p:
                n += 1
                ok += 1 if judge_consistency(r["question"], d.get("misconception", ""), d.get("answer", ""), r["correct"]) else 0
        print(f"JUDGE CONSISTENCY (API): {100 * ok / n if n else 0:.1f}%  ({ok}/{n} pairs)")
    if use_rubric:
        means, n = rubric_scores(gold, preds)
        print(f"JUDGE RUBRIC (API, Appendix A, mean 0-2 over {n}/{len(gold)} items):")
        print("   ", {k: round(v, 3) for k, v in means.items()})
        # Reminder: on this clean hold-out "robustness" is a plausibility proxy, not a true
        # adversarial-robustness measurement (that needs a dedicated perturbed input set -- stretch).


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("predictions", nargs="?", help="predictions JSONL vs the real hold-out")
    ap.add_argument("--judge", action="store_true", help="also run the TrueFoundry YES/NO consistency judge (API cost)")
    ap.add_argument("--rubric", action="store_true", help="also run the Appendix-A LLM-judge rubric, 0-2 x4 dims (API cost)")
    args = ap.parse_args()
    gold = load_jsonl(DATA_PROCESSED / "eval_heldout.jsonl")
    if not args.predictions:
        _self_validate(gold)
    else:
        report_predictions(gold, args.predictions, use_judge=args.judge, use_rubric=args.rubric)


if __name__ == "__main__":
    main()
