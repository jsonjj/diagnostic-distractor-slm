# v8 Final Evaluation Results

## Statistical verdict

- **v8 8B model-only:** NOT DEMONSTRATED for the registered win rule. GDR/Good@3 are unavailable, so neither ≥90% GDR nor ≥40% GDR relative error-rate reduction versus Opus can be established.
- **v8 verifier-guided best-of-N:** NOT DEMONSTRATED for the registered win rule for the same reason. Verifier-guided best-of-N is a system result, not model-only performance.
- Deterministic hard-gate paired deltas and relative error-rate reductions are reported below, but they do not substitute for GDR.
- For every selected primary quality metric, the ≥40% relative error-rate reduction target is NOT DEMONSTRATED because the required binding/quality judgments are unavailable.
- **Exploratory blinded human result:** Opus wins the usable 1–5 rating evidence on the 24-item, one-reviewer sample: 4.50 overall versus 3.49 for v8 best-of-N, Opus minus v8 +1.01 points [bootstrap 95% CI +0.54, +1.50]. This is a set-level human judgment, not GDR and not a model-only comparison.
- The recorded preference field nominally favors v8 best-of-N 13–11, but every response is `B`; eight selected-B items have a lower three-rating total, and several notes conflict with the B choice. The raw count is preserved but is not treated as credible preference evidence.

Observed student plausibility and the registered diagnostic-quality/GDR metrics remain **UNAVAILABLE**. No student option-pick frequencies exist, and the registered Opus judge calibration was rejected. The blinded reviewer’s plausibility rating is exploratory human judgment only.

## Blinded human review: v8 best-of-N vs Opus

- Exact sample: `n = 24` paired questions; one anonymous reviewer (`reviewer_code = 1`).
- Deterministic stratified pack: three questions from each of eight predeclared Number families and one item from each lower/middle/upper metadata-complexity band per family. Sample seed `20260713`; independent A/B-order seed `20260714`; selection did not use candidate outputs.
- Completion/integrity: JSON and CSV exports match exactly after normalization; 24/24 review IDs and source IDs are unique; every required preference, rating, and issue check is complete; the sealed HTML/key package reproduces from the frozen inputs and its SHA-256 matches. The unsigned exports cannot provide cryptographic proof against manual editing.
- Data-quality warning: A `0`, Tie `0`, B `24`. B’s rating total is higher on 10 items, equal on 6, and lower on 8. Preference status is **REVIEW REQUIRED**.
- Raw recorded vote: v8 best-of-N 13/24 (54.2%; decisive Wilson 95% CI 35.1%–72.1%), Opus 11/24 (45.8%; 27.9%–64.9%), ties 0. Raw vote difference: +2 votes / +8.3 percentage points for v8. No relative preference ratio is claimed.
- Diagnostic usefulness (mean; median): v8 3.83; 4.00, Opus 4.46; 5.00. Opus minus v8 +0.63 [paired question-bootstrap 95% CI +0.21, +1.08].
- Realistic student plausibility: v8 2.83; 2.50, Opus 4.54; 5.00. Opus minus v8 +1.71 [+0.96, +2.46].
- Teacher clarity/actionability: v8 3.79; 4.00, Opus 4.50; 5.00. Opus minus v8 +0.71 [+0.29, +1.13].
- Overall 1–5 mean, averaging all three dimensions equally within every item and then all 24 items equally: v8 3.49 (median across 72 ratings 4.00), Opus 4.50 (median 5.00). Opus minus v8 +1.01 [+0.54, +1.50], equal to 25.3% of the four-point scale span. This is not an “error reduction.”
- Any reviewer issue flag: v8 12/24 (50.0%; Wilson 95% CI 31.4%–68.6%), Opus 2/24 (8.3%; 2.3%–25.8%). By category, v8/Opus: mathematically inconsistent 0/0; correct-answer collision 0/1; duplicate 2/0; nonsense 10/1.
- Inter-rater reliability is **UNAVAILABLE** with one reviewer. Human GDR and Good@3 also remain unavailable because the rubric scores complete sets, not every distractor against every registered gate.
- Full item-level unblinding and cross-evaluation interpretation: `HUMAN_REVIEW_V8_OPUS_RESULTS.md`.
- Machine-readable result with provenance hashes: `data/eval_out/blind_review_v8_opus_final.json`. The hidden source key itself is not copied into that result.

**Integrated verdict:** deterministic hard gates favor verifier-guided v8 best-of-N, and model-only v8 carries most of the computation-consistency gain. In this one-reviewer blind sample, however, Opus clearly wins the internally coherent rating and issue evidence. The nominal v8 preference count is compromised by a systematic all-B response pattern. Neither evidence stream establishes the registered GDR win rule or a publishable holistic superiority claim.

## Training and artifact handoff

