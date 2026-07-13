# v8 Benchmark Protocol — Pre-registered Before Opus/v8 Evaluation

## Scope and claim

The claim is narrow: generation of three diagnostic distractors for Eedi-style middle-school **Number** questions under this repository's schema. It is not a claim that an 8B model is generally better than Claude Opus.

The legacy 140-item set has guided v1–v7.1 development, so it is now a frozen **development/continuity** set, not an unbiased final test. Final v8 model selection cannot use `data/processed/eval_v8_frozen.jsonl`; that new 140-question set was deterministically selected from the 270 previously unused Eedi Number questions before v8 training.

## Primary metric: Good Distractor Rate (GDR)

The denominator is three expected distractor slots per question. Missing slots fail. Extra slots are also included and cannot improve the rate.

A pair is good only when every applicable gate passes:

1. answer is nonempty and differs from the correct key;
2. answer is unique within the output set;
3. misconception is nonempty/specific and distinct within the set;
4. for numeric/applicable items, computation parses, is question-grounded, and evaluates exactly to the answer;
5. the named misconception resolves to a supported executable procedure when a deterministic registry mapping is available;
6. misconception specificity, student plausibility, and diagnostic usefulness pass the strict rubric threshold only when an independent calibrated judge is available and applied identically to both systems.

Gate 6 is an **independent expert-model proxy**, never observed student frequency. The available 2024 Kaggle files have no response counts. Without an accepted independent calibration, gates 5–6 and therefore GDR remain unavailable rather than being inferred.

Report GDR as `good pairs / expected-or-emitted pairs`, percentage, and a question-cluster bootstrap 95% interval.

## Good@3

`Good@3` is the percentage of questions with exactly three distractors and all three passing GDR. Report `questions with three good / all questions`, percentage, and Wilson 95% interval.

## Small headline set

1. **GDR** — primary all-gates pair quality.
2. **Good@3** — complete usable-question yield.
3. **Numeric misconception→answer binding consistency** — deterministic programmatic result or accepted independent-judge result, reported separately from nonnumeric.
4. **Diagnostic-quality proxy pass rate** — strict independent expert-model rubric when calibrated; explicitly not observed pick likelihood.
5. **Selective GDR at ≥80% coverage**, plus ECE and Brier for the numeric binding-confidence calibration.
6. **Hard gates:** valid exactly-three JSON, key safety, distinct answers, distinct misconceptions, and hardened computation validity.

Exact/Partial/Proportional@3 remain diagnostics in `src.eval`; they are excluded from the headline because reproducing one historical option set penalizes different valid distractors.

## Win rules

Both must hold:

- absolute v8 GDR is at least **90%**; and
- for every selected bounded higher-is-better quality rate, v8 achieves at least **40% relative error-rate reduction** versus Opus:

`RER = ((100 - Opus) - (100 - v8)) / (100 - Opus)`.

Example: Opus `95`, v8 `97` gives `(5 - 3) / 5 = 40%`. If Opus is `100`, RER is undefined and only the absolute delta/interval is reported. For ECE, Brier, latency, cost, size, and other lower-is-better quantities, ordinary relative reduction applies.

Every comparison reports both absolute score delta and a paired question-bootstrap 95% interval. A point estimate above target with an interval crossing zero is not described as a demonstrated win.

## Tracks

- **Model-only:** deterministic greedy 8B output. This is the model claim.
- **Verifier-guided best-of-N:** four candidates with deterministic local selection. This is a system/inference-time claim and must never be presented as model-only performance.
- **Opus:** same prompt/schema and frozen questions.

## Judge and confidence controls

- The pre-registered Opus judge failed its 80% agreement/10% false-positive gate and is not used. Its rejected artifact remains evidence, not a confidence source.
- Opus is the teacher/frontier generator only. It must not judge either v8 or its own output.
- An independent calibrated judge may be used only after clearing documented scope-specific thresholds and only when applied identically—same model, prompt, thresholds, and cache policy—to both systems. Plausibility and diagnostic-quality results remain explicitly labeled proxies.
- Numeric confidence may be attached only from an accepted numeric/programmatic calibration artifact. Confidence is not an SFT target or model self-report.
- Nonnumeric and mixed-answer confidence remain `null/not_calibrated` until a separate independent calibration clears its own gate; numeric calibration cannot be reused across scopes.

## One-shot stopping rule

The planned training budget is one 8B SFT run with checkpoint selection on a separate grouped validation split. Final frozen results are reported whether targets pass or fail. A second training is allowed only for a serious technical failure such as corrupted inputs, failed checkpoint save, or an incorrectly executed recipe—not routine metric chasing.
