"""Generate DPO preference pairs that teach the misconception->computation BINDING.

Motivation (see RESULTS_V5.md): plain SFT taught the model to EMIT valid-looking show-the-work,
but not to BIND a named misconception to the exact arithmetic it actually produces. On the eval,
even computation-VALID distractors were only ~64% judge-consistent — the model writes arithmetic
that self-evaluates but doesn't follow from the stated error (e.g. "24-40 = -16" for 0.2/0.4).

DPO fixes this with preference pairs, entirely from the engine (ZERO API cost):
  prompt   = the same chat prompt used in SFT (system + user).
  chosen   = the fully-correct target: each misconception with ITS OWN true computation+answer.
  rejected = the SAME misconception labels, but one or more distractors' computation+answer is
             swapped to a DIFFERENT misconception's arithmetic for THIS question — i.e. the
             label stays put while the shown math no longer follows from it. This is exactly the
             v5 binding failure, so preferring `chosen` over `rejected` directly trains the bind.

Both chosen and rejected are well-formed JSON in the identical schema, differ ONLY in the
misconception->arithmetic binding — so DPO's gradient isolates the binding signal, not format.

Usage:
  python -m src.dpo_pairs --n 800 --out data/processed/dpo_pairs_v6.jsonl
  python -m src.dpo_pairs --n 20 --preview     # inspect a few, no file
"""
from __future__ import annotations

import argparse
import json
import random

from .buggy_procedures import FAMILIES, fmt, generate_example, _mcs_for
from .config import DATA_PROCESSED
from .consistency import computation_consistent, to_value
from .prompts import SYSTEM_PROMPT, build_assistant, build_user
from .text_utils import normalize_answer

SEED = 23


def _distractor(misc_name, comp, answer):
    return {"misconception": misc_name, "computation": comp, "answer": normalize_answer(answer)}


def _make_pair(ex, r):
    """From a synthetic example, build (chosen_assistant, rejected_assistant) JSON strings.

    chosen: each misconception with its own true (comp, answer).
    rejected: for >=1 distractor, keep the misconception LABEL but substitute the (comp, answer)
              of a DIFFERENT misconception of the same family evaluated on THIS question, so the
              shown arithmetic no longer follows from the stated misconception. The substituted
              value must (a) differ from the true value and (b) not equal the correct answer and
              (c) not collide with the other distractors' answers (keep it well-formed + distinct).
    """
    ops, fam = ex["operands"], ex["family"]
    question = ex["question"]
    correct = to_value(ex["correct"])
    chosen = [_distractor(d["misconception"], d["computation"], d["answer"]) for d in ex["distractors"]]

    # Guard: every CHOSEN computation must pass the hardened (grounded) check. A few engine
    # misconceptions have operator-free comps (e.g. neg_ignore_second -> "-10 = -10") that would
    # teach the model to prefer a degenerate "correct" answer. Reject the whole example if any
    # chosen distractor isn't cleanly consistent -- chosen must be a flawless target.
    if any(computation_consistent(d["computation"], d["answer"], question) is not True for d in chosen):
        return None

    pool = _mcs_for(fam)
    rejected = [dict(d) for d in chosen]
    used_answers = {to_value(d["answer"]) for d in chosen}

    # Corrupt at least one distractor's binding (try each; keep the first that yields a clean swap).
    order = list(range(len(rejected)))
    r.shuffle(order)
    corrupted = 0
    for i in order:
        true_name = rejected[i]["misconception"]
        cands = [m for m in pool if m.name != true_name]
        r.shuffle(cands)
        for m in cands:
            try:
                v = m.apply(ops)
            except Exception:
                continue
            if v == correct:
                continue  # would equal the key -> not a clean distractor
            if v in used_answers:
                continue  # would duplicate another answer -> breaks distinctness
            # swap in the WRONG misconception's arithmetic under the ORIGINAL label
            used_answers.discard(to_value(rejected[i]["answer"]))
            rejected[i] = _distractor(true_name, f"{m.comp(ops)} = {fmt(v)}", fmt(v))
            used_answers.add(v)
            corrupted += 1
            break
        if corrupted >= 1:
            break
    if corrupted == 0:
        return None  # couldn't build a clean rejected; skip this example

    return build_assistant(chosen), build_assistant(rejected)


def generate_pairs(n: int, seed: int = SEED):
    r = random.Random(seed)
    out, seen, tries = [], set(), 0
    while len(out) < n and tries < n * 120:
        tries += 1
        ex = generate_example(r, families=list(FAMILIES))  # full v5 family coverage
        if not ex or ex["question"] in seen:
            continue
        pair = _make_pair(ex, r)
        if pair is None:
            continue
        chosen, rejected = pair
        if chosen == rejected:
            continue
        seen.add(ex["question"])
        out.append({
            "system": SYSTEM_PROMPT,
            "user": build_user(ex["question"], normalize_answer(ex["correct"]), ex["topic"]),
            "chosen": chosen,
            "rejected": rejected,
            "meta": {"family": ex["family"], "topic": ex["topic"], "source": "dpo_synth"},
        })
    return out


def _validate(pairs):
    """Sanity gates: chosen fully consistent, rejected has a broken binding, both well-formed."""
    n_ok_chosen = n_rej_broken = n_distinct = 0
    for p in pairs:
        q = p["user"].split("Question: ", 1)[-1].split("\nCorrect answer:")[0]
        ch = json.loads(p["chosen"])["distractors"]
        rj = json.loads(p["rejected"])["distractors"]
        # chosen: every computation grounded+consistent
        if all(computation_consistent(d.get("computation", ""), d["answer"], q) is True for d in ch):
            n_ok_chosen += 1
        # rejected: at least one distractor's shown label no longer matches its arithmetic's origin
        if p["chosen"] != p["rejected"]:
            n_rej_broken += 1
        # both keep 3 distinct answers
        if len({to_value(d["answer"]) for d in rj}) == 3 and len({to_value(d["answer"]) for d in ch}) == 3:
            n_distinct += 1
    N = len(pairs)
    print("=== DPO pair validation ===")
    print(f"pairs: {N}")
    print(f"chosen fully consistent (grounded)     : {n_ok_chosen}/{N} ({100*n_ok_chosen/N:.1f}%)  <- must be 100")
    print(f"rejected differs from chosen (broken)  : {n_rej_broken}/{N} ({100*n_rej_broken/N:.1f}%)  <- must be 100")
    print(f"both have 3 distinct answers           : {n_distinct}/{N} ({100*n_distinct/N:.1f}%)  <- must be 100")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--out", default=str(DATA_PROCESSED / "dpo_pairs_v6.jsonl"))
    ap.add_argument("--preview", action="store_true", help="print a few pairs, do not write")
    args = ap.parse_args()

    pairs = generate_pairs(args.n)
    _validate(pairs)
    if args.preview:
        for p in pairs[:3]:
            print("\n--- PROMPT ---\n" + p["user"])
            print("CHOSEN  :", p["chosen"])
            print("REJECTED:", p["rejected"])
        return
    with open(args.out, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"\n-> wrote {len(pairs)} DPO pairs to {args.out}")


if __name__ == "__main__":
    main()
