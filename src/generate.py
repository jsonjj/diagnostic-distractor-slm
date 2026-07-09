"""Generate synthetic SFT examples (engine + hard consistency filter) and assemble the train set.

Outputs (data/processed/):
  - synth_train.jsonl      unique, consistency-verified synthetic SFT records (v1)
  - train_v1.jsonl         real seed + synthetic, shuffled -> the v1 training dataset
  - synth_train_v2.jsonl   expanded/rebalanced synthetic SFT records (v2)
  - train_v2.jsonl         distinct-only real seed + v2 synthetic -> the v2 training dataset

Usage:
  python -m src.generate        # build v1 (unchanged)
  python -m src.generate --v2   # build v2 (fixes the misconception-repetition regression)
  python -m src.generate --v3   # build v3 (v2's distinct labels + v1's alignment recovery)
  python -m src.generate --v4   # build v4 (v3's composition + a show-the-work `computation` per distractor)
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter

from .buggy_procedures import FAMILIES, LEGACY_FAMILIES, generate_example
from .config import DATA_PROCESSED
from .consistency import check_synthetic_example, computation_consistent
from .prompts import build_assistant, build_user, SYSTEM_PROMPT, SYSTEM_PROMPT_LEGACY
from .text_utils import normalize_answer

SEED = 7
N_SYNTH = 1200

# --- v2 knobs: expanded misconception pool (buggy_procedures) + rebalanced sampling ---
SEED_V2 = 11
PER_FAMILY_CAP = 220  # cap large families so none dominates (v1 ranged fraction_mul=350 ... square=7)


def synth_to_sft(ex: dict, with_computation: bool = False) -> dict:
    """Synthetic example -> SFT chat record.

    with_computation=False (v1/v2/v3): legacy {misconception, answer} target under the
    legacy system prompt -- byte-for-byte identical to the original output.
    with_computation=True (v4): show-the-work {misconception, computation, answer} target
    under the v4 system prompt.
    """
    correct = normalize_answer(ex["correct"])
    if with_computation:
        distractors = [
            {
                "misconception": d["misconception"],
                "computation": d.get("computation", ""),
                "answer": normalize_answer(d["answer"]),
            }
            for d in ex["distractors"]
        ]
    else:
        distractors = [
            {"misconception": d["misconception"], "answer": normalize_answer(d["answer"])}
            for d in ex["distractors"]
        ]
    return {
        "system": SYSTEM_PROMPT if with_computation else SYSTEM_PROMPT_LEGACY,
        "user": build_user(ex["question"], correct, ex["topic"]),
        "assistant": build_assistant(distractors),
        "meta": {"family": ex["family"], "topic": ex["topic"], "source": "synthetic"},
    }


def generate(n: int = N_SYNTH, seed: int = SEED):
    r = random.Random(seed)
    seen, out, tries = set(), [], 0
    while len(out) < n and tries < n * 80:
        tries += 1
        ex = generate_example(r, families=LEGACY_FAMILIES)  # pinned to 8 for v1-v4 byte-identity
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


def generate_balanced(per_family_cap: int = PER_FAMILY_CAP, seed: int = SEED_V2,
                      families=None, harden: bool = False):
    """Unique, consistency-verified synthetic examples, sampled evenly across families.

    Each family is grown to `per_family_cap` unique questions or its natural ceiling,
    whichever is smaller, so no single family dominates (the v1 skew). generate_example
    already guarantees each example has 3 distinct misconceptions and 3 distinct answers.

    `families` defaults to LEGACY_FAMILIES (the 8 original) so v2's build stays byte-identical
    after v5 added families to FAMILIES; v5 passes the full 16-family list. `harden=True` (v5)
    additionally requires each computation to be operator-bearing + question-grounded.
    """
    fams = families if families is not None else LEGACY_FAMILIES
    r = random.Random(seed)
    out, per_family = [], {}
    for fam in fams:
        seen, kept, stale = set(), [], 0
        while len(kept) < per_family_cap and stale < 6000:
            ex = generate_example(r, family=fam)
            if not ex or not check_synthetic_example(ex, harden=harden) or ex["question"] in seen:
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


# ---------------- v3: distinct-only real seed (oversampled) + v1-style eval-matching synthetic ----------------
SEED_V3 = 13
REAL_REPEAT_V3 = 2  # oversample the 79 distinct-label reals so the real signal isn't diluted (v2 halved it)


def build_v3():
    """Best-of-both: keep v2's distinct-misconception discipline AND recover v1's alignment.

    Every training target still has 3 distinct misconceptions (the v1 collapse never re-enters,
    because the duplicate-label reals stay out). Two changes vs v2, both aimed at the alignment
    drop rather than the labels:
      1. real seed = the 79 distinct-label reals, oversampled x REAL_REPEAT_V3. v2 let the real
         fraction fall to ~5.5%; v1's was ~10.5%. Oversampling restores the real teacher signal
         that alignment (an answer-overlap metric) actually depends on.
      2. synthetic = the v1 (eval-matching) mix, NOT v2's flat per-family rebalance. v2's balancing
         diluted the families that resemble the eval set, which is the other half of the alignment cost.
    """
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    # v1-style synthetic: reuse the on-disk v1 synth if present (identical, eval-matching), else regenerate.
    synth_path = DATA_PROCESSED / "synth_train.jsonl"
    synth = load_jsonl(synth_path) if synth_path.exists() else [synth_to_sft(ex) for ex in generate()]

    real, real_total = load_real_distinct()
    real_up = real * REAL_REPEAT_V3

    combined = real_up + synth
    random.Random(SEED_V3).shuffle(combined)
    with open(DATA_PROCESSED / "train_v3.jsonl", "w", encoding="utf-8") as f:
        for rec in combined:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- report ----------------
    dropped = real_total - len(real)
    real_frac = 100 * len(real_up) / len(combined)
    print("=== v3 build ===")
    print(f"real seed: {len(real)}/{real_total} distinct-label reals, oversampled x{REAL_REPEAT_V3} "
          f"-> {len(real_up)} records (dropped {dropped} duplicate-label reals, kept OUT to avoid re-teaching repeats)")
    print(f"synthetic (v1 eval-matching mix, unique + consistency-verified): {len(synth)}")
    print(f"\nv3 train dataset: {len(combined)}  ({len(real_up)} real-oversampled + {len(synth)} synthetic; real weight {real_frac:.1f}%)")
    print("  -> data/processed/train_v3.jsonl")

    # ---------------- verification ----------------
    n_three = n_dm = n_da = 0
    for rec in combined:
        ds = _sft_distractors(rec)
        miscs = [str(d.get("misconception", "")).strip().lower() for d in ds]
        answers = [normalize_answer(d.get("answer", "")) for d in ds]
        n_three += 1 if len(ds) == 3 else 0
        n_dm += 1 if (len(set(miscs)) == 3 and all(miscs)) else 0
        n_da += 1 if (len(set(answers)) == 3 and all(answers)) else 0
    N = len(combined)
    print("\n=== verification ===")
    print(f"train_v3 exactly 3 distractors    : {n_three}/{N} ({100 * n_three / N:.1f}%)")
    print(f"train_v3 3 DISTINCT misconceptions: {n_dm}/{N} ({100 * n_dm / N:.1f}%)  <- must be ~100 (the v1 fix)")
    print(f"train_v3 3 distinct answers       : {n_da}/{N} ({100 * n_da / N:.1f}%)")
    return combined


# ---------------- v4: v3's composition + a show-the-work `computation` in EVERY target ----------------
SEED_V4 = 17
REAL_REPEAT_V4 = 3  # x3 restores ~10.3% real weight (46 verified reals -> 138); x2 fell to 7.1% vs v3's 11.6%


def _no_empty_fields(rec: dict) -> bool:
    ds = _sft_distractors(rec)
    return bool(ds) and all(
        str(d.get("misconception", "")).strip()
        and str(d.get("computation", "")).strip()
        and str(d.get("answer", "")).strip()
        for d in ds
    )


def _computation_stats(records):
    """(item_ok, n_items, pair_ok, n_pairs): does each distractor's computation LHS == its answer."""
    item_ok = pair_ok = pairs = 0
    for rec in records:
        ds = _sft_distractors(rec)
        all_ok = bool(ds)
        for d in ds:
            pairs += 1
            if computation_consistent(d.get("computation", ""), d.get("answer", "")) is True:
                pair_ok += 1
            else:
                all_ok = False
        item_ok += 1 if all_ok else 0
    return item_ok, len(records), pair_ok, pairs


