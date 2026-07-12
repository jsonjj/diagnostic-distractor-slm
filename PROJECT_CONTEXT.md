# Diagnostic Distractor SLM — Full Project Context (Handoff)

> **Purpose of this file:** a complete, self-contained brief so a fresh agent (or person) can be fully caught up — the goal, the code, every dataset iteration, all results, and the current state. Read top to bottom. If anything is ambiguous, ask the owner before acting.

_Last updated: 2026-07-10. Repo: `/Users/jonat/Projects/diagnostic-distractor-slm` · GitHub: `https://github.com/jsonjj/diagnostic-distractor-slm` · branch `main` (HEAD `b86b2d8`)._

_Status: **COMPLETE through v7.1 (the final model).** Trained, evaluated, published to Hugging Face. Only the demo video remains (owner task). Full lineage v1→v7.1 documented below._

---

## 0. TL;DR — where things stand

- **Goal:** prove a small, locally-runnable open model can generate **diagnostic distractors** for middle-school "Number" MCQs — 3 wrong answers, each tagged to a named student **misconception** and **numerically consistent** with it (the answer = what a student making that mistake would compute). Consistency is the differentiator vs prior work; the win is *behavior from data*, not beating a frontier model.
- **Final model: v7.1** — **Qwen3-4B**, QLoRA, trained on `train_v7.jsonl`. Published at **`j2ampn/qwen3-4b-distractor-lora-v7`** (Hugging Face).
- **Headline results (140-item held-out real eval):**
  - Judged consistency **60%** (numeric subset 60.4%) — the FIRST real gain past the ~50% ceiling that held across v1–v6, and it **beats the 7B prior-work SOTA** (LookAlike 2025: 51.6%) at smaller scale.
  - **Beats the base on all four Appendix-A rubric dims** (spec 0.50→1.07, robustness 0.35→0.77, task 0.31→0.74, consistency 0.23→0.71) — the assignment's explicit "win" bar, cleared decisively.
  - **Meets/beats Sonnet 5** on the diagnostic-defining metrics: exactly_3 (104% of Sonnet) and distinct_misconceptions (99%, ~tied).
  - **Honest ceiling:** consistency is ~63% of Sonnet's 95.8%, not full parity — a capacity limit for small open models, documented, not a data bug.
- **What's left:** owner records the 3–5 min demo video (`DEMO_SCRIPT.md`) and submits links. Everything else is done and pushed.

---

## 1. The behavior spec (the exact contract the model must satisfy)

Given a "Number"-strand question + correct answer + topic, output **exactly 3 diagnostic distractors** as one JSON object. Each distractor must:
1. be tagged to a **specific, named student misconception / procedural error**,
2. carry a **`computation`** — the show-the-work arithmetic that misconception performs on THIS question, ending in `= <answer>`,
3. have an **answer** that is exactly what that misconception computes (numeric consistency — the core claim),
4. not equal the correct answer,
5. be distinct in answer and (soft) in misconception label from the other two.

**Target schema (v4+):** `{"distractors":[{"misconception":"...","computation":"0.4 ÷ 0.2 = 2","answer":"2"}, ...]}`
Legacy v1–v3 targets omit `computation` (backward-compatible; `build_assistant` emits it only when present).

---

## 2. Repo, environment, how to run

- **Python:** `.venv/bin/python` (deps in `requirements.txt`).
- **Secrets in `.env`** (not committed): `TFY_BASE_URL`, `TFY_MODEL` (=`claude-sonnet-5`), `TFY_API_KEY` (TrueFoundry → Sonnet 5, the teacher + judge), `HF_TOKEN`.
- **Final base model:** `unsloth/Qwen3-4B-bnb-4bit` (v7.1). Earlier versions used `Qwen3-1.7B-bnb-4bit`. QLoRA r=32/α=32, 3 epochs, effective batch 8, Unsloth. v7 needs an A100/L4 (Colab Pro); 1.7B fits a T4.

