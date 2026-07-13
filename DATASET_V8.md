# v8 Dataset and Student-Signal Audit

## What real student signal exists

The local Kaggle `train.csv` columns are:

`QuestionId, ConstructId, ConstructName, SubjectId, SubjectName, CorrectAnswer, QuestionText, AnswerAText..AnswerDText, MisconceptionAId..MisconceptionDId`.

`misconception_mapping.csv` maps IDs to expert descriptions. There are no response counts, selected-option counts, proportions, student IDs, timestamps, or confidence fields. The wrong options and labels are real Eedi assessment content, but this release does **not** establish how often students selected each option. v8 therefore never calls a distractor “frequently selected.”

The public [Eedi NeurIPS 2020 Education Challenge](https://www.eedischool.com/projects/neurips-education-challenge) data (public mirror: `https://dqanonymousdata.blob.core.windows.net/neurips-public/data.zip`) does contain millions of `QuestionId, UserId, AnswerValue, CorrectAnswer, IsCorrect` response records and answer confidence metadata. Its IDs were independently anonymized, its question sets are different, and no verified join to the 2024 misconception questions/options is available. It can study generic response behavior, but cannot supply pick rates for this benchmark. Its CC BY-NC-ND 4.0 terms also need owner review before any redistribution or derived-data use.

The ideal owner-provided aggregate would be:

`QuestionId, OptionLabel, SelectionCount, TotalResponses`

with one A–D row per option, a documented population/time window, and at least 30 responses per question. A future weighting rule should use a smoothed wrong-option rate `(SelectionCount + 1) / (TotalResponses + 4)`, cap any one question's total weight, and preserve a minimum topic floor. No such artifact exists now.

## Frozen split

- 281 fully misconception-labeled Number questions remain the legacy 141-train/140-development lineage.
- 270 additional Number questions were never in legacy training or evaluation.
- Stable hash seed `808` partitions those 270 into:
  - 130 real-question Opus teacher candidates;
  - 140 new frozen final-benchmark questions.

The split is made before teacher generation/training. ID and normalized-question fingerprints are checked against both frozen sets.

## Current role-separated final build

`python -m src.v8_data` currently produces:

- 1,200 new targeted synthetic rows across 12 previously weak families;
- 2,060 balanced legacy-engine rows across 15 families;
- 46 legacy hardened real Eedi targets plus 32 deterministic-filter Opus
  teacher targets, repeated four times (312 rows);
- 3,572 total training rows;
- 130 teacher-pool rows;
- 140 frozen benchmark rows;
- 10,716/10,716 hardened-consistent target pairs.

The 27 synthetic families have max/min unique-row ratio `180/63 = 2.86×`; each of the 12 targeted families contributes exactly 100 rows. Topic distributions for both reserved real splits are recorded in the manifest.

Exact counts and SHA-256 values live in `data/processed/v8_manifest.json`.
The final manifest says `opus_access_ready: true`,
`opus_judge_ready: false`, `deterministic_teacher_filter_ready: true`,
`opus_teacher_records: 32`, and `training_ready: true`. The rejected numeric
Opus judge artifact remains unchanged at 77.5% agreement and 15% FPR; it was
not used to admit teacher data or attach confidence.

The targeted families cover observed legacy-development weaknesses without copying development questions: Equivalent Fractions, full fraction division, fraction→decimal, fraction→percentage, LCM, mixed→improper fractions, negative multiplication, percentage change, significant-figure rounding, simplifying fractions, square roots, and standard form.

## Teacher data gate

The one-shot training build is ready only because all of these gates passed:

1. Opus generated exactly 130 teacher-pool candidates as teacher/frontier
   generator, not judge;
2. `deterministic_teacher_filter` required exact structure/count, key safety,
   distinct answers and misconceptions, hardened arithmetic evaluation,
   question grounding, and exact aliases to three distinct procedures in the
   audited `wayline-procedures-v1` registry;
3. deduplication and both frozen leakage boundaries passed;
4. 32 survivors exceeded the predeclared floor of 20;
5. `python -m src.v8_data --require-deterministic-teacher` rebuilt and hashed
   `train_v8.jsonl`.

The 98 record-level rejections were: computation/grounding 76, unsupported
procedure mapping 11, answer shape 10, and key collision 1. No Opus
self-judgment, plausibility proxy, or observed-student-frequency claim was used.

Opus-generated rows use real Eedi questions but synthetic teacher answers. They are not student responses.

## Confidence target

Confidence is deliberately absent from SFT targets. A generated decimal would
be an uncalibrated self-report. The deterministic filter emits binary acceptance
evidence, not probabilities. Any future confidence artifact is limited to
numeric/programmatic scope and must identify an accepted independent
calibration. Mixed/nonnumeric and item/all-three confidence remain
`null/not_calibrated`.