def build_v4():
    """v4 = v3's composition, but every distractor target also SHOWS THE WORK.

    Same shape as v3 -- the distinct-misconception reals (oversampled x REAL_REPEAT_V4) plus the
    1,200 v1-style eval-matching synthetic examples -- except each distractor now carries a
    `computation` string (e.g. "0.4 \u00f7 0.2 = 2"). Synthetic computations come from the buggy-
    procedure engine (guaranteed to evaluate to the answer). Real computations are teacher-generated
    and kept only if they verify programmatically (src.real_computations), which also filters out the
    inconsistent reals -- so the real seed here is the verified subset of the 79.
    """
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    from .real_computations import ensure_real_v4  # lazy: only v4 needs the (cached) teacher output

    # synthetic WITH computation: identical questions to the v1/v3 mix (same seed/filter), + computation
    exs = generate()
    synth = [synth_to_sft(ex, with_computation=True) for ex in exs]
    with open(DATA_PROCESSED / "synth_train_v4.jsonl", "w", encoding="utf-8") as f:
        for rec in synth:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # real WITH computation: verified survivors of the 79 distinct-misconception reals (teacher-generated)
    real = ensure_real_v4()
    real_up = real * REAL_REPEAT_V4

    combined = real_up + synth
    random.Random(SEED_V4).shuffle(combined)
    with open(DATA_PROCESSED / "train_v4.jsonl", "w", encoding="utf-8") as f:
        for rec in combined:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- report ----------------
    real_frac = 100 * len(real_up) / len(combined)
    print("=== v4 build ===")
    print(f"real seed: {len(real)}/79 distinct-misconception reals survived teacher+verify, "
          f"oversampled x{REAL_REPEAT_V4} -> {len(real_up)} records "
          f"(dropped {79 - len(real)} = {100 * (79 - len(real)) / 79:.1f}% whose computation couldn't be verified)")
    print(f"synthetic (v1 eval-matching mix, unique + consistency-verified, WITH computation): {len(synth)}")
    print(f"\nv4 train dataset: {len(combined)}  ({len(real_up)} real-oversampled + {len(synth)} synthetic; "
          f"real weight {real_frac:.1f}%)")
    print("  -> data/processed/train_v4.jsonl  (+ synth_train_v4.jsonl, real_train_seed_v4.jsonl)")

    # ---------------- verification ----------------
    n_three = n_dm = n_da = n_ne = n_json = 0
    for rec in combined:
        try:
            json.loads(rec["assistant"])
            n_json += 1
        except Exception:
            pass
        ds = _sft_distractors(rec)
        miscs = [str(d.get("misconception", "")).strip().lower() for d in ds]
        answers = [normalize_answer(d.get("answer", "")) for d in ds]
        n_three += 1 if len(ds) == 3 else 0
        n_dm += 1 if (len(set(miscs)) == 3 and all(miscs)) else 0
        n_da += 1 if (len(set(answers)) == 3 and all(answers)) else 0
        n_ne += 1 if _no_empty_fields(rec) else 0
    N = len(combined)

    s_item, s_n, s_pair, s_pairs = _computation_stats(synth)
    r_item, r_n, r_pair, r_pairs = _computation_stats(real_up)

    def pct(a, b):
        return 100 * a / b if b else 0.0

    print("\n=== verification ===")
    print(f"valid JSON                        : {n_json}/{N} ({pct(n_json, N):.1f}%)")
    print(f"exactly 3 distractors             : {n_three}/{N} ({pct(n_three, N):.1f}%)")
    print(f"3 DISTINCT misconceptions         : {n_dm}/{N} ({pct(n_dm, N):.1f}%)")
    print(f"3 distinct answers                : {n_da}/{N} ({pct(n_da, N):.1f}%)")
    print(f"no empty fields (misc/comp/answer): {n_ne}/{N} ({pct(n_ne, N):.1f}%)")
    print(f"SYNTHETIC computation-consistency : item {pct(s_item, s_n):.1f}% ({s_item}/{s_n}) | "
          f"pair {pct(s_pair, s_pairs):.1f}% ({s_pair}/{s_pairs})   <- must be 100 (engine guarantee)")
    print(f"REAL     computation-consistency  : item {pct(r_item, r_n):.1f}% ({r_item}/{r_n}) | "
          f"pair {pct(r_pair, r_pairs):.1f}% ({r_pair}/{r_pairs})   <- must be 100 (verify filter guarantee)")
    return combined