**Common commands**
```bash
# Build datasets (offline, no GPU)
.venv/bin/python -m src.data_prep                 # real seed + eval hold-out from Kaggle CSVs
.venv/bin/python -m src.generate                  # train_v1   (also --v2 --v3 --v4 --v5 --v7)
.venv/bin/python -m src.real_computations --v7    # distill+verify real computations (COSTS API)
.venv/bin/python -m src.dpo_pairs --n 800         # build DPO preference pairs (offline)

# Evaluate a predictions file vs the 140-item hold-out
.venv/bin/python -m src.eval predictions_tuned_v7.jsonl            # free: alignment, structural, hardened computation-consistency
.venv/bin/python -m src.eval predictions_tuned_v7.jsonl --judge    # + calibrated YES/NO consistency judge (API $)
.venv/bin/python -m src.eval predictions_tuned_v7.jsonl --rubric   # + Appendix-A 0-2 rubric (API $)
.venv/bin/python -m src.consistency_split predictions_tuned_v7.jsonl   # consistency split: numeric vs non-numeric (API $)

# Calibrate the judge / demo / interactive prompt
.venv/bin/python -m src.calibrate_judge deterministic --n 40      # judge vs ground truth (API $)
.venv/bin/python -m src.demo                                      # base-vs-tuned side by side (free)
.venv/bin/python -m src.prompt_model --q "What is 0.2 + 0.15?" --a 0.35 --topic "Adding and Subtracting with Decimals"  # needs GPU
```

**Training:** `notebooks/train_qwen3_distractor.ipynb` (Colab). Clones the repo, loads the base, runs a base litmus, QLoRA-fine-tunes on `TRAIN_FILE` (currently `train_v7.jsonl`), base-vs-tuned eval, downloads predictions, and has a ready (commented) HF-push cell → `j2ampn/qwen3-4b-distractor-lora-v7`.

---

## 3. Source layout (`src/`)

- `config.py` — paths, TrueFoundry config, the 34 "Number" subjects.
- `prompts.py` — `SYSTEM_PROMPT` (show-the-work) + `SYSTEM_PROMPT_LEGACY`, `build_user`, `build_assistant`, `parse_distractors`.
- `buggy_procedures.py` — the synthetic engine: **16 families, 66 misconceptions** (was 8/39 through v4). `LEGACY_FAMILIES` (the original 8) pins v1–v4 builds byte-identical. Each misconception has `apply` (exact value) + `comp` (arithmetic string). **Training-only; never ships.**
- `consistency.py` — `is_consistent`, `check_synthetic_example`, `eval_computation`, and the **hardened** `computation_consistent(comp, ans, question=…)` (operator gate + question-grounding; opt-in via `question=`).
- `format_augment.py` — **(v7)** stylizes plain engine questions into real Eedi LaTeX format (transfer-gap fix). Training-only.
- `data_prep.py` — builds `real_train_seed.jsonl`, `eval_heldout.jsonl` from Kaggle CSVs.
- `generate.py` — assembles all sets: `build_v1..v5`, `build_v7` (`--v2/--v3/--v4/--v5/--v7`).
- `real_computations.py` — teacher-generates + verifies computations for real records (`ensure_real_v4/v5/v7`).
- `dpo_pairs.py` — **(v6)** synthesizes on-spec/off-spec DPO preference pairs from the engine.
- `eval.py` — harness: alignment@K, structural/spec, free hardened `computation_consistency`, `--judge` (concurrent + live progress), `--judge2` (solve-first), `--persona` (plausibility proxy), `--rubric`.
- `consistency_split.py` — **(new)** judged consistency split into numeric (judge-reliable) vs non-numeric.
- `calibrate_judge.py` — **(new)** calibrates the judge vs ground truth (deterministic + human arms).
- `tfy_client.py` — TrueFoundry client (Sonnet 5; omits `temperature`; retry/backoff for 529 overloads).
- `run_frontier.py` — Sonnet baseline → `predictions_frontier.jsonl`.
- `demo.py`, `prompt_model.py` — inference demo + interactive prompting.

---

## 4. Data pipeline & dataset iterations

