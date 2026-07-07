"""Generate synthetic SFT examples (engine + hard consistency filter) and assemble the train set.

Outputs (data/processed/):
  - synth_train.jsonl      unique, consistency-verified synthetic SFT records (v1)
  - train_v1.jsonl         real seed + synthetic, shuffled -> the v1 training dataset
  - synth_train_v2.jsonl   expanded/rebalanced synthetic SFT records (v2)
  - train_v2.jsonl         distinct-only real seed + v2 synthetic -> the v2 training dataset

Usage:
  python -m src.generate        # build v1 (unchanged)
  python -m src.generate --v2   # build v2 (fixes the misconception-repetition regression)
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter

from .buggy_procedures import FAMILIES, generate_example
from .config import DATA_PROCESSED
from .consistency import check_synthetic_example
from .prompts import build_assistant, build_user, SYSTEM_PROMPT
from .text_utils import normalize_answer

SEED = 7
N_SYNTH = 1200

# --- v2 knobs: expanded misconception pool (buggy_procedures) + rebalanced sampling ---
SEED_V2 = 11
PER_FAMILY_CAP = 220  # cap large families so none dominates (v1 ranged fraction_mul=350 ... square=7)


def synth_to_sft(ex: dict) -> dict:
    correct = normalize_answer(ex["correct"])
    distractors = [
        {"misconception": d["misconception"], "answer": normalize_answer(d["answer"])}
        for d in ex["distractors"]
    ]
    return {
        "system": SYSTEM_PROMPT,
        "user": build_user(ex["question"], correct, ex["topic"]),
        "assistant": build_assistant(distractors),
        "meta": {"family": ex["family"], "topic": ex["topic"], "source": "synthetic"},
    }


def generate(n: int = N_SYNTH, seed: int = SEED):
    r = random.Random(seed)
    seen, out, tries = set(), [], 0
    while len(out) < n and tries < n * 80:
        tries += 1
        ex = generate_example(r)
        if not ex or not check_synthetic_example(ex):  # hard filter: consistency guaranteed
            continue
        if ex["question"] in seen:
            continue
        seen.add(ex["question"])
        out.append(ex)
    return out


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------- v2: distinct-only real seed + expanded, rebalanced synthetic ----------------
def _sft_distractors(rec: dict) -> list:
    """Parse [{misconception, answer}, ...] from an SFT record's assistant JSON string."""
    try:
        return json.loads(rec["assistant"]).get("distractors", [])
    except Exception:
        return []


def three_distinct_ok(rec: dict) -> bool:
    """SFT record has exactly 3 distractors with 3 DISTINCT misconceptions AND 3 distinct answers.

    Misconception comparison mirrors the eval `distinct_misconceptions` metric
    (case-insensitive, all non-empty); answers are compared after normalization.
    """
    ds = _sft_distractors(rec)
    if len(ds) != 3:
        return False
    miscs = [str(d.get("misconception", "")).strip().lower() for d in ds]
    answers = [normalize_answer(d.get("answer", "")) for d in ds]
    return len(set(miscs)) == 3 and all(miscs) and len(set(answers)) == 3 and all(answers)


def load_real_distinct():
    """Real TRAIN seed filtered to only examples whose 3 distractors have 3 distinct misconceptions.

    The ~40% of real records with duplicate misconception labels literally teach the
    repetition regression we are fixing, so they are dropped from the train seed. The
    eval hold-out files are NOT touched.
    """
    path = DATA_PROCESSED / "real_train_seed.jsonl"
    if not path.exists():
        return [], 0
    recs = load_jsonl(path)
    return [r for r in recs if three_distinct_ok(r)], len(recs)


def generate_balanced(per_family_cap: int = PER_FAMILY_CAP, seed: int = SEED_V2):
    """Unique, consistency-verified synthetic examples, sampled evenly across families.

    Each family is grown to `per_family_cap` unique questions or its natural ceiling,
    whichever is smaller, so no single family dominates (the v1 skew). generate_example
    already guarantees each example has 3 distinct misconceptions and 3 distinct answers.
    """
    r = random.Random(seed)
    out, per_family = [], {}
    for fam in FAMILIES:
        seen, kept, stale = set(), [], 0
        while len(kept) < per_family_cap and stale < 6000:
            ex = generate_example(r, family=fam)
            if not ex or not check_synthetic_example(ex) or ex["question"] in seen:
                stale += 1
                continue
            seen.add(ex["question"])
            kept.append(ex)
            stale = 0
        per_family[fam] = len(kept)
        out.extend(kept)
    return out, per_family