# ---------------- v5: broad-coverage engine (de-skewed) + show-the-work + grown real set ----------------
SEED_V5 = 19
REAL_REPEAT_V5 = 3        # oversample verified reals to ~10-12% weight (tuned after real yield known)
PER_FAMILY_CAP_V5 = 90    # 15 families capped at 90 -> ~1150 synth; ratio driven only by legacy `square`
# v5 family list: all engine families EXCEPT the legacy `square` (n=2..20 -> only 18 questions, which
# would force the whole set to <=54 for a 3:1 ratio). `square`/Squares-Cubes coverage is retained via
# the v1-v4 lineage; eval has only 5 square items. Everything else reaches >=63, so <=90 stays ~<=1.4:1.
V5_FAMILIES = [f for f in FAMILIES if f != "square"]


def build_v5():
    """v5 = attack the consistency gap via COVERAGE.

    Two changes vs v4: (1) the engine now spans 16 families covering the eval's high-count
    topics (Place Value, Rounding, Indices, Factors/HCF, decimal add/sub, conversions, mental
    arithmetic) instead of 8 -- so the model learns to COMPUTE the misconceptions it is tested
    on, rather than fabricating arithmetic that games the free metric; (2) synthetic is sampled
    BALANCED (de-skewed to <=3:1) WITH show-the-work computations. Real seed = the grown,
    hardened-verified survivors (src.real_computations.ensure_real_v5), oversampled x REAL_REPEAT_V5.
    Every synthetic example passes the HARDENED (operator-bearing + question-grounded) self-check.
    """
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    from .real_computations import ensure_real_v5  # lazy: only v5 needs the (cached) teacher output

    # synthetic WITH computation, balanced across the v5 family set, hardened self-check
    exs, per_family = generate_balanced(per_family_cap=PER_FAMILY_CAP_V5, seed=SEED_V5,
                                        families=V5_FAMILIES, harden=True)
    synth = [synth_to_sft(ex, with_computation=True) for ex in exs]
    with open(DATA_PROCESSED / "synth_train_v5.jsonl", "w", encoding="utf-8") as f:
        for rec in synth:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # real WITH computation: grown + hardened-verified survivors, oversampled
    real = ensure_real_v5()
    real_up = real * REAL_REPEAT_V5

    combined = real_up + synth
    random.Random(SEED_V5).shuffle(combined)
    with open(DATA_PROCESSED / "train_v5.jsonl", "w", encoding="utf-8") as f:
        for rec in combined:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- report ----------------
    real_frac = 100 * len(real_up) / len(combined) if combined else 0
    print("=== v5 build ===")
    print(f"real seed: {len(real)} hardened-verified reals, oversampled x{REAL_REPEAT_V5} -> {len(real_up)} records")
    print(f"synthetic (16-family balanced, WITH computation): {len(synth)}")
    for fam, c in sorted(per_family.items(), key=lambda kv: -kv[1]):
        print(f"  {c:4d}  {fam}")
    print(f"\nv5 train dataset: {len(combined)}  ({len(real_up)} real-oversampled + {len(synth)} synthetic; "
          f"real weight {real_frac:.1f}%)")
    print("  -> data/processed/train_v5.jsonl  (+ synth_train_v5.jsonl, real_train_seed_v5.jsonl)")

    # ---------------- verification ----------------
    n_three = n_dm = n_da = n_ne = n_json = n_nek = 0
    for rec in combined:
        try:
            json.loads(rec["assistant"])
            n_json += 1
        except Exception:
            pass
        ds = _sft_distractors(rec)
        miscs = [str(d.get("misconception", "")).strip().lower() for d in ds]
        answers = [normalize_answer(d.get("answer", "")) for d in ds]
        n_three += 1 if len(ds) == 3 else 0
        n_dm += 1 if (len(set(miscs)) == 3 and all(miscs)) else 0
        n_da += 1 if (len(set(answers)) == 3 and all(answers)) else 0
        n_ne += 1 if _no_empty_fields(rec) else 0

    # synthetic computation-consistency under the HARDENED (grounded) check
    s_item = s_pair = s_pairs = 0
    for ex in exs:
        all_ok = True
        for d in ex["distractors"]:
            s_pairs += 1
            if computation_consistent(d.get("computation", ""), d["answer"], ex["question"]) is True:
                s_pair += 1
            else:
                all_ok = False
        s_item += 1 if all_ok else 0
    r_item, r_n, r_pair, r_pairs = _computation_stats(real_up)
    N = len(combined)
    fam_dist = Counter(ex["family"] for ex in exs)
    hi = max(fam_dist.values()); lo = min(fam_dist.values())

    def pct(a, b):
        return 100 * a / b if b else 0.0

    print("\n=== verification ===")
    print(f"valid JSON                        : {n_json}/{N} ({pct(n_json, N):.1f}%)")
    print(f"exactly 3 distractors             : {n_three}/{N} ({pct(n_three, N):.1f}%)")
    print(f"3 DISTINCT misconceptions         : {n_dm}/{N} ({pct(n_dm, N):.1f}%)")
    print(f"3 distinct answers                : {n_da}/{N} ({pct(n_da, N):.1f}%)")
    print(f"no empty fields                   : {n_ne}/{N} ({pct(n_ne, N):.1f}%)")
    print(f"SYNTHETIC computation-consistency (HARDENED/grounded): item {pct(s_item, len(exs)):.1f}% | "
          f"pair {pct(s_pair, s_pairs):.1f}%   <- must be 100")
    print(f"REAL     computation-consistency  : item {pct(r_item, r_n):.1f}% | pair {pct(r_pair, r_pairs):.1f}%")
    print(f"synthetic family balance          : max {hi} / min {lo} = {hi / lo:.1f}x  (target <=3.0x; v4 ~50x)")
    return combined