- Selected checkpoint: `outputs_v8/checkpoint-403` (validation loss 0.051324423402547836).
- Base: `unsloth/Qwen3-8B-bnb-4bit` at immutable revision `1deaf68f694c40dbce295da300851729d759b21a`.
- Frozen/train hashes matched the receipt and manifest: `True`.
- Adapter ZIP: `qwen3-8b-distractor-lora-v8.zip` (SHA-256 `e00dcb7653e9baa19fb103bbe0712b419fccb281724b487847de7f03a960c7fb`); archive integrity and required adapter entries verified.
- Hugging Face reference: https://huggingface.co/j2ampn/qwen3-8b-distractor-lora-v8 (unverified: Hugging Face API returned 404 with the local credential). The verified local ZIP is the recovery artifact.

## Paid evaluation budget

- Exact frontier model: `anthropic-primary/claude-opus-4-8`.
- Completed frontier task calls: 140/980 (14.3% of cap).
- Configured output-token ceiling: 71,680/172,480 (41.6% of cap).
- Actual provider output-token usage and dollar cost are UNAVAILABLE because the gateway client/cache does not retain usage and organization-specific TrueFoundry pricing is not stored in the repository.

## Verification

- Original deterministic benchmark run: 149 full Python tests and 46 v8-specific tests passed.
- Post-unblinding run: 158 full Python tests and 9 focused blinded-review/scorer tests passed.
- The updated comparison canvas reports no TypeScript errors.
- Frozen data manifest verified: `True`.
- Unity was not run.

## Registered score table

Scores are percentages unless noted. Brackets are 95% intervals.

| Metric | opus | v8_model_only | v8_best_of_n |
|---|---:|---:|---:|
| Good Distractor Rate | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |
| Good@3 | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |
| Numeric misconception→answer consistency | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |
| Diagnostic-quality proxy pass | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |
| Selective GDR at ≥80% coverage | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |
| Numeric binding confidence ECE | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |
| Numeric binding confidence Brier | UNAVAILABLE | UNAVAILABLE | UNAVAILABLE |
| Valid exactly-3 output | 97.1% (136/140) [92.9, 98.9] | 100.0% (140/140) [97.3, 100.0] | 100.0% (140/140) [97.3, 100.0] |
| No answer equals key | 94.3% (132/140) [89.1, 97.1] | 94.3% (132/140) [89.1, 97.1] | 96.4% (135/140) [91.9, 98.5] |
| Three distinct answers | 85.0% (119/140) [78.2, 90.0] | 80.7% (113/140) [73.4, 86.4] | 96.4% (135/140) [91.9, 98.5] |
| Three distinct misconceptions | 97.1% (136/140) [92.9, 98.9] | 100.0% (140/140) [97.3, 100.0] | 100.0% (140/140) [97.3, 100.0] |
| Hardened computation validity | 40.3% (170/422) [34.2, 47.4] | 79.3% (333/420) [73.3, 84.5] | 84.8% (356/420) [79.0, 89.3] |

Diagnostic quality/plausibility would be an independent calibrated expert-model proxy, not observed student option frequency.

Exact/Partial/Proportional@3 are diagnostics and are intentionally excluded from this headline table.

## Paired comparison: v8_model_only_vs_opus

| Metric | Absolute delta [95% CI] | Relative error reduction [95% CI] | 40% target |
|---|---:|---:|---:|
| Valid exactly-3 output | 2.9 [0.7, 5.7] | 100.0% [100.0%, 100.0%] | PASS |
| No answer equals key | 0.0 [-5.7, 5.0] | 0.0% [-200.0%, 66.7%] | NOT DEMONSTRATED |
| Three distinct answers | -4.3 [-12.9, 5.0] | -28.6% [-123.1%, 25.8%] | NOT DEMONSTRATED |
| Three distinct misconceptions | 2.9 [0.7, 5.7] | 100.0% [100.0%, 100.0%] | PASS |
| Hardened computation validity | 39.0 [31.2, 46.1] | 65.3% [55.5%, 73.8%] | PASS |

## Paired comparison: v8_best_of_n_vs_opus

| Metric | Absolute delta [95% CI] | Relative error reduction [95% CI] | 40% target |
|---|---:|---:|---:|
| Valid exactly-3 output | 2.9 [0.7, 5.7] | 100.0% [100.0%, 100.0%] | PASS |
| No answer equals key | 2.1 [-2.9, 7.1] | 37.5% [-100.0%, 87.5%] | NOT DEMONSTRATED |
| Three distinct answers | 11.4 [5.0, 17.9] | 76.2% [47.1%, 94.4%] | PASS |
| Three distinct misconceptions | 2.9 [0.7, 5.7] | 100.0% [100.0%, 100.0%] | PASS |
| Hardened computation validity | 44.5 [37.2, 51.0] | 74.5% [66.0%, 81.8%] | PASS |

## Evidence

- artifact validation: `data/eval_out/v8_artifact_validation.json`
- frontier estimate: `data/eval_out/opus_frontier_estimate_v8.json`
- frontier generation log: `data/eval_out/opus_frontier_generation_v8.log`
- frontier cache: `data/eval_out/opus_frontier_v8.cache.jsonl`
- rejected opus calibration: `data/eval_out/opus_binding_calibration_v8.json`
- python test log: `data/eval_out/python_tests_v8_final.log`
