# v8 Primary Benchmark Results

Scores are percentages unless noted. Brackets are 95% intervals.

| Metric | base | v71 | sonnet |
|---|---:|---:|---:|
| Good Distractor Rate | NOT YET RUN | NOT YET RUN | NOT YET RUN |
| Good@3 | NOT YET RUN | NOT YET RUN | NOT YET RUN |
| Numeric misconception→answer consistency | NOT YET RUN | NOT YET RUN | NOT YET RUN |
| Diagnostic-quality proxy pass | NOT YET RUN | NOT YET RUN | NOT YET RUN |
| Selective GDR at ≥80% coverage | NOT YET RUN | NOT YET RUN | NOT YET RUN |
| Numeric binding confidence ECE | NOT YET RUN | NOT YET RUN | NOT YET RUN |
| Numeric binding confidence Brier | NOT YET RUN | NOT YET RUN | NOT YET RUN |
| Valid exactly-3 output | 96.4% (135/140) [91.9, 98.5] | 98.6% (138/140) [94.9, 99.6] | 95.0% (133/140) [90.0, 97.6] |
| No answer equals key | 58.6% (82/140) [50.3, 66.4] | 79.3% (111/140) [71.8, 85.2] | 95.0% (133/140) [90.0, 97.6] |
| Three distinct answers | 70.0% (98/140) [62.0, 77.0] | 84.3% (118/140) [77.4, 89.4] | 94.3% (132/140) [89.1, 97.1] |
| Three distinct misconceptions | 91.4% (128/140) [85.6, 95.0] | 94.3% (132/140) [89.1, 97.1] | 95.0% (133/140) [90.0, 97.6] |
| Hardened computation validity | NOT APPLICABLE | 75.0% (315/420) [69.3, 80.7] | NOT APPLICABLE |

Diagnostic quality/plausibility is an expert/Opus proxy, not observed student option frequency.

Exact/Partial/Proportional@3 are diagnostics and are intentionally excluded from this headline table.

## Paired comparison: v71_vs_sonnet

| Metric | Absolute delta [95% CI] | Relative error reduction [95% CI] | 40% target |
|---|---:|---:|---:|
| Valid exactly-3 output | 3.6 [-0.7, 7.9] | 71.4% [-33.3%, 100.0%] | NOT DEMONSTRATED |
| No answer equals key | -15.7 [-23.6, -8.6] | -314.3% [-1170.0%, -120.0%] | NOT DEMONSTRATED |
| Three distinct answers | -10.0 [-17.1, -3.6] | -175.0% [-700.0%, -40.0%] | NOT DEMONSTRATED |
| Three distinct misconceptions | -0.7 [-5.7, 4.3] | -14.3% [-251.7%, 61.6%] | NOT DEMONSTRATED |
