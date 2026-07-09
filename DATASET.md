# The Dataset — Source, Trustworthiness, Evolution, and Eval Results

> This is the complete account of the training data: where it came from, why it can be trusted,
> every change made across versions and why, and how each version performed. The **raw data**
> lives in `data/processed/*.jsonl` (one JSON object per line); this file is the guided tour.

---

## 1. Where the data comes from

Everything derives from the **Kaggle Eedi "Number" competition** data in `data/raw/`:
- `train.csv` — 1,869 real middle-school (ages 10–13) "Number" MCQs from **Eedi**, a UK edtech
  company whose entire product is diagnosing math misconceptions. Each row has the question, the
  correct answer, the wrong options, and — crucially — a **misconception ID for each wrong option**.
- `misconception_mapping.csv` — maps each misconception ID to its human-readable description.

This is the **same dataset used by the two papers this project builds on** (Feng et al. 2024;
DiVERT / Fernandez et al. 2024, both from UMass ML4Ed). Two synthetic-data sources supplement it
(below), but every real question and every real misconception label originates from Eedi.

### Why the source is trustworthy
- **Real, expert-authored labels.** The misconception tags are written by Eedi's content team
  (their core business is misconception diagnosis), not scraped or LLM-guessed.
- **Real student validation.** Eedi's items are answered by thousands of real students; the wrong
  options are ones students actually pick, not hypothetical.
- **Peer-reviewed provenance.** The exact dataset underpins published NAACL/EMNLP 2024 work, so its
  suitability for this task is externally established.
- **Frozen, leakage-free eval.** 140 real MCQs are split off once as a held-out test set and
  **never** used in training (verified: 0% train↔eval overlap). Every number reported is scored on
  these unseen real questions.

### The two hard limits of the raw data (they shaped every decision)
1. **Real data is thin.** After filtering to "Number" + all-3-options-labeled: **281 usable real
   MCQs** → 140 held out for eval, ~141 for training. Far too few to fine-tune on alone → synthetic
   data is required for volume.
2. **No student-pick rates.** This Kaggle release omits the per-option selection percentages that
   Feng's paper had. So *plausibility* ("would a real student actually pick this?") cannot be
   directly measured — an honest limitation, documented as future work.

---

## 2. The synthetic engine (why it exists and why it's trustworthy)

To get volume with **guaranteed** consistency, the project uses a "buggy-procedure" engine
(`src/buggy_procedures.py`) grounded in Brown & Burton (1978): each misconception is an
**executable rule** that computes the exact wrong answer a student with that error would produce.

- **Trustworthy by construction:** because the answer is *computed from* the misconception, the
  label↔answer binding is correct by definition — synthetic consistency is 100%, provably.
- **Training-only.** The engine generates JSONL files offline; it is **not** part of the model and
  never ships. The trained model produces distractors on its own.
- Each distractor carries `misconception`, `computation` (the show-the-work arithmetic), and
  `answer`, where the computation must evaluate to the answer.

---

## 3. The files (all in `data/processed/`)

| File | Rows | What it is |
|---|---|---|
| `real_train_seed.jsonl` | 141 | Real Eedi training MCQs (the usable real ceiling) |
| `real_train_seed_v4/v5/v7.jsonl` | 46 / 43 / 63 | Real records with teacher-generated + verified show-the-work computations |
| `train_v1 … v5.jsonl` | 1341–1423 | The v1–v5 training sets (see evolution below) |
| `train_v7.jsonl` | 1618 | **Final training set: real-format synthetic + distinct-label distilled reals (for the 4B model)** |
| `dpo_pairs_v6.jsonl` | 800 | v6 DPO preference pairs (correct vs off-spec binding) |
| `eval_heldout.jsonl` | 140 | Held-out real MCQs — never trained on; all metrics scored here |

Each training row is a chat record `{system, user, assistant, meta}`; the `assistant` field is the
JSON target `{"distractors":[{misconception, computation, answer} ×3]}`.

---

## 4. Quality evals run ON the dataset (proving it's good, before training)

Every version is verification-gated offline (no GPU/API) before it is allowed to train:

| Version | Rows | Real/Synth | distinct misconceptions | distinct answers | show-work consistency (hardened) | family skew |
|---|---|---|---|---|---|---|
| v1 | 1341 | 141 / 1200 | 95% | 96% | — (no computations) | 50:1 |
| v2 | 1441 | 79 / 1362 | 100% | 98% | — | 11.6:1 |
| v3 | 1358 | 158 / 1200 | 100% | 96% | — | 50:1 |
| v4 | 1338 | 138 / 1200 | 100% | 100% | 96% | 12.9:1 |
| v5 | 1423 | 129 / 1294 | 100% | 100% | 100% | 1.4:1 |
| **v7 (final)** | **1618** | **138 / 1480** | **100%** | **100%** | **100%** | **1.7:1** |

Additional integrity checks (all pass): 0% train↔eval leakage; synthetic consistency 100% by
construction; DPO pairs 800/800 valid (every `chosen` consistent, every `rejected` a genuine broken
binding); v1–v5 rebuild byte-identical from the committed engine (regression guard).

