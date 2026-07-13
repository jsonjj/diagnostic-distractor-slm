# v8 Primary Benchmark — Pre-registered Plan

`NOT YET RUN` is intentional. No Opus or v8 score is inferred or fabricated.

## Primary quality metrics

| Metric | Base 4B | v7.1 4B | Sonnet 5 legacy | Opus 4.8 | v8 8B model-only |
|---|---:|---:|---:|---:|---:|
| **Good Distractor Rate (GDR)** | NOT RUN | NOT RUN | NOT RUN | **NOT YET RUN** | **NOT YET RUN** |
| **Good@3** | NOT RUN | NOT RUN | NOT RUN | **NOT YET RUN** | **NOT YET RUN** |
| Numeric misconception→answer consistency | ~0% legacy estimate | 60.4% | 95.8% | **NOT YET RUN** | **NOT YET RUN** |
| Strict diagnostic-quality proxy pass | NOT RUN | NOT RUN | NOT RUN | **NOT YET RUN** | **NOT YET RUN** |
| Selective GDR at ≥80% coverage | NOT RUN | NOT RUN | NOT RUN | **NOT YET RUN** | **NOT YET RUN** |
| Numeric binding confidence ECE / Brier | NOT RUN | NOT RUN | NOT RUN | **NOT YET RUN** | **NOT YET RUN** |

GDR/Good@3 require complete per-pair binding and strict quality-proxy verdicts, which do not exist for legacy files. The Sonnet numeric value is a historical one-shot-judge result, not a currently accepted v8 calibration artifact or a GDR score. No task-quality or observed-frequency claim is available unless an independent judge is calibrated and then applied identically to both final systems.

## Hard safety/spec gates

| Metric | Base 4B | v7.1 4B | Sonnet 5 legacy | Opus 4.8 | v8 8B model-only |
|---|---:|---:|---:|---:|---:|
| Valid exactly-3 output | 96.4% | 98.6% | 95.0% | **NOT YET RUN** | **NOT YET RUN** |
| No answer equals key | 58.6% | 79.3% | 95.0% | **NOT YET RUN** | **NOT YET RUN** |
| Three distinct answers | 70.0% | 84.3% | 94.3% | **NOT YET RUN** | **NOT YET RUN** |
| Three distinct misconceptions | 91.4% | 94.3% | 95.0% | **NOT YET RUN** | **NOT YET RUN** |
| Hardened computation validity | n/a (old schema) | 75.0% (315/420 expected slots) | n/a (old schema) | **NOT YET RUN** | **NOT YET RUN** |

These are v8-strict local gates: a wrong-count/missing-field item fails each set-level gate. This is intentionally stricter than `TABLE.md`'s legacy independent rates. The legacy Sonnet output predates the computation schema; a new Opus run uses the exact v8 prompt and is the fair frontier comparison. Exact/Partial/Proportional@3 are intentionally absent from the headline.

## Win rule

v8 must reach absolute **GDR ≥90%** and at least **40% relative error-rate reduction** versus Opus on every selected bounded quality metric:

`((100 - Opus) - (100 - v8)) / (100 - Opus) >= 0.40`.

The final table also reports absolute deltas and paired bootstrap 95% intervals. No win is claimed from point estimates alone.

## Opus access preflight

Access is now verified for `anthropic-primary/claude-opus-4-8`: exact owner-shaped
streaming, non-streaming, and repository-client probes all returned `OK` at an
8-token cap. The earlier 403 used a different stale `TFY_API_KEY` inherited by
the Cursor process; `python-dotenv` was absent and config silently skipped `.env`,
then default dotenv precedence preserved the stale process value. Config now
fails closed if `.env` cannot be loaded and gives non-empty project `.env`
values precedence. `data/processed/v8_manifest.json` records
`opus_access_ready: true`.

## Role-separated pre-training preparation

The pre-registered Opus judge calibration remains rejected and unchanged:
62/80 correct (77.5% agreement), TP/TN/FP/FN = 28/34/6/12, 15% false-positive
rate, and 30% false-negative rate. Neither threshold was lowered or
reinterpreted. The manifest therefore correctly records
`opus_judge_ready: false`.

Opus now has only the teacher/frontier-generator role. The 130 teacher
generations were admitted through `deterministic_teacher_filter`, which uses
exactly-three/schema, no-key collision, answer and misconception distinctness,
hardened arithmetic evaluation, question grounding, exact alias resolution to
the audited `wayline-procedures-v1` registry, deduplication, and both leakage
boundaries. It makes no Opus self-judge call and no student-frequency or
plausibility claim.

The survivor floor was fixed at 20 before generation. The executed result is
32/130 survivors and 98 rejections:

- 76 failed hardened computation/grounding;
- 11 lacked a supported procedure mapping;
- 10 had invalid/duplicate answer shape;
- 1 collided with the answer key.

The preparation used 80 completed numeric-calibration tasks plus 130 teacher
generation tasks: 210/612 task calls and a configured output ceiling of
76,160/124,400 tokens. Actual provider output-token usage was not retained.
The rejected 390-call Opus self-judgment stage was skipped, saving its full
46,800-token ceiling. The 12-call nonnumeric arm was also not run because mixed
confidence is outside this recovery route.

```bash
.venv/bin/python -m src.run_frontier \
  --input data/processed/v8_teacher_pool.jsonl \
  --model anthropic-primary/claude-opus-4-8 \
  --out data/processed/v8_teacher_predictions_opus.jsonl \
  --deterministic-teacher --estimate-only

.venv/bin/python -m src.run_frontier \
  --input data/processed/v8_teacher_pool.jsonl \
  --model anthropic-primary/claude-opus-4-8 \
  --out data/processed/v8_teacher_predictions_opus.jsonl \
  --deterministic-teacher --max-calls 130

.venv/bin/python -m src.v8_teacher \
  --predictions data/processed/v8_teacher_predictions_opus.jsonl \
  --minimum-survivors 20

.venv/bin/python -m src.v8_data --require-deterministic-teacher
.venv/bin/python -m src.v8_data --verify-only
```

`training_ready: true` now means exact Opus access plus a ready
`deterministic_teacher_filter` artifact; it does not imply an accepted Opus
judge. Confidence is unavailable as a probability here. Any future confidence
artifact must remain numeric/programmatic in scope; mixed/nonnumeric confidence
stays unavailable until independently calibrated.

## Final evaluation role separation

After the one-shot Colab run, Opus may generate the 140-item frontier baseline,
but must not judge either system. No accepted independent v8 judge artifact
currently exists. Until one is calibrated, report only deterministic metrics
and leave GDR, strict task-quality proxy, and confidence cells unavailable. If
an independent judge such as Sonnet later clears a documented calibration, use
the identical model, prompt, thresholds, and cache policy for both v8 and Opus,
label all plausibility/diagnostic-quality results as proxy judgments, and
estimate that separately before any paid call.

The next one-shot action is to open
`notebooks/train_qwen3_distractor_v8.ipynb` on one L4 or A100 Colab runtime,
preload this reviewed repository state (or deliberately publish it), optionally
set `HF_TOKEN`, and use **Run All once** without interruption.
