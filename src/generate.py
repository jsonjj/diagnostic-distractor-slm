"""Generate synthetic SFT examples (engine + hard consistency filter) and assemble the v1 train set.

Outputs (data/processed/):
  - synth_train.jsonl   unique, consistency-verified synthetic SFT records
  - train_v1.jsonl      real seed + synthetic, shuffled -> the v1 training dataset
"""
from __future__ import annotations

import json
import random
from collections import Counter

from .buggy_procedures import generate_example
from .config import DATA_PROCESSED
from .consistency import check_synthetic_example
from .prompts import build_assistant, build_user, SYSTEM_PROMPT
from .text_utils import normalize_answer

SEED = 7
N_SYNTH = 1200


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


def main():
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


if __name__ == "__main__":
    main()
