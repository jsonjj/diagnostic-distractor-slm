# Diagnostic Distractor v8 Implementation Plan

> **Superseded judge path (2026-07-12):** Opus failed the pre-registered judge
> calibration and is now teacher/frontier generator only. Follow
> `2026-07-12-v8-role-separated-recovery.md` for the executed recovery.

> **For agentic workers:** Execute this plan task-by-task with test-first changes. Do not commit or push.

**Goal:** Make one high-confidence 8B training run for a sixth-grade/middle-school Number distractor model, evaluated primarily by Good Distractor Rate (GDR) and Good@3 against Claude Opus 4.8 with externally calibrated confidence and honest Eedi provenance.

**Architecture:** Finish Opus teacher generation, judge calibration, data QA, balancing, targeted synthesis, and leakage checks before Colab. Keep raw generation (`misconception`, `computation`, `answer`) separate from post-hoc confidence, train Qwen3-8B once with grouped validation and automatic checkpoint selection, then evaluate model-only and verifier-guided tracks on a sealed benchmark with GDR/Good@3 and paired confidence intervals.

**Tech Stack:** Python 3, standard library exact arithmetic, pytest/unittest, JSONL, Qwen3 QLoRA through Unsloth/Colab, and the OpenAI-compatible TrueFoundry gateway.

## Global Constraints

- Never use, print, store, or commit the token pasted into chat; treat the current local `TFY_API_KEY` as compromised until the owner rotates it.
- Make no further TrueFoundry calls before rotation is confirmed.
- Use `https://tfy-eu.promptlens.trilogy.com` and configurable role models; the requested Opus deployment is `anthropic-primary/claude-opus-4-8`.
- Preserve the Sonnet baseline and legacy `TFY_MODEL` behavior.
- Do not train on either the legacy 140-item development holdout or the new sealed v8 benchmark.
- Do not describe expert-written Eedi options as frequently selected without response counts.
- Do not emit invented confidence decimals. Missing calibration must be represented as `probability: null`, `level: "not_calibrated"`.
- Pre-register the quality target as at least 40% relative error-rate reduction versus Opus, with absolute score deltas and paired confidence intervals.
- GDR is the primary pair metric, Good@3 the primary complete-item metric, and absolute GDR target is at least 90%.
- The planned training budget is one run; a second is contingency-only for a demonstrated technical execution failure.
- Do not modify Unity/game files, commit, push, or run a large paid API sweep.

---

### Task 1: Confidence contract and calibration metrics

**Files:**
- Create: `src/confidence.py`
- Modify: `src/prompts.py`
- Create: `tests/test_v8_confidence.py`

**Interfaces:**
- `confidence_payload(probability, *, target, source, calibration_id=None) -> dict`
- `ensure_confidence_schema(prediction: dict) -> dict`
- `confidence_metrics(labels, probabilities, *, bins=10, thresholds=(0.5, 0.7, 0.8, 0.9, 0.95)) -> dict`
- `parse_distractors` continues reading legacy v1-v7 outputs and preserves a valid optional `confidence` object.

- [ ] Write tests proving legacy outputs parse unchanged, missing calibration produces `null/not_calibrated`, invalid or arbitrary probability values are rejected, and ECE/Brier/selective accuracy are numerically correct.
- [ ] Run the tests and confirm they fail because the confidence module/schema does not exist.
- [ ] Implement the minimal post-hoc confidence schema and metrics.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Role-specific TrueFoundry configuration

**Files:**
- Modify: `src/config.py`
- Modify: `src/tfy_client.py`
- Modify: `src/real_computations.py`
- Modify: `src/eval.py`
- Modify: `src/run_frontier.py`
- Create: `tests/test_tfy_model_roles.py`

**Interfaces:**
- `TFY_TEACHER_MODEL`, `TFY_JUDGE_MODEL`, and `TFY_FRONTIER_MODEL` fall back to legacy `TFY_MODEL`.
- `TFY_OPUS_MODEL` defaults to `anthropic-primary/claude-opus-4-8`.
- Teacher calls pass `TFY_TEACHER_MODEL`; judge/rubric calls pass `TFY_JUDGE_MODEL`; frontier generation passes `TFY_FRONTIER_MODEL`.
- `run_frontier --model MODEL_ID` provides a reproducible CLI override and records the model ID in every prediction row.

- [ ] Write tests with an injected chat function proving each role uses the requested model and the Sonnet legacy fallback remains available.
- [ ] Run the tests and confirm they fail under the single-model configuration.
- [ ] Add role-specific settings and explicit model provenance without changing secrets.
- [ ] Run the focused tests and confirm they pass. Do not call the gateway.