def build_v2():
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    exs, per_family = generate_balanced()
    synth = [synth_to_sft(ex) for ex in exs]
    with open(DATA_PROCESSED / "synth_train_v2.jsonl", "w", encoding="utf-8") as f:
        for rec in synth:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    real, real_total = load_real_distinct()

    combined = real + synth
    random.Random(SEED_V2).shuffle(combined)
    with open(DATA_PROCESSED / "train_v2.jsonl", "w", encoding="utf-8") as f:
        for rec in combined:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- report ----------------
    dropped = real_total - len(real)
    print("=== v2 build ===")
    print(f"real seed: kept {len(real)}/{real_total} with 3 distinct misconceptions "
          f"(dropped {dropped} repetition-teaching records = {100 * dropped / real_total:.1f}%)")
    print(f"synthetic (unique, consistency-verified, rebalanced): {len(synth)}")
    for fam, c in sorted(per_family.items(), key=lambda kv: -kv[1]):
        print(f"  {c:4d}  {fam}")
    print(f"\nv2 train dataset: {len(combined)}  ({len(real)} real + {len(synth)} synthetic)")
    print("  -> data/processed/train_v2.jsonl  (+ synth_train_v2.jsonl)")

    # ---------------- verification ----------------
    synth_ok = sum(1 for ex in exs if check_synthetic_example(ex))
    n_three = n_dm = n_da = 0
    for rec in combined:
        ds = _sft_distractors(rec)
        miscs = [str(d.get("misconception", "")).strip().lower() for d in ds]
        answers = [normalize_answer(d.get("answer", "")) for d in ds]
        n_three += 1 if len(ds) == 3 else 0
        n_dm += 1 if (len(set(miscs)) == 3 and all(miscs)) else 0
        n_da += 1 if (len(set(answers)) == 3 and all(answers)) else 0
    N = len(combined)
    fam_dist = Counter(ex["family"] for ex in exs)
    print("\n=== verification ===")
    print(f"synthetic consistency self-check : {synth_ok}/{len(exs)} ({100 * synth_ok / len(exs):.1f}%)")
    print(f"train_v2 exactly 3 distractors    : {n_three}/{N} ({100 * n_three / N:.1f}%)")
    print(f"train_v2 3 DISTINCT misconceptions: {n_dm}/{N} ({100 * n_dm / N:.1f}%)")
    print(f"train_v2 3 distinct answers       : {n_da}/{N} ({100 * n_da / N:.1f}%)")
    hi = max(fam_dist.values()); lo = min(fam_dist.values())
    print(f"synthetic family balance          : max {hi} / min {lo} = {hi / lo:.1f}x "
          f"(v1 was 350/7 = 50.0x)")
    return combined


def build_v1():
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    exs = generate()
    synth = [synth_to_sft(ex) for ex in exs]
    with open(DATA_PROCESSED / "synth_train.jsonl", "w", encoding="utf-8") as f:
        for rec in synth:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    real_path = DATA_PROCESSED / "real_train_seed.jsonl"
    real = load_jsonl(real_path) if real_path.exists() else []

    combined = real + synth
    random.Random(SEED).shuffle(combined)
    with open(DATA_PROCESSED / "train_v1.jsonl", "w", encoding="utf-8") as f:
        for rec in combined:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    fam = Counter(ex["family"] for ex in exs)
    print(f"synthetic (unique, consistency-verified): {len(synth)}")
    for k, v in fam.most_common():
        print(f"  {v:4d}  {k}")
    print(f"\nv1 train dataset: {len(combined)}  ({len(real)} real + {len(synth)} synthetic)")
    print("  -> data/processed/train_v1.jsonl")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--v2", action="store_true",
                    help="build the v2 dataset (distinct-only real seed + expanded/rebalanced synthetic)")
    args = ap.parse_args()
    build_v2() if args.v2 else build_v1()


if __name__ == "__main__":
    main()