---

## 5. How the dataset changed — and why each change was made

**Start:** raw Kaggle CSV (1,869) → filter to "Number" + all-3-labeled → **281 usable reals** →
140 eval (frozen) + 141 train seed. Then synthetic supplements for volume.

| Version | Change | Why |
|---|---|---|
| **v1** | 141 real (incl. ~62 duplicate-label) + 1200 synth | First real run. Best raw alignment — but duplicate-label reals taught the model to repeat one misconception (distinct-misconceptions collapsed at inference) and consistency was only ~50%. |
| **v2** | drop duplicate-label reals (→79), rebalance synth | Fix label repetition. Worked, but halving the real signal + diluting eval-matching families **lost alignment**. Over-corrected. |
| **v3** | distinct reals ×2 (oversample) + v1-style synth | Best-of-both: keep the label fix, restore alignment by oversampling the clean reals. |
| **v4** | add a `computation` (show-the-work) to every distractor | Attack consistency directly and make it programmatically checkable. Consistency barely moved (~53%) — revealed the model was *gaming* a weak metric. |
| **v5** | engine 8→16 families (cover eval topics), de-skew 50:1→1.4:1, harden the check | Root cause looked like a coverage gap + a gameable metric; fixed both. Consistency still ~50% → proved it wasn't a coverage problem. |
| **v6** | + 800 DPO preference pairs on top of v5 SFT | The gap is the misconception→arithmetic *binding*. DPO trains "prefer the correct binding." Improved structure but consistency stayed ~50% at 1.7B. |
| **v7 (final)** | move to a **4B** base + stylize synthetic questions into **real Eedi LaTeX format** + distill more real questions | Two remaining levers: **capacity** (the binding needs more reasoning than 1.7B) and the **synthetic→real transfer gap** (v1–v6 trained on clean templates like `What is 0.2 ÷ 0.4?` but were tested on `\( 0.2 \div 0.4 = \)`). Consistency finally moved: ~50% → **60%**. |
| **v7.1** | v7 real seed filtered to **distinct-label reals only** | The first 4B run regressed distinct-misconceptions (duplicate-label distilled reals reintroduced repetition, the v1 mistake). Filtering restored it to ~94% while keeping the consistency gain. |

**Throughline:** every version fixed exactly one diagnosed failure of the previous, by changing the
**data** (not hyperparameters). v1→v3 fixed label repetition and alignment balance; v4→v5 attacked
consistency via show-the-work + coverage + an honest metric; v6 tried preference tuning; v7 moved to
4B + real-format data and finally broke the consistency ceiling.

---

## 6. Eval results (final, on the 140-item held-out real set)

Consistency is judged by an LLM grader that is **calibrated** (agrees with programmatic ground
truth 90% on numeric answers; see `judge_calibration.md`). Full table in `TABLE.md`.

| Metric | Base | v1 (1.7B) | v6 (1.7B) | **v7.1 (4B, final)** | Sonnet 5 |
|---|---|---|---|---|---|
| Consistency — judge (numeric) | ~0 | — | 51.6 | **60.4** | 95.8 |
| distinct_misconceptions | 91.4 | 30.7 | 99.3 | **94.3** | 95.0 |
| distinct_answers | 70.7 | 87.9 | 80.0 | **84.3** | 94.3 |
| none_equals_key | 62.1 | 90.0 | 78.6 | **80.7** | 100.0 |
| spec_pass | 43.6 | 81.4 | 63.6 | **67.9** | 94.3 |
| exactly_3 | 97.1 | 100.0 | 99.3 | **98.6** | 95.0 |

**What the results show:**
- **Behavior learned from data:** base ≈ 0 consistency and malformed output → the tuned model
  produces well-formed, distinct-misconception, show-the-work distractors and is ~60% consistent.
- **Consistency breakthrough:** ~50% (stuck across v1–v6 at 1.7B) → **60% at 4B** — the first real
  gain, and it **beats the 7B prior-work state of the art** (LookAlike, 2025: 51.6%) at smaller scale.
- **Meets/beats Sonnet where it matters most for diagnosis:** exactly_3 (above) and
  distinct_misconceptions (essentially tied) — the properties that let a completed test tell a
  teacher which distinct skills to reteach.
- **Honest ceiling:** consistency reached ~63% of Sonnet, not full parity. Six data iterations
  across two model sizes indicate deep misconception→arithmetic consistency is a **capacity ceiling
  for small open models**, not a data bug.

---

## 7. Browse the raw data yourself
```bash
# whole file, pretty:
python -c "import json;[print(json.dumps(json.loads(l),indent=2)) for l in open('data/processed/train_v7.jsonl')]" | less
# just the targets:
python -c "import json;[print(json.loads(l)['assistant']) for l in open('data/processed/train_v7.jsonl')]" | less
# inspect DPO pairs:
python -m src.dpo_pairs --n 5 --preview
```