# ---------------- v7: real-FORMAT synthetic + grown distilled reals (for a 4B base) ----------------
SEED_V7 = 29
REAL_REPEAT_V7 = 3
PER_FAMILY_CAP_V7 = 110  # more per family: 4B can absorb more, and format variety adds effective diversity


def build_v7():
    """v7 = attack the synthetic->real TRANSFER gap (the likely consistency ceiling), for a 4B base.

    Same guaranteed-consistent engine as v5, but each synthetic question is STYLIZED into real
    Eedi format (LaTeX, varied stems) so the model trains on the format it is tested on -- the
    plain-text->LaTeX gap is a prime suspect for why in-distribution consistency didn't transfer.
    Real seed = v5's hardened-verified reals PLUS the grown teacher-distilled reals
    (ensure_real_v7). Stylization is guarded: if a stylized question breaks the hardened check for
    any distractor, we keep the plain question for that example, so consistency stays 100%.
    TRAINING-ONLY: the stylizer/engine never ship; only the trained model does.
    """
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    from .format_augment import stylize
    from .real_computations import ensure_real_v5, ensure_real_v7

    exs, per_family = generate_balanced(per_family_cap=PER_FAMILY_CAP_V7, seed=SEED_V7,
                                        families=V5_FAMILIES, harden=True)
    # stylize each question into real Eedi format, with a consistency guard (fall back to plain)
    r = random.Random(SEED_V7)
    n_styl = 0
    for ex in exs:
        plain = ex["question"]
        styled = stylize(plain, r)
        if styled != plain and all(
            computation_consistent(d.get("computation", ""), d["answer"], styled) is True
            for d in ex["distractors"] if d.get("computation")
        ):
            ex["question"] = styled
            n_styl += 1
        # else: keep plain (guard) -- guarantees the hardened check still passes
    synth = [synth_to_sft(ex, with_computation=True) for ex in exs]
    with open(DATA_PROCESSED / "synth_train_v7.jsonl", "w", encoding="utf-8") as f:
        for rec in synth:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # reals: v5's verified set + the grown teacher-distilled set (dedup by user string).
    # DISTINCT-LABEL ONLY: duplicate-misconception reals teach label-repetition (they crashed
    # distinct_misconceptions 99->72 in the first v7 run). Filtering restores the above-Sonnet win
    # without touching the format-augmentation that lifted consistency.
    reals = {r["user"]: r for r in ensure_real_v5()}
    for rec in ensure_real_v7():
        reals[rec["user"]] = rec
    real = [r for r in reals.values() if three_distinct_ok(r)]
    real_up = real * REAL_REPEAT_V7

    combined = real_up + synth
    random.Random(SEED_V7).shuffle(combined)
    with open(DATA_PROCESSED / "train_v7.jsonl", "w", encoding="utf-8") as f:
        for rec in combined:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------- report + verification ----------------
    real_frac = 100 * len(real_up) / len(combined) if combined else 0
    print("=== v7 build (for Qwen3-4B) ===")
    print(f"real seed: {len(real)} unique verified reals (v5 {len(ensure_real_v5())} + distilled), x{REAL_REPEAT_V7} -> {len(real_up)}")
    print(f"synthetic: {len(synth)} (real-format stylized: {n_styl}/{len(exs)} = {100*n_styl/len(exs):.0f}%)")
    print(f"v7 train dataset: {len(combined)}  (real weight {real_frac:.1f}%)")

    n_three = n_dm = n_da = 0
    s_pair = s_pairs = 0
    for ex in exs:
        for d in ex["distractors"]:
            if d.get("computation"):
                s_pairs += 1
                if computation_consistent(d["computation"], d["answer"], ex["question"]) is True:
                    s_pair += 1
    for rec in combined:
        ds = _sft_distractors(rec)
        miscs = [str(d.get("misconception", "")).strip().lower() for d in ds]
        answers = [normalize_answer(d.get("answer", "")) for d in ds]
        n_three += 1 if len(ds) == 3 else 0
        n_dm += 1 if (len(set(miscs)) == 3 and all(miscs)) else 0
        n_da += 1 if (len(set(answers)) == 3 and all(answers)) else 0
    N = len(combined)
    fam = Counter(ex["family"] for ex in exs)
    hi, lo = max(fam.values()), min(fam.values())
    print("\n=== verification ===")
    print(f"exactly 3 / distinct misc / distinct ans : {100*n_three/N:.0f}% / {100*n_dm/N:.0f}% / {100*n_da/N:.0f}%")
    print(f"SYNTHETIC computation-consistency (hardened, on STYLIZED q): {100*s_pair/s_pairs:.1f}%  <- must be 100")
    print(f"family balance: {hi}/{lo} = {hi/lo:.1f}x")
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
    ap.add_argument("--v3", action="store_true",
                    help="build the v3 dataset (distinct-only real seed oversampled + v1-style eval-matching synthetic)")
    ap.add_argument("--v4", action="store_true",
                    help="build the v4 dataset (v3's composition + a show-the-work `computation` in every distractor)")
    ap.add_argument("--v5", action="store_true",
                    help="build the v5 dataset (16-family de-skewed coverage + show-the-work + grown verified reals)")
    ap.add_argument("--v7", action="store_true",
                    help="build the v7 dataset (real-FORMAT stylized synthetic + distilled reals, for a 4B base)")
    args = ap.parse_args()
    if args.v7:
        build_v7()
    elif args.v5:
        build_v5()
    elif args.v4:
        build_v4()
    elif args.v3:
        build_v3()
    elif args.v2:
        build_v2()
    else:
        build_v1()


if __name__ == "__main__":
    main()
