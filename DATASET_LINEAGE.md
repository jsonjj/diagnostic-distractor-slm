# Dataset Lineage — From Kaggle Eedi to train_v5

> How the training dataset for the diagnostic-distractor SLM was built: what we started
> with, every transformation and *why*, how each version was tested, what each version
> taught us, and how those lessons produced the current v5 dataset.

_Task: given a "Number" MCQ + its correct answer, output exactly 3 diagnostic distractors —
each a wrong answer tagged to a named student misconception and **numerically consistent**
with it (the answer equals what a student making that mistake would actually compute).
Consistency is the thesis; it is the gap prior work (DiVERT/Feng) left open._

---

## 1. The raw source (Kaggle Eedi "Number" competition)

Everything derives from the Kaggle Eedi CSVs in `data/raw/`:
- `train.csv` — **1,869 rows**. Columns: `QuestionId, ConstructName, SubjectName, CorrectAnswer,
  QuestionText, Answer{A,B,C,D}Text, Misconception{A,B,C,D}Id`.
- `misconception_mapping.csv` — maps each `MisconceptionId` → human-readable misconception name.

**What we can actually use.** We restrict to the "Number" strand (the 34 vetted subjects in
`src/config.py::NUMBER_SUBJECTS`) and to questions where **all three wrong options are labeled
with a misconception** (a distractor with no misconception can't teach the task). Filtering:

| Filter | Rows |
|---|---|
| Raw `train.csv` | 1,869 |
| "Number" strand | 551 |
| Number **and** all-3-distractors labeled | **281** |

`src/data_prep.py` performs this, then splits the 281 into a **140-item held-out eval set**
(`eval_heldout.jsonl`, never trained on) and a **141-record real train seed**
(`real_train_seed.jsonl`). The eval set is frozen across every version so all numbers compare.

**The two hard limits of the raw data — both shaped every decision below:**
1. **The real signal is thin.** Only 281 usable rows total; ~141 for training. That is far too
   little to fine-tune on alone → we need **synthetic data**.
2. **No student-pick data.** This Kaggle release has *no* column for the proportion of students
   who chose each option. (Feng's paper had it; it lives in Eedi's private repository.) So we
   cannot directly measure *plausibility* ("would a real student pick this") — a documented
   limitation, logged as future work.

---

## 2. The synthetic engine (`src/buggy_procedures.py`) — why it exists

To get volume with **guaranteed** consistency, we built a "buggy-procedure" engine grounded in
Brown & Burton (1978): student errors are systematic, executable "bugs." Each misconception is a
small program: given a question's operands it computes the *exact* wrong answer that error
produces. Because the answer is computed from the misconception, **synthetic consistency is
100% by construction** — the label↔value binding is true by definition.

Each misconception carries two functions:
- `apply(operands) → Fraction` — the exact wrong value.
- `comp(operands) → "arithmetic string"` — the show-the-work (added in v4), which must evaluate
  to `apply()`.

This engine is the backbone of every version; the versions differ mainly in *how much* real vs
synthetic, *how balanced* the synthetic is, and *whether* targets show their work.

---

## 3. The version-by-version journey (what we learned, and why we changed)

| Version | Composition | Status | The lesson it taught |
|---|---|---|---|
| **v1** | 141 real (incl. ~62 duplicate-label) + 1,200 synth | trained, shipped | Best alignment; but **repeats one misconception label** and **fails consistency** |
| **v2** | 79 distinct-label reals + 1,362 balanced synth | trained | Fixed labels **but lost alignment** — over-corrected |
| **v3** | 79 distinct reals ×2 + 1,200 v1-style synth | built, not trained | Best-of-both on paper (kept for lineage) |
| **v4** | 46 verified reals ×3 + 1,200 synth **with computation** | trained | Show-the-work **didn't close the gap**; the free metric was **gameable** |
| **v5** | ~60+ hardened reals ×3 + ~1,300 **balanced 16-family** synth w/ computation | building | Coverage is the real lever; metric hardened to be honest |

### v1 — first real run (shipped)
1,341 rows. Decisively beat the base model and rivaled Sonnet on **alignment** (Proportional@3
31.9 vs 39.3). **Two failures surfaced:**
- `distinct_misconceptions` collapsed to **30.7%** — ~62 real records had the *same* misconception
  on two options, teaching "real-style question → repeat a label."
- Judged **consistency only 49.8%** (vs Sonnet 94.7%). The core thesis was unmet.

**Lesson:** duplicate-label reals poison the distinctness requirement, and — more importantly —
the target never *showed the arithmetic*, so the model never learned the label↔value binding.

### v2 — kill the label repetition
Dropped duplicate-label reals (keeping 79 distinct) and rebalanced the synthetic families.
`distinct_misconceptions` jumped to **88.6%** — but **alignment fell** (Prop@3 25.0). Halving the
real signal and diluting the eval-matching families cost too much.

**Lesson:** you can't fix distinctness by simply throwing away real data; the real signal is
precious and the synthetic mix must still resemble the eval.

### v3 — best-of-both (built, not trained)
Keep only distinct-label reals **but oversample them ×2**, and revert to v1's eval-matching
synthetic. On paper: distinct labels *and* recovered alignment. Retained for lineage.

**Lesson:** oversampling a small, clean real set restores its weight without re-introducing the
bad records.

### v4 — show the work (trained; the pivotal negative result)
Added a `computation` field to **every** distractor (`{misconception, computation, answer}`, e.g.
`0.4 ÷ 0.2 = 2`) to directly supervise the binding, and made consistency **programmatically
checkable for free**. Composition: 46 teacher-verified reals ×3 + 1,200 synth.

**What happened — the make-or-break test:**
- Free `computation_consistency`: **84.6%** (looked like a huge win).
- API judge (the truth): **53.5%** — barely above v1's 49.8%. **The gap did not close.**

**Why the 31-point discrepancy — this is the key learning of the whole project:**
The model learned the *format*, not the *binding*. It emitted self-consistent arithmetic that
**games the free metric**:
- **Degenerate computations** like `6 = 6` (5.6% of outputs) — a number equals itself, so the
  free check passes, but it encodes no misconception.
- **Fabricated arithmetic** like `2÷4 = 0.5` for the question "0.2 ÷ 0.4" — internally consistent,
  but not what the stated misconception computes for *this* question.

**Root cause (measured, not guessed): a COVERAGE gap.** The engine taught **8 question families /
39 misconceptions**; the eval spans **33 topics / 217 unique misconception labels** with ~0%
label overlap — **~87 of 140 eval items (62%) had zero engine coverage.** On any topic it never
learned to *compute* (Place Value, Rounding, Indices, Factors/HCF, conversions, mental
arithmetic…), the model fell back on plausible-looking fabrication.

### v5 — attack the coverage gap + make the metric honest (current)
Two coordinated changes:
1. **Coverage.** Expanded the engine from **8 → 16 families** and **39 → 66 misconceptions**,
   adding the eval's biggest uncovered topics: Place Value, Rounding (DP), Laws of Indices,
   Factors/HCF, decimal add/sub, decimal↔percentage conversion, mental addition/subtraction,
   mental multiplication. Now the model learns to *compute* the misconceptions it is tested on.
2. **De-skew + honest metric.** Synthetic is sampled **balanced** — from ~50:1 down to **≤1.5:1**
   across families — with a computation on every distractor. And the free metric was **hardened**
   so it can no longer be gamed (see §4). Real seed = distinct-label reals whose teacher-generated
   computations pass the *hardened* check, oversampled ×3.

**Lesson driving v5:** the consistency gap was never a "small models can't do this" problem — it
was "we never taught the model the topics it's tested on," compounded by a metric that hid the
failure. Fix coverage; make the metric tell the truth.

---

## 4. How we hardened the consistency metric (so our own headline is honest)

The v4 free check (`computation_consistent`) only verified that the arithmetic *left of the `=`*
evaluated to the answer — nothing tied it to a real misconception or the question. Two gates were
added (in `src/consistency.py`), active whenever a `question` is supplied (legacy callers with no
question keep the old behavior, so v1–v4 builds stay byte-identical):

- **B1 — operator gate:** the computation must contain a real binary operator. Kills `6 = 6` and
  bare-number tautologies (they now return "not a computation").
- **B2 — question anchoring:** at least one number in the computation must come from the question.
  Kills wholly-fabricated arithmetic (e.g. `100 × 5 = 500` for "0.2 ÷ 0.4"). It is deliberately a
  *loose* anchor rather than "every number must be in the question," because legitimate error
  arithmetic introduces derived offsets (e.g. `875599 + 24401 = 900000` for a rounding error) —
  requiring all-leaves-grounded falsely rejected real, correct computations and destroyed real
  yield (0/2 in testing). The anchor version restored it to 80% (4/5).

**Impact — the honesty proof.** Re-scoring v4's own predictions under the hardened metric:
`84.6% (inflated) → ~72.6% (anchored)`, much closer to the judge's `53.5%` truth. The free metric
is now a cheap, honest screen; the LLM judge remains the ground-truth backstop for the residual
digit-reuse cases the anchor can't catch.

**A stronger judge, too.** For v5 we added a **solve-first** consistency judge (`--judge2`): instead
of a one-shot YES/NO, it reconstructs the correct solution, injects the stated misconception at the
step it corrupts, computes the resulting value, and rules VALID only if that value equals the
student's answer. (Grading an objective property with an LLM is fair — the thesis is about
*generation*, not grading.)

