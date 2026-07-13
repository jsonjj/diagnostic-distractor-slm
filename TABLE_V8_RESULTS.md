# v8 Final Evaluation Results

## Statistical verdict

- **v8 8B model-only:** NOT DEMONSTRATED for the registered win rule. GDR/Good@3 are unavailable, so neither ≥90% GDR nor ≥40% GDR relative error-rate reduction versus Opus can be established.
- **v8 verifier-guided best-of-N:** NOT DEMONSTRATED for the registered win rule for the same reason. Verifier-guided best-of-N is a system result, not model-only performance.
- Deterministic hard-gate paired deltas and relative error-rate reductions are reported below, but they do not substitute for GDR.
- For every selected primary quality metric, the ≥40% relative error-rate reduction target is NOT DEMONSTRATED because the required binding/quality judgments are unavailable.

Student plausibility and diagnostic quality are **UNAVAILABLE**. No student option-pick frequencies exist, and the registered Opus judge calibration was rejected.

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

- 149 full Python tests passed.
- 46 v8-specific tests passed.
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
