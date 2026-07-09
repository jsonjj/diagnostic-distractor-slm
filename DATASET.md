# The Dataset — Full Contents, Quality Evals, and Evolution

> This is the browsable overview. The **complete raw data** lives in `data/processed/*.jsonl`
> (one JSON object per line). This file shows: (1) what each file is, (2) the quality evals we ran
> on every version to prove the data was good, (3) samples, and (4) how the dataset changed from
> start to finish and why each change was made.

## 1. The files (all in `data/processed/`)

| File | Rows | What it is |
|---|---|---|
| `real_train_seed.jsonl` | 141 | Real Eedi "Number" MCQs (misconception+answer), the ceiling of usable real data |
| `real_train_seed_v4/v5.jsonl` | 46 / 43 | Real records with teacher-generated + verified show-the-work computations |
| `train_v1.jsonl` | 1341 | v1 training set (141 real + 1200 synthetic) |
| `train_v2.jsonl` | 1441 | v2 (distinct-label reals + rebalanced synthetic) |
| `train_v3.jsonl` | 1358 | v3 (distinct reals ×2 + v1-style synthetic) |
| `train_v4.jsonl` | 1338 | v4 (v3 composition + show-the-work `computation`) |
| `train_v5.jsonl` | 1423 | **v5 (final SFT set): 16-family de-skewed + show-the-work** |
| `dpo_pairs_v6.jsonl` | 800 | **v6 DPO preference pairs (chosen vs off-spec rejected)** |
| `eval_heldout.jsonl` | 140 | Held-out real MCQs — NEVER trained on; every metric is scored here |

Each training row is a chat record: `{system, user, assistant, meta}`. The `assistant` field is the
JSON target: `{"distractors":[{misconception, computation, answer} ×3]}`.

## 2. Quality evals run ON the dataset (proving it's good, before training)

Every version is verification-gated offline (no GPU/API). Results per version:

| Version | Rows | Real/Synth | 3 distinct misconceptions | 3 distinct answers | Show-work consistency (hardened) | Family skew |
|---|---|---|---|---|---|---|
| v1 | 1341 | 141 / 1200 | 95% | 96% | — (no computations) | 50:1 |
| v2 | 1441 | 79 / 1362 | 100% | 98% | — | 11.6:1 |
| v3 | 1358 | 158 / 1200 | 100% | 96% | — | 50:1 |
| v4 | 1338 | 138 / 1200 | 100% | 100% | 96% | 12.9:1 |
| **v5** | **1423** | **129 / 1294** | **100%** | **100%** | **100%** | **1.4:1** |

Additional dataset-integrity checks (all pass):
- **0% train↔eval leakage** — the 140-item hold-out is split off once and never enters training.
- **100% synthetic consistency by construction** — the buggy-procedure engine computes each
  distractor from its misconception, so the label↔value binding is true by definition.
- **v5 hardened self-check: 3,882/3,882 synthetic computation-pairs** operator-bearing + grounded.
- **DPO pairs (v6): 800/800** — every `chosen` fully consistent, every `rejected` a genuine
  broken binding, both well-formed with 3 distinct answers.
- **Backward-compat:** v2 and v4 rebuild byte-identical from the committed engine (regression guard).

## 3. Samples

**v5 SFT target** (what the model is trained to output):
```
USER: Question: What is 6 + 6 × 6?  Correct answer: 42  Topic: BIDMAS
ASSISTANT: {"distractors": [
  {"misconception": "Carries out operations left to right, ignoring order of operations",
   "computation": "(6 + 6) × 6 = 72", "answer": "72"},
  {"misconception": "Adds all the numbers, ignoring the multiplication",
   "computation": "6 + 6 + 6 = 18", "answer": "18"},
  {"misconception": "Multiplies all the numbers together",
   "computation": "6 × 6 × 6 = 216", "answer": "216"}]}
```

**v6 DPO pair** (teaches the misconception→arithmetic binding):
```
CHOSEN  : "Adds numerators but multiplies the denominators" -> (1 + 6)/(3 × 9) = 7/27   (true)
REJECTED: "Adds numerators but multiplies the denominators" -> (1 + 6)/9 = 7/9          (label kept, wrong math)
```

## 4. How the dataset changed from start to finish — and why

**Start: raw Kaggle Eedi CSV** → 1,869 rows → filter to "Number" strand + all-3-distractors-labeled
→ **281 usable real MCQs** → split into 140 eval (frozen) + 141 real train seed. Two hard limits
shaped everything: (a) real data is thin (~141 rows), so we need synthetic; (b) the Kaggle release
has NO student-pick-rate data (so plausibility can't be directly measured — documented limitation).

**The synthetic engine** (`src/buggy_procedures.py`): each misconception is an executable "bug"
(Brown & Burton, 1978) that computes the exact wrong answer for a question. This gives volume with
**guaranteed** consistency.

| Step | Change | Why |
|---|---|---|
| **v1** | 141 real (incl. ~62 duplicate-label) + 1200 synth | First real run. Best alignment — but the duplicate-label reals taught label-repetition (distinct-misconceptions collapsed to 30.7% at inference) and consistency was only 49.8%. |
| **v2** | dropped duplicate-label reals (→79), rebalanced synth | Fix the label repetition. Worked (distinct labels ✓) but halving real signal + diluting eval-matching families **lost alignment**. Over-corrected. |
| **v3** | distinct reals ×2 (oversample) + revert to v1-style synth | Best-of-both: keep the label fix, restore alignment by oversampling the clean reals. |
| **v4** | add a `computation` (show-the-work) to EVERY distractor | Attack consistency directly: supervise the arithmetic, and make consistency programmatically checkable. Consistency barely moved (53.5%) — revealed the model was *gaming* a weak metric. |
| **v5** | engine 8→16 families (cover eval topics), de-skew 50:1→1.4:1, harden the check | Root cause was a COVERAGE gap (62% of eval topics unseen) + a gameable metric. Fixed both. Consistency still ~50% → proved it's not a coverage problem. |
| **v6** | add 800 DPO preference pairs on top of v5 SFT | The gap is the misconception→arithmetic BINDING. DPO trains "prefer the correct binding." Improved structure (distinct_answers 71→80) but consistency stayed ~50% — a 1.7B capacity ceiling (matches 7B LookAlike's 51.6%). |

**The throughline:** each version fixed exactly one diagnosed failure of the previous, changing the
DATA (not hyperparameters). v1→v3 fixed label repetition and alignment balance; v4→v5 attacked
consistency via show-the-work + coverage + an honest metric; v6 tried preference tuning. The
consistency ceiling (~50%) held across four data strategies — a real finding, not a data bug.

## 5. How to read the raw data yourself
```bash
# whole file, pretty:
python -c "import json;[print(json.dumps(json.loads(l),indent=2)) for l in open('data/processed/train_v5.jsonl')]" | less
# just the targets:
python -c "import json;[print(json.loads(l)['assistant']) for l in open('data/processed/train_v5.jsonl')]" | less
# count / inspect DPO pairs:
python -m src.dpo_pairs --n 5 --preview
```