**Source:** Kaggle Eedi "Number" strand (same data as Feng 2024 / DiVERT). Filtered to all-3-labeled: **281 usable real MCQs** → 140 held-out eval (frozen, 0% leakage) + 141 real train seed. Two limits shaped everything: real data is thin (→ need synthetic) and there are no student-pick rates (→ plausibility unmeasurable directly). See `DATASET.md` for the full source/trust write-up.

**Synthetic engine:** executable "bugs" (Brown & Burton 1978) that compute each misconception's wrong answer — 100% consistent by construction. Training-only.

| Dataset | Rows | Composition | Base | Result / why it exists |
|---|---|---|---|---|
| `train_v1` | 1341 | 141 real (incl. ~62 dup-label) + 1200 synth | 1.7B | shipped; best alignment; consistency 49.8%, label-repetition broke distinct-misc |
| `train_v2` | 1441 | 79 distinct reals + 1362 balanced synth | 1.7B | fixed labels, lost alignment (over-corrected) |
| `train_v3` | 1358 | 79 distinct ×2 + 1200 v1-style synth | 1.7B | best-of-both (labels + alignment) |
| `train_v4` | 1338 | 46 verified reals ×3 + 1200 synth **+ computation** | 1.7B | show-the-work; consistency 53.5% — revealed metric-gaming |
| `train_v5` | 1423 | 16-family de-skewed synth + verified reals | 1.7B | coverage + hardened metric; consistency ~50% → not a coverage problem |
| `dpo_pairs_v6` | 800 | preference pairs (correct vs off-spec binding) | 1.7B | DPO on v5; consistency ~50% — DPO didn't move it at 1.7B |
| **`train_v7`** | **1618** | **real-format synth + distinct-label distilled reals** | **4B** | **capacity + transfer fix; consistency → 60% (v7.1 = distinct-label reals only, fixes a distinct-misc regression from the first v7 run)** |

**v1–v5 rebuild byte-identical** from the committed engine (regression guard verified).

---

## 5. Eval harness, calibration & win criteria

**Metrics (`src/eval.py`, scored on the 140-item hold-out):** alignment (Exact/Partial/Proportional@3), structural (exactly_3, distinct_misconceptions, none_equals_key, distinct_answers, spec_pass), free **hardened** `computation_consistency`, `--judge` (calibrated YES/NO), `--judge2` (solve-first), `--rubric` (Appendix-A 0–2), `--persona` (plausibility proxy).

**Judge calibration (`data/eval_out/judge_calibration.md`):** the one-shot judge agrees with programmatic ground truth **90% on numeric** consistency but only **~50% (35% false-positive) on non-numeric/conceptual** — empirical support for the thesis that LLMs judge student-error plausibility poorly, and the reason we lead with numeric consistency + programmatic checking. One-shot judge is the judge of record (beats solve-first).