---

## 5. How we test the dataset (every build is verification-gated, offline, before any GPU)

The build (`src/generate.py --v5`) refuses to be trusted without passing these, all runnable with
no GPU and no API:

1. **Engine self-check** (`python -m src.consistency`): 300 synthetic examples, each distractor
   recomputed from its misconception AND its show-the-work re-evaluated under the **hardened**
   check → must be **300/300**. (Confirmed.)
2. **Metric hardening assertions** (in `python -m src.eval` self-validate): `6 = 6` → rejected;
   ungrounded arithmetic → rejected; a genuine grounded computation → accepted; legacy (no
   question) behavior unchanged → all PASS. (Confirmed.)
3. **Structural guarantees on every training row** (`--v5` verification block): valid JSON,
   exactly 3 distractors, 3 distinct misconceptions, 3 distinct answers, no empty fields, and
   **none equal to the correct answer** — the well-formedness the eval scores (`none_equals_key`,
   `distinct_answers`), enforced at build time.
4. **Synthetic consistency = 100%** under the hardened check (v5 synth: **3,882/3,882 pairs**,
   1,294 examples). Real consistency = 100% by the verify-filter (a real record is kept only if
   all 3 computations pass).
5. **Family balance ≤ 3:1** (v5: **1.43:1**, down from v4's ~50:1) — printed in the build report.
6. **Backward-compatibility:** rebuilding v2 and v4 reproduces the committed files **byte-for-byte**
   (proving the engine expansion didn't disturb legacy). `generate()` is pinned to the original 8
   families (`LEGACY_FAMILIES`) exactly for this. (v1/v3 were already non-reproducible on pristine
   HEAD — a pre-existing property from an older engine, unchanged by v5.)
7. **Zero train↔eval leakage** — the 140-item eval hold-out is split off once in `data_prep` and
   never enters any training file.

Only after all of the above passes is `train_v5.jsonl` considered ready for the GPU.

---

## 6. Where we are now

- **v5 dataset:** ~1,294 balanced 16-family synthetic examples (100% hardened-consistent) + the
  hardened-verified real seed (distinct-label Eedi reals, oversampled ×3), real weight ~10%.
- **Coverage:** engine now spans the eval's high-count topics that v4 entirely missed.
- **Metric:** hardened to be un-gameable; free number now tracks the judge instead of inflating
  ~30 points above it.
- **Next:** train v5 on GPU (notebook `TRAIN_FILE` already set to `train_v5.jsonl`), then measure
  judged consistency vs v4's 53.5% and Sonnet's 94.7% — the make-or-break test for whether the
  coverage fix closes the gap.

---

## 7. One-line summary of the arc

Kaggle gave us **281 usable rows and no student-pick data** → we built a **guaranteed-consistent
synthetic engine** to get volume → **v1** revealed label-repetition + a consistency gap → **v2/v3**
fixed labels and tuned the real/synthetic balance → **v4** added show-the-work but proved the model
was **gaming a weak metric** because it lacked **coverage** of the eval's topics → **v5** expands the
engine to 16 families covering those topics, de-skews the mix, and **hardens the metric** so the
number we report is the number that's true.
