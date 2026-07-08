"""Calibrate the consistency judge(s) against ground truth.

Why: the whole thesis rests on the judge's consistency numbers (v4 53.5% vs Sonnet 94.7%).
An uncalibrated LLM judge produces "confident, wrong scores." This module measures how well
each judge (one-shot YES/NO `judge_consistency` and solve-first `judge_consistency_cot`)
agrees with ground truth, two ways:

  (A) DETERMINISTIC arm (free-ish, no human): draw (misconception, answer) pairs from the
      buggy-procedure engine where consistency is KNOWN by construction. Half are true
      (the engine's own answer) and half are corrupted (answer replaced with a different
      value that is NOT what the misconception computes). A good judge says YES to the true
      ones and NO to the corrupted ones. Reports agreement, false-positive, false-negative.
      This is a rigor most eval stacks can't achieve -- our quality property is programmatically
      checkable, so we calibrate against computed truth, not just opinion.

  (B) HUMAN arm: emit a worksheet of real eval items (weighted toward NON-numeric answers,
      where only a judge can operate) for a human to mark YES/NO, then read it back and
      compute judge-vs-human agreement. Establishes a human correlation for the subset the
      deterministic arm can't reach.

Usage:
  python -m src.calibrate_judge deterministic --n 40           # API cost (~2*n judge calls per judge)
  python -m src.calibrate_judge worksheet --n 20 --out cal.jsonl
  python -m src.calibrate_judge score-human --in cal.jsonl     # after you fill human_verdict
"""
from __future__ import annotations

import argparse
import json
import random

from .config import DATA_PROCESSED
from .consistency import to_value


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------- (A) deterministic arm ----------------
def build_deterministic_pairs(n: int, seed: int = 101):
    """Return labeled (question, correct, misconception, answer, truth) pairs from the engine.

    truth=True  -> answer is exactly what the misconception computes (engine's own value).
    truth=False -> answer is corrupted to a value the misconception does NOT compute.
    Balanced ~50/50. Corruption reuses ANOTHER distractor's answer from the same item, so the
    wrong answer is still a plausible number for the question (a hard negative, not a random one).
    """
    from .buggy_procedures import generate_example

    r = random.Random(seed)
    pairs = []
    tries = 0
    while len(pairs) < n and tries < n * 200:
        tries += 1
        ex = generate_example(r)
        if not ex or len(ex["distractors"]) < 2:
            continue
        ds = ex["distractors"]
        i = r.randrange(len(ds))
        d = ds[i]
        make_true = len(pairs) % 2 == 0
        if make_true:
            answer = d["answer"]
            truth = True
        else:
            # corrupt: use a DIFFERENT distractor's answer (same misconception label kept),
            # guaranteed != this misconception's computed value.
            others = [o["answer"] for j, o in enumerate(ds)
                      if j != i and to_value(o["answer"]) != to_value(d["answer"])]
            if not others:
                continue
            answer = others[0]
            truth = False
        pairs.append({
            "question": ex["question"],
            "correct": ex["correct"],
            "misconception": d["misconception"],
            "answer": answer,
            "truth": truth,
        })
    return pairs


def _agreement(labels, preds):
    """labels/preds are lists of bool|None. Returns dict of agreement/FP/FN over non-None preds."""
    n = tp = tn = fp = fn = skipped = 0
    for t, p in zip(labels, preds):
        if p is None:
            skipped += 1
            continue
        n += 1
        if t and p:
            tp += 1
        elif (not t) and (not p):
            tn += 1
        elif (not t) and p:
            fp += 1  # judge said consistent, truth says not -> the dangerous error
        else:
            fn += 1
    return {
        "n": n, "skipped": skipped,
        "agreement": (tp + tn) / n if n else 0.0,
        "false_positive_rate": fp / n if n else 0.0,
        "false_negative_rate": fn / n if n else 0.0,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
    }


