# v8 One-Shot Training and Evaluation Runbook

This is a one-primary-run workflow. Do not start Colab while any preflight gate is red.

> **Current recovery (2026-07-12):** the numeric Opus judge calibration remains
> rejected at 77.5% agreement and 15% false-positive rate; its thresholds and
> artifact were not changed. Opus is now teacher/frontier generator only.
> `deterministic_teacher_filter` accepted 32/130 generated rows against a
> predeclared floor of 20 and rejected 98. The manifest records
> `opus_judge_ready: false`, `deterministic_teacher_filter_ready: true`, and
> `training_ready: true`.

1. **Rotate the compromised token.** The token pasted into chat must stay revoked. Put only the newly rotated key in local `.env`; never paste it into this notebook, source, logs, or Git.

2. **Confirm the resolved Opus preflight.** The required deployment is `anthropic-primary/claude-opus-4-8`. Exact 8-token streaming, non-streaming, and repository-client probes succeeded with the rotated `.env` token. The earlier 403 was a stale inherited-token bug, not a missing entitlement; sanitized evidence is in `data/eval_out/opus_access_preflight_v8.json`.

3. **Verify masked configuration.**

   ```bash
   .venv/bin/python -c 'from src.config import TFY_API_KEY,TFY_BASE_URL,TFY_CREDENTIAL_SOURCE,OPUS_MODEL_ID; print({"key_present":bool(TFY_API_KEY),"credential_source":TFY_CREDENTIAL_SOURCE,"gateway":TFY_BASE_URL,"model":OPUS_MODEL_ID})'
   ```

   Output must show `key_present: True` and `credential_source: dotenv`; it must never print the key.

4. **Build and verify the API-free core data.**

   ```bash
   .venv/bin/python -m src.v8_data
   .venv/bin/python -m unittest discover -s tests -p 'test_v8_*.py'
   ```

   The preliminary manifest has 130 teacher candidates, 140 frozen questions, 100% verifier-passing targets, `opus_access_ready: true`, and `training_ready: false` until verified teacher rows are generated.

5. **Estimate the role-separated Opus teacher usage without calling it.**

   ```bash
   .venv/bin/python -m src.run_frontier \
     --input data/processed/v8_teacher_pool.jsonl \
     --model anthropic-primary/claude-opus-4-8 \
     --out data/processed/v8_teacher_predictions_opus.jsonl \
     --deterministic-teacher --estimate-only
   ```

   This stage is capped at 130 task calls and 66,560 output tokens. Including
   the already completed 80-call rejected calibration, preparation totals
   210/612 tasks with a 76,160/124,400 configured output-token ceiling. Actual
   provider token usage was not retained. Look up organization-specific pricing
   in TrueFoundry; the repository does not invent one.

6. **Preserve the rejected Opus judge calibration and separate roles.**

   `data/eval_out/opus_binding_calibration_v8.json` remains `accepted: false`
   with 62/80 correct, TP/TN/FP/FN 28/34/6/12, 15% FPR, and 30% FNR.
   Do not rerun, lower, reinterpret, or use it for confidence. Do not run the
   12-call nonnumeric arm for this route. Mixed/nonnumeric confidence remains
   unavailable. `opus_judge_ready` must remain false.

7. **Generate the real-question teacher pool with registered-label guidance.**

   ```bash
   .venv/bin/python -m src.run_frontier \
     --input data/processed/v8_teacher_pool.jsonl \
     --model anthropic-primary/claude-opus-4-8 \
     --out data/processed/v8_teacher_predictions_opus.jsonl \
     --deterministic-teacher --max-calls 130
   ```

   The completed resumable artifact contains 130 rows from 130 paid generation
   tasks and zero resumed tasks.

8. **Filter locally with no Opus self-judgment.**

   ```bash
   .venv/bin/python -m src.v8_teacher \
     --predictions data/processed/v8_teacher_predictions_opus.jsonl \
     --minimum-survivors 20
   ```

   The 390-call/46,800-token Opus self-judge stage is intentionally skipped.
   The all-or-nothing record filter requires exact schema/count, no key
   collision, distinct answers/misconceptions, hardened computation evaluation,
   question grounding, exact alias mapping to three distinct procedures in
   `wayline-procedures-v1`, deduplication, and both leakage boundaries.
   It accepted 32 and rejected 98: computation/grounding 76, unsupported
   procedure mapping 11, answer shape 10, and key collision 1. It makes no
   plausibility, diagnostic-quality, or observed-frequency claim.

9. **Build the final one-shot training file and fail closed.**

   ```bash
   .venv/bin/python -m src.v8_data --require-deterministic-teacher
   .venv/bin/python -m src.v8_data --verify-only
   ```

   `data/processed/v8_manifest.json` must say `training_ready: true`,
   `teacher_acceptance_route: deterministic_teacher_filter`,
   `deterministic_teacher_filter_ready: true`, `opus_judge_ready: false`, and
   contain at least 20 teacher records. It must verify five artifact hashes,
   the filter-report evidence hash, both leakage boundaries, and every target
   pair. Do not weaken gates to increase yield.