**Win criteria:**
- **Beat base** (the assignment's real bar) → **PASS** decisively (rubric beats base on all 4 dims).
- **Behavior from data** (reliable niche behavior a prompt can't guarantee) → **PASS**.
- **Match/approach Sonnet on what makes a distractor diagnostic** → distinct_misconceptions & exactly_3 at/above Sonnet; consistency ~63% of Sonnet (honest ceiling).

---

## 6. Final results (140-item hold-out) — see `TABLE.md`

| Metric | Base | v1 (1.7B) | v6 (1.7B) | **v7.1 (4B, FINAL)** | Sonnet 5 |
|---|---|---|---|---|---|
| Consistency — judge (numeric) | ~0 | — | 51.6 | **60.4** | 95.8 |
| Consistency — judge (full) | ~0 | 49.8 | 50.1 | **59.9** | 94.5 |
| distinct_misconceptions | 91.4 | 30.7 | 99.3 | **94.3** | 95.0 |
| distinct_answers | 70.7 | 87.9 | 80.0 | **84.3** | 94.3 |
| none_equals_key | 62.1 | 90.0 | 78.6 | **80.7** | 100.0 |
| spec_pass | 43.6 | 81.4 | 63.6 | **67.9** | 94.3 |
| exactly_3 | 97.1 | 100.0 | 99.3 | **98.6** | 95.0 |

**Appendix-A rubric (0–2), base vs v7.1:** spec 0.50→**1.07**, robustness 0.35→**0.77**, task 0.31→**0.74**, consistency 0.23→**0.71** (Sonnet ~1.8). Logs in `eval_out/rubric_v7.log`, `eval_out/rubric_base.log`.

**Committed predictions:** `predictions_base_v1`, `predictions_tuned_v1`, `predictions_tuned_v4/v5/v6/v7`, `predictions_frontier(_c)`.

---

## 7. Key findings & the honest story

- **Behavior from data held for structure; deep binding hit a capacity ceiling.** SFT reliably taught the format, distinct misconceptions, and show-the-work (base ~0 → tuned ~60% consistency, 94% distinct-misc). But the misconception→arithmetic *binding* plateaued at ~50% across v1–v6 (1.7B); only moving to **4B + real-format-augmented data (v7)** broke it to ~60%.
- **The consistency ceiling is real, not a data bug:** six data iterations across two model sizes; even 7B prior work (LookAlike) only reached 51.6%. v7.1 matches/beats that at 4B.
- **Contributions beyond the model:** (1) consistency made **programmatically checkable** (computation field + engine; 90% reliable vs the judge's 50% on hard cases) — operationalizing the metric DiVERT left unmeasured; (2) **judge calibration** quantifying where LLM judges fail; (3) full v1→v7.1 lineage with base-vs-tuned deltas.
- **Root cause of the v6→v7 jump:** the synthetic→real *transfer gap* (trained on `What is 0.2 ÷ 0.4?`, tested on `\( 0.2 \div 0.4 = \)`) plus 1.7B capacity. `format_augment.py` + 4B addressed both.

---

## 8. Deliverables status (assignment)

| Deliverable | Status |
|---|---|
| Dataset published | ✅ GitHub (`data/processed/*.jsonl`) + `dataset_sample.jsonl` subset; documented in `DATASET.md` |
| Model on HF Hub | ✅ `j2ampn/qwen3-4b-distractor-lora-v7` (v7.1); prior 1.7B at `j2ampn/qwen3-1.7b-distractor-lora` |
| Running inference demo | ✅ `src/demo.py` (base-vs-tuned), `src/prompt_model.py` (interactive) |
| Eval harness + results table (base vs tuned) | ✅ `src/eval.py`, `TABLE.md` (incl. Appendix-A rubric) |
| Brainlift | ✅ `BRAINLIFT.md` (owner finalized) |
| **3–5 min demo video** | ❌ **owner task** — script in `DEMO_SCRIPT.md` |
| Stretch: DPO | ✅ done (v6) |

---

## 9. Docs map

- `BRAINLIFT.md` — thesis, spiky POV, did-data→behavior-hold analysis, error analysis.
- `DATASET.md` — data source, trustworthiness, evolution, eval results (the data story).
- `TABLE.md` — **the final results table** (base/v1/v6/v7.1/Sonnet + Appendix-A rubric + matters/doesn't-matter split).
- `DEMO_SCRIPT.md` — timed 3–5 min demo video script.
- `RESULTS_FINAL.md` / `RESULTS_V5.md` — historical v1–v6 detail (marked superseded by TABLE.md).
- `DATASET_LINEAGE.md` — narrative of the dataset changes.
- `data/eval_out/judge_calibration.md` — judge calibration finding.
- `PROJECT_CONTEXT.md` — this file (untracked; internal handoff, not pushed).

---

## 10. What a fresh agent should do next

1. **If continuing:** the model is final and shipped; the only open task is the owner's demo video. Don't retrain unless the owner asks.
2. **If asked to improve consistency further:** the lever is capacity (a larger base) or richer real-format/real-question data — but the evidence says returns are diminishing for small open models. Do NOT chase alignment@K (a known-flawed proxy). Do NOT add an inference-time engine/verifier to the *model* (breaks "model from data"); the engine is training-data-only.
3. **Always:** score on the frozen 140-item hold-out; keep v1–v5 builds byte-identical; report consistency numeric-vs-full with the calibration caveat.