### Task 3: Frozen v8 split and deterministic training data

**Files:**
- Create: `src/v8_data.py`
- Create: `tests/test_v8_data.py`
- Generate: `data/processed/v8_teacher_pool.jsonl`
- Generate: `data/processed/eval_v8_frozen.jsonl`
- Generate: `data/processed/synth_train_v8.jsonl`
- Generate: `data/processed/train_v8.jsonl`
- Create: `DATASET_V8.md`

**Interfaces:**
- `stable_partition_unused(rows, used_ids, *, teacher_n, benchmark_n, seed) -> tuple[list, list]`
- `question_fingerprint(question) -> str`
- `assert_no_leakage(train_records, benchmark_records) -> None`
- `build_v8(...) -> dict` writes deterministic artifacts and a SHA-256 manifest.

- [ ] Write fixture-based tests proving stable disjoint partitioning, ID/text leakage rejection, deterministic hashes, exactly-three targets, distinct answers/misconceptions, no key collision, and 100% hardened computation verification.
- [ ] Run the tests and confirm they fail because the v8 builder does not exist.
- [ ] Implement a deterministic builder that treats the legacy 140 as a development set, partitions only previously unused Number questions, reuses verified distinct-label Eedi training rows, and adds verifier-gated real-format synthetic examples.
- [ ] Generate artifacts from the local Kaggle files and record exact counts/hashes.
- [ ] Run the focused tests and confirm they pass.

### Task 4: Error analysis and important benchmark

**Files:**
- Create: `src/error_analysis_v8.py`
- Create: `src/benchmark_v8.py`
- Create: `tests/test_v8_benchmark.py`
- Create: `docs/V8_BENCHMARK_PROTOCOL.md`
- Create: `TABLE_V8_PLAN.md`

**Interfaces:**
- `analyze_predictions(gold, predictions, training_labels=()) -> dict`
- `primary_metrics(gold, predictions, verdicts=None) -> dict`
- `paired_bootstrap(gold, systems, metric_fn, *, samples=2000, seed=...) -> dict`
- `relative_error_reduction(candidate_score, baseline_score, *, ceiling=100.0) -> float | None`
- CLI accepts repeated `--system NAME=predictions.jsonl` and optional `--verdicts NAME=verdicts.jsonl`, then writes JSON and a concise Markdown table.

- [ ] Write tests for all six GDR gates, Good@3, collisions, duplicates, missing computations, numeric/non-numeric splits, exact-unseen label diagnostics, deterministic bootstrap intervals, absolute deltas, and the 40% relative error-rate-reduction formula.
- [ ] Run the tests and confirm they fail because the benchmark modules do not exist.
- [ ] Implement local metrics and error clusters without pretending computation validity proves misconception binding.
- [ ] Run analysis on base, v7.1, and Sonnet artifacts; clearly mark Opus/v8/confidence/human-quality results `NOT YET RUN`.
- [ ] Pre-register primary metrics, confidence intervals, multiple-comparison handling, model-only versus verifier-selected tracks, and the exact win rule before running Opus.

### Task 5: Reproducible v8 Colab workflow

**Files:**
- Create: `notebooks/train_qwen3_distractor_v8.ipynb`
- Create: `docs/V8_RUNBOOK.md`

**Interfaces:**
- Notebook defaults to the single planned 8B Qwen3 QLoRA run, consumes `train_v8.jsonl`, selects among epoch checkpoints using only a grouped validation split, verifies frozen-set hashes before training, saves raw model-only predictions, and runs verifier-guided best-of-N into a separate artifact.
- The notebook never fits confidence on the final benchmark and never calls TrueFoundry unless the owner explicitly supplies a newly rotated key.

- [ ] Build a compact notebook with environment setup, immutable data checks, base litmus, QLoRA training, model-only inference, optional best-of-N inference, artifact download, and no automatic publication.
- [ ] Document exact Colab clicks/commands, expected artifact names, GPU/memory tradeoffs, rotated-key setup, Sonnet reproduction, Opus generation/judging, confidence fitting, and final table generation.
- [ ] Add a notebook structure test so missing safety/hash/eval cells fail locally.

### Task 6: Verification and handoff

**Files:**
- Review all files above plus the existing working tree.

- [ ] Run all new focused tests.
- [ ] Run the full relevant Python test suite.
- [ ] Rebuild v8 artifacts twice and compare hashes.
- [ ] Run local v8 benchmark/error-analysis commands.
- [ ] Review `git diff` and prove no Unity file was changed by this work.
- [ ] Report measured current state, unresolved rotation/student-frequency/GPU/human-eval inputs, exact next commands, and the honest path to a narrow Opus win.