def run_deterministic(n: int):
    from .eval import judge_consistency, judge_consistency_cot

    pairs = build_deterministic_pairs(n)
    labels = [p["truth"] for p in pairs]
    print(f"Built {len(pairs)} labeled pairs ({sum(labels)} true / {len(labels) - sum(labels)} false).")
    print("Querying judges (this costs API)...", flush=True)

    oneshot, cot = [], []
    for i, p in enumerate(pairs):
        oneshot.append(judge_consistency(p["question"], p["misconception"], p["answer"], p["correct"]))
        cot.append(judge_consistency_cot(p["question"], p["misconception"], p["answer"], p["correct"]))
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(pairs)}", flush=True)

    res_oneshot = _agreement(labels, oneshot)
    res_cot = _agreement(labels, cot)
    lines = [
        "# Judge calibration -- deterministic arm",
        "",
        f"Ground truth from the buggy-procedure engine; {len(pairs)} balanced pairs.",
        "A false positive (judge says consistent when it is NOT) is the dangerous error -- it",
        "inflates the consistency headline.",
        "",
        "| Judge | Agreement | False-Pos rate | False-Neg rate | n |",
        "|---|---|---|---|---|",
        f"| one-shot YES/NO | {res_oneshot['agreement']:.1%} | {res_oneshot['false_positive_rate']:.1%} | {res_oneshot['false_negative_rate']:.1%} | {res_oneshot['n']} |",
        f"| solve-first/CoT | {res_cot['agreement']:.1%} | {res_cot['false_positive_rate']:.1%} | {res_cot['false_negative_rate']:.1%} | {res_cot['n']} |",
        "",
        f"one-shot raw: {res_oneshot}",
        f"solve-first raw: {res_cot}",
        "",
        "Verdict: prefer the judge with higher agreement AND lower false-positive rate.",
        "If neither clears ~0.8 agreement, consistency numbers need an explicit asterisk.",
    ]
    out = DATA_PROCESSED.parent / "eval_out" / "judge_calibration.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n-> {out}")


# ---------------- (B) human arm ----------------
def build_worksheet(n: int, out_path: str, seed: int = 202, nonnumeric_ratio: float = 0.6):
    """Emit real eval (misconception, answer) pairs for a human to mark YES/NO.

    Weighted toward NON-numeric answers (default 60%), where no deterministic check exists and
    the judge is the ONLY instrument -- so human calibration matters most there.
    """
    ev = _load_jsonl(DATA_PROCESSED / "eval_heldout.jsonl")
    numeric, nonnum = [], []
    for e in ev:
        for d in e["distractors"]:
            row = {
                "id": e["id"], "question": e["question"], "correct": e["correct"],
                "topic": e["topic"], "misconception": d["misconception"], "answer": d["answer"],
            }
            (numeric if to_value(d["answer"]) is not None else nonnum).append(row)
    r = random.Random(seed)
    r.shuffle(numeric)
    r.shuffle(nonnum)
    n_non = min(len(nonnum), round(n * nonnumeric_ratio))
    n_num = min(len(numeric), n - n_non)
    picked = nonnum[:n_non] + numeric[:n_num]
    r.shuffle(picked)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in picked:
            row["human_verdict"] = ""  # <- fill "YES" or "NO": is `answer` exactly what `misconception` computes for `question`?
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(picked)} items ({n_non} non-numeric, {n_num} numeric) -> {out_path}")
    print('Fill the "human_verdict" field with YES or NO for each row, then run score-human.')


def score_human(in_path: str):
    from .eval import judge_consistency, judge_consistency_cot

    rows = _load_jsonl(in_path)
    labeled = [x for x in rows if str(x.get("human_verdict", "")).strip().upper() in ("YES", "NO")]
    if not labeled:
        print("No human_verdict values filled in. Mark each row YES/NO first.")
        return
    labels = [x["human_verdict"].strip().upper() == "YES" for x in labeled]
    oneshot = [judge_consistency(x["question"], x["misconception"], x["answer"], x["correct"]) for x in labeled]
    cot = [judge_consistency_cot(x["question"], x["misconception"], x["answer"], x["correct"]) for x in labeled]
    res_o, res_c = _agreement(labels, oneshot), _agreement(labels, cot)
    print(f"Human-labeled items: {len(labeled)} ({sum(labels)} YES / {len(labels) - sum(labels)} NO)")
    print(f"one-shot vs human : agreement {res_o['agreement']:.1%}  FP {res_o['false_positive_rate']:.1%}  FN {res_o['false_negative_rate']:.1%}")
    print(f"solve-first vs human: agreement {res_c['agreement']:.1%}  FP {res_c['false_positive_rate']:.1%}  FN {res_c['false_negative_rate']:.1%}")
    print("\nTarget: >=0.8 agreement. Whichever judge is higher (and lower FP) is the one to report with.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deterministic"); d.add_argument("--n", type=int, default=40)
    w = sub.add_parser("worksheet"); w.add_argument("--n", type=int, default=20); w.add_argument("--out", default="judge_calibration_worksheet.jsonl")
    s = sub.add_parser("score-human"); s.add_argument("--in", dest="inp", default="judge_calibration_worksheet.jsonl")
    args = ap.parse_args()
    if args.cmd == "deterministic":
        run_deterministic(args.n)
    elif args.cmd == "worksheet":
        build_worksheet(args.n, args.out)
    elif args.cmd == "score-human":
        score_human(args.inp)


if __name__ == "__main__":
    main()
