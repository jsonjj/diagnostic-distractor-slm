"""Demo: base model vs the fine-tuned model, side by side, on held-out questions.

Shows the 'behavior from data' win concretely: the base model produces malformed / duplicate /
key-colliding distractors with no misconception mapping and no shown work, while the fine-tuned
model produces 3 distinct named misconceptions each with its show-the-work arithmetic. Reads the
committed prediction files (no GPU/API needed) so it runs anywhere.

Usage:
  python -m src.demo                       # a curated set of base-fails / tuned-succeeds items
  python -m src.demo --tuned predictions_tuned_v6.jsonl --n 5
  python -m src.demo --id 35               # one specific question id
"""
from __future__ import annotations

import argparse
import json

from .config import DATA_PROCESSED
from .consistency import computation_consistent, to_value


def _load(path):
    with open(path, encoding="utf-8") as f:
        return {str(json.loads(l)["id"]): json.loads(l) for l in f if l.strip()}


def _struct_ok(pred, correct):
    ds = pred.get("distractors", [])
    if len(ds) != 3:
        return False
    ans = [to_value(d.get("answer", "")) for d in ds]
    if any(a is None for a in ans) or len(set(map(str, ans))) != 3:
        return False
    if to_value(correct) in ans:
        return False
    return len({d.get("misconception", "") for d in ds}) == 3


def _render(qid, g, base, tuned):
    q = g["question"].replace("\n", " ")
    print("=" * 78)
    print(f"Q{qid}: {q}")
    print(f"Correct answer: {g['correct']}   [{g['topic']}]")
    print("-" * 78)
    print("BASE model (well-prompted, un-tuned):")
    bd = base.get("distractors", [])
    if not bd:
        print("   (no parseable output)")
    for d in bd:
        m = d.get("misconception", "") or "(no misconception)"
        c = d.get("computation", "") or "(no working shown)"
        print(f"   - answer {d.get('answer','?'):<8} | {m[:50]} | {c}")
    ok = _struct_ok(base, g["correct"])
    print(f"   => well-formed diagnostic set? {'YES' if ok else 'NO'}")
    print("-" * 78)
    print("FINE-TUNED model:")
    for d in tuned.get("distractors", []):
        m = d.get("misconception", "")
        c = d.get("computation", "")
        cons = computation_consistent(c, d.get("answer", ""), g["question"])
        mark = "✓" if cons is True else "·"
        print(f"   - answer {d.get('answer','?'):<8} | {m[:50]} | {c} {mark}")
    ok2 = _struct_ok(tuned, g["correct"])
    print(f"   => well-formed diagnostic set? {'YES' if ok2 else 'NO'}   (✓ = computation checks out)")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="predictions_base_v1.jsonl")
    ap.add_argument("--tuned", default="predictions_tuned_v6.jsonl")
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--id", type=str, default=None)
    args = ap.parse_args()

    gold = _load(DATA_PROCESSED / "eval_heldout.jsonl")
    base = _load(args.base)
    tuned = _load(args.tuned)

    if args.id:
        g = gold.get(args.id)
        if not g:
            print(f"id {args.id} not in eval set")
            return
        _render(args.id, g, base.get(args.id, {}), tuned.get(args.id, {}))
        return

    # curate: base malformed, tuned well-formed, tuned has >=2 computation-valid, short question
    picks = []
    for qid, g in gold.items():
        b, t = base.get(qid, {}), tuned.get(qid, {})
        if _struct_ok(b, g["correct"]) or not _struct_ok(t, g["correct"]):
            continue
        vc = sum(1 for d in t["distractors"]
                 if computation_consistent(d.get("computation", ""), d.get("answer", ""), g["question"]) is True)
        if vc >= 2 and len(g["question"]) < 75:
            picks.append((vc, qid, g, b, t))
    picks.sort(key=lambda x: -x[0])
    print(f"Base-fails / tuned-succeeds demo — {len(picks)} such items in the hold-out; showing {min(args.n, len(picks))}.\n")
    for _, qid, g, b, t in picks[:args.n]:
        _render(qid, g, b, t)


if __name__ == "__main__":
    main()