10. **Run the full local preflight.**

    ```bash
    .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
    .venv/bin/python -m src.error_analysis_v8 predictions_tuned_v7.jsonl \
      --training-data data/processed/train_v7.jsonl \
      --out data/eval_out/error_analysis_v71.json
    ```

    Do not commit, push, or start Colab until the owner has reviewed this diff and the manifest. If the owner later wants the notebook to clone from GitHub, they must deliberately publish these reviewed files; otherwise preload the reviewed repository folder in Colab before Run All. The notebook fails if it clones an older repository without the v8 manifest.

11. **Open one Colab session.** Choose an L4 or A100 GPU. A T4 may fit batch size 1 but is not the recommended one-shot path. Add `HF_TOKEN` as a Colab secret if automatic adapter push is desired. Open `notebooks/train_qwen3_distractor_v8.ipynb`.

12. **Run All once.** The notebook:

    - verifies hashes and both leakage boundaries;
    - requires Opus teacher survivors;
    - creates a grouped 90/10 training/validation split;
    - loads `unsloth/Qwen3-8B-bnb-4bit` at immutable revision `1deaf68f694c40dbce295da300851729d759b21a`;
    - performs one three-epoch QLoRA SFT invocation;
    - evaluates/saves each epoch and automatically restores the lowest-validation-loss checkpoint;
    - saves and, when configured, pushes the adapter;
    - generates the untouched frozen predictions only after checkpoint selection;
    - writes separate **model-only** and verifier-guided **best-of-N** artifacts;
    - computes local hard gates and downloads all artifacts.

    Do not interrupt after training starts. Downloaded adapter ZIP plus prediction/metric JSON files are the recovery boundary.

13. **Copy Colab artifacts into the repository root.** At minimum:

    - `predictions_v8_model_only.jsonl`
    - `predictions_v8_best_of_n.jsonl`
    - `local_metrics_v8_model_only.json`
    - `local_metrics_v8_best_of_n.json`
    - `v8_training_receipt.json`
    - the adapter ZIP or confirmed Hugging Face URL

14. **Estimate final frontier generation only.** Opus may generate the 140-item
    comparison baseline but must not judge either system. Run the notebook's
    exact generation handoff with `--estimate-only` first. Any independent
    judge requires a separate accepted calibration, symmetric v8/Opus use, a
    separate estimate, and owner approval.

15. **Run deterministic final metrics and keep unavailable cells honest.** With
    no accepted independent judge artifact, report schema, key safety,
    distinctness, hardened computation/grounding, and programmatic numeric
    metrics only. Leave GDR, strict task-quality proxy, ECE/Brier, and
    mixed/nonnumeric confidence unavailable. If an independent judge such as
    Sonnet later clears calibration, apply the same model/prompt/thresholds to
    both systems and label plausibility/diagnostic-quality results as proxies.

16. **Apply the pre-registered decision rule.** Target absolute GDR ≥90% and at least **40% relative error-rate reduction** versus Opus on each selected bounded quality metric. Report absolute deltas and intervals. Existing Opus/v8 cells remain **NOT YET RUN** until artifacts exist.

17. **Stop after reporting.** A miss is a measured result, not permission for routine retraining. A second training run is contingency-only for a serious technical failure (corrupt data, wrong file/revision, failed checkpoint save, or broken notebook execution). Do not use the frozen benchmark to tune v8.1.

18. **Require humans for any “best” claim.** Blindly randomize at least 60 paired v8/Opus questions, use two independent middle-school math assessment reviewers, adjudicate disagreements, and publish inter-rater agreement plus the rubric. Until that exists, call diagnostic quality an Opus/expert proxy and do not claim “best on the planet.”

## Why 8B SFT, not 4B/14B or SFT+DPO

- **4B:** proven local fit, but v7.1 reached only ~60% numeric binding. Repeating it is a weak one-shot bet.
- **8B:** doubles capacity while a Q4-class runtime remains plausible on the owner's 16 GB M4 alongside the game. It fits L4/A100 QLoRA with batch 1/accumulation 8. Local latency, RSS, and game coexistence still require measurement.
- **14B:** Q4 weights plus KV/runtime and Unity approach the 16 GB machine's unsafe memory-pressure range and likely violate latency goals. It is not a credible local-game default.
- **No DPO phase:** v6 DPO did not improve binding and adds another failure surface. One high-quality SFT run on verified targets is the evidence-backed recipe.

For game release, export the selected 8B adapter to Q4_K_M and measure p50/p95 latency, RSS, and verifier acceptance. If it violates the game budget, keep v8 as the research model and v7.1 as the runtime model; do not disguise a system-operability regression as a quality win.
