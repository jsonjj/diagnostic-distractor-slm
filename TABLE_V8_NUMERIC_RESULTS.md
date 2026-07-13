# v8 Numeric-only intended-domain view

This is an additional trained-domain recalibration. It preserves the original all-scope evidence and is **not overall MCQ superiority** outside the declared numeric-answer scope.

## Eligibility rule and fixed scope

- Frozen scope: **100 included / 40 excluded** from 140.
- Blind-review scope: **16 included / 8 excluded** from 24.
- Classification uses only trusted gold `question`/`correct` fields and deterministic parser/type logic; it does not inspect model outputs, automatic scores, flags, ratings, or winners.
- Included: exact integers/signed values, decimals, fractions/mixed numbers, percentages, numeric money/measurement displays, exact repeating decimals, bounded powers, exact roots, and standard-form values.
- Excluded: named-person/categorical/verbal/truth answers, operation or sign choices, image-only answers, ordered/compound answers, Roman-numeral keys, algebraic keys, and malformed/unparseable keys.
- Rule: `v8-gold-numeric-eligibility-v1`. Full per-ID decisions are in `data/eval_out/v8_numeric_scope_manifest.json`.

- Included frozen IDs (100): `1002`, `1032`, `1055`, `1089`, `1113`, `1117`, `1134`, `116`, `1174`, `1183`, `1187`, `1233`, `1240`, `1265`, `1266`, `1289`, `1295`, `1309`, `1315`, `1343`, `1345`, `1352`, `1360`, `1365`, `1385`, `1387`, `1390`, `1393`, `1396`, `1407`, `1412`, `1418`, `1431`, `1433`, `1439`, `1440`, `1507`, `1508`, `156`, `1562`, `1575`, `1625`, `1659`, `1661`, `1689`, `1700`, `1720`, `1728`, `1750`, `1777`, `18`, `1805`, `1819`, `1829`, `1842`, `1863`, `1866`, `191`, `262`, `263`, `266`, `275`, `292`, `351`, `398`, `493`, `520`, `531`, `555`, `568`, `60`, `602`, `604`, `61`, `617`, `624`, `639`, `675`, `678`, `708`, `714`, `721`, `73`, `730`, `755`, `762`, `782`, `786`, `815`, `818`, `825`, `826`, `849`, `851`, `875`, `882`, `892`, `920`, `973`, `981`
- Excluded frozen IDs (40): `0`, `1015`, `1034`, `1043`, `1102`, `1152`, `1162`, `1184`, `119`, `1252`, `136`, `1371`, `147`, `1488`, `1503`, `1548`, `1550`, `1552`, `1555`, `1624`, `1629`, `1703`, `1708`, `1840`, `204`, `22`, `298`, `402`, `411`, `417`, `481`, `51`, `541`, `580`, `629`, `764`, `770`, `827`, `958`, `965`

### Exclusion reasons

- `algebraic_or_text_answer`: 1
- `compound_multi_value_answer`: 2
- `image_only_answer`: 3
- `named_or_ordered_text_answer`: 2
- `named_person_or_categorical_answer`: 17
- `operation_choice_text`: 8
- `unparseable_numeric_key`: 1
- `verbal_or_concept_answer`: 2
- `yes_no_or_truth_answer`: 4

## Frozen numeric-subset deterministic metrics

Scores are pass percentages. Brackets are 95% intervals: Wilson for question-level gates and question-cluster bootstrap for computation.

| Metric | Opus | v8 model-only | v8 best-of-N |
|---|---:|---:|---:|
| Valid exactly-3/schema | 96.0% (96/100) [90.2, 98.4] | 100.0% (100/100) [96.3, 100.0] | 100.0% (100/100) [96.3, 100.0] |
| No distractor equals key (numeric equivalence) | 94.0% (94/100) [87.5, 97.2] | 89.0% (89/100) [81.4, 93.7] | 92.0% (92/100) [85.0, 95.9] |
| Three distinct answers (numeric equivalence) | 82.0% (82/100) [73.3, 88.3] | 83.0% (83/100) [74.5, 89.1] | 96.0% (96/100) [90.2, 98.4] |
| Three distinct misconception labels | 96.0% (96/100) [90.2, 98.4] | 100.0% (100/100) [96.3, 100.0] | 100.0% (100/100) [96.3, 100.0] |
| Hardened computation validity | 54.6% (165/302) [47.3, 62.1] | 83.3% (250/300) [77.7, 88.3] | 90.0% (270/300) [85.3, 94.0] |

### Paired comparison: v8 model-only vs Opus

| Metric | Absolute difference [95% CI] | Relative error-rate reduction [95% CI] | 40% interval target |
|---|---:|---:|---:|
| Valid exactly-3/schema | +4.0 pp [+1.0, +8.0] | +100.0% [+100.0%, +100.0%] | DEMONSTRATED |
| No distractor equals key (numeric equivalence) | -5.0 pp [-13.0, +3.0] | -83.3% [-500.0%, +30.0%] | NOT DEMONSTRATED |
| Three distinct answers (numeric equivalence) | +1.0 pp [-11.0, +12.0] | +5.6% [-85.7%, +52.4%] | NOT DEMONSTRATED |
| Three distinct misconception labels | +4.0 pp [+1.0, +8.0] | +100.0% [+100.0%, +100.0%] | DEMONSTRATED |
| Hardened computation validity | +28.7 pp [+20.5, +37.2] | +63.3% [+49.8%, +74.9%] | DEMONSTRATED |

### Paired comparison: v8 verifier-guided best-of-N vs Opus

| Metric | Absolute difference [95% CI] | Relative error-rate reduction [95% CI] | 40% interval target |
|---|---:|---:|---:|
| Valid exactly-3/schema | +4.0 pp [+1.0, +8.0] | +100.0% [+100.0%, +100.0%] | DEMONSTRATED |
| No distractor equals key (numeric equivalence) | -2.0 pp [-9.0, +5.0] | -33.3% [-350.0%, +57.1%] | NOT DEMONSTRATED |
| Three distinct answers (numeric equivalence) | +14.0 pp [+7.0, +22.0] | +77.8% [+50.0%, +95.5%] | DEMONSTRATED |
| Three distinct misconception labels | +4.0 pp [+1.0, +8.0] | +100.0% [+100.0%, +100.0%] | DEMONSTRATED |
| Hardened computation validity | +35.4 pp [+28.2, +42.8] | +78.0% [+68.4%, +86.8%] | DEMONSTRATED |

GDR, Good@3, accepted numeric binding, diagnostic-quality proxy, selective GDR, ECE, and Brier remain **UNAVAILABLE**; the numeric scope does not manufacture missing judgments.

## Numeric-eligible blind human subset

- Included review items (16): `R02`, `R03`, `R04`, `R06`, `R07`, `R08`, `R09`, `R11`, `R12`, `R13`, `R17`, `R19`, `R20`, `R21`, `R22`, `R23`
- Excluded review items (8): `R01`, `R05`, `R10`, `R14`, `R15`, `R16`, `R18`, `R24`
- This remains exploratory: one reviewer, a smaller fixed gold-defined subset, and no inter-rater reliability.

### Excluded blind items

| Review item | Frozen ID | Gold answer | Reason |
|---|---:|---|---|
| R01 | 1552 | `OnlyKatie` | `named_person_or_categorical_answer` |
| R05 | 629 | `OnlyTom` | `named_person_or_categorical_answer` |
| R10 | 1184 | `OnlyPaul` | `named_person_or_categorical_answer` |
| R14 | 764 | `sometimestrue` | `yes_no_or_truth_answer` |
| R15 | 136 | `-4and6` | `compound_multi_value_answer` |
| R16 | 827 | `Neitheriscorrect` | `named_person_or_categorical_answer` |
| R18 | 204 | `Twohundredandfourthousandandfifty` | `verbal_or_concept_answer` |
| R24 | 1371 | `OnlyKatie` | `named_person_or_categorical_answer` |

### Human 1–5 ratings

| Dimension | Opus mean · median | v8 best-of-N mean · median | Opus − v8 [paired bootstrap 95% CI] |
|---|---:|---:|---:|
| Diagnostic usefulness | 4.38 · 5.00 | 4.31 · 4.00 | +0.06 [-0.31, +0.38] |
| Student plausibility | 4.50 · 5.00 | 3.75 · 4.00 | +0.75 [-0.00, +1.50] |
| Teacher actionability | 4.44 · 5.00 | 4.25 · 4.00 | +0.19 [-0.19, +0.56] |
| Equal-weight overall | 4.44 · 5.00 | 4.10 · 4.00 | +0.33 [-0.06, +0.75] |

### Recorded votes and response anomaly

- **v8_best_of_n:** 11 wins, 0 ties, 5 losses; 68.8% win rate.
- **opus:** 5 wins, 0 ties, 11 losses; 31.2% win rate.
- Blind labels: A 0, Tie 0, B 16. All responses same label: `True`; status `REVIEW_REQUIRED`.
- Selected candidate rating direction: higher 4, equal 6, lower 6, tie 0.
- The all-B anomaly is retained and the nominal vote is not treated as clean preference evidence.

### Reviewer issue flags

| Issue | Opus | v8 best-of-N |
|---|---:|---:|
| any | 1/16 (6.2%) | 4/16 (25.0%) |
| mathematically inconsistent | 0/16 (0.0%) | 0/16 (0.0%) |
| correct answer collision | 0/16 (0.0%) | 0/16 (0.0%) |
| duplicate | 0/16 (0.0%) | 2/16 (12.5%) |
| nonsense | 1/16 (6.2%) | 2/16 (12.5%) |

### Included item-level results

Each item lists `diagnostic/plausibility/actionability`; issue flags are candidate-level reviewer observations.

- **R02 / frozen 1866 / gold `31`:** recorded `B` → `v8_best_of_n`; v8 4/5/4 (none); Opus 5/3/4 (none).
- **R03 / frozen 624 / gold `6`:** recorded `B` → `opus`; v8 4/2/4 (nonsense); Opus 4/4/5 (none).
- **R04 / frozen 1720 / gold `42`:** recorded `B` → `opus`; v8 4/3/4 (none); Opus 4/4/5 (none).
- **R06 / frozen 675 / gold `0.4`:** recorded `B` → `v8_best_of_n`; v8 4/5/4 (duplicate); Opus 4/5/4 (none).
- **R07 / frozen 875 / gold `27.5`:** recorded `B` → `v8_best_of_n`; v8 5/5/5 (none); Opus 5/5/5 (none).
- **R08 / frozen 973 / gold `0.025`:** recorded `B` → `v8_best_of_n`; v8 5/5/5 (none); Opus 5/5/5 (none).
- **R09 / frozen 531 / gold `75`:** recorded `B` → `v8_best_of_n`; v8 5/4/5 (none); Opus 5/5/5 (none).
- **R11 / frozen 1240 / gold `1000`:** recorded `B` → `opus`; v8 5/5/5 (none); Opus 5/5/5 (none).
- **R12 / frozen 1343 / gold `8`:** recorded `B` → `v8_best_of_n`; v8 4/4/4 (none); Opus 5/5/5 (none).
- **R13 / frozen 1440 / gold `20%`:** recorded `B` → `v8_best_of_n`; v8 3/1/3 (nonsense); Opus 4/5/5 (none).
- **R17 / frozen 1659 / gold `13/7`:** recorded `B` → `opus`; v8 5/5/4 (none); Opus 5/5/4 (none).
- **R19 / frozen 920 / gold `100000`:** recorded `B` → `v8_best_of_n`; v8 3/2/3 (duplicate); Opus 1/1/1 (nonsense).
- **R20 / frozen 1819 / gold `3`:** recorded `B` → `v8_best_of_n`; v8 4/4/4 (none); Opus 4/5/4 (none).
- **R21 / frozen 755 / gold `69.94`:** recorded `B` → `opus`; v8 5/5/5 (none); Opus 5/5/5 (none).
- **R22 / frozen 292 / gold `-2`:** recorded `B` → `v8_best_of_n`; v8 5/4/5 (none); Opus 5/5/5 (none).
- **R23 / frozen 263 / gold `17`:** recorded `B` → `v8_best_of_n`; v8 4/1/4 (none); Opus 4/5/4 (none).

## Product interpretation and verdict

- **No holistic numeric-scope winner is demonstrated.** Best-of-N v8 wins the numeric deterministic hard-gate comparison, but key safety is lower and the required holistic GDR/quality evidence is unavailable.
- **Model-only:** demonstrated +28.7 pp computation advantage; −5.0 pp key safety; +1.0 pp answer diversity with an interval spanning zero.
- **Verifier-guided best-of-N:** +35.4 pp computation and +14.0 pp answer diversity, both demonstrated; −2.0 pp key safety with an interval spanning zero.
- **Human ratings:** no demonstrated winner on the numeric subset. Opus has the +0.33/5 overall point estimate, but its interval crosses zero.
- Numeric-only is the fair trained-domain comparison; 40 written/categorical/operation/image/compound/unparseable-key items are explicitly outside scope.
- Numeric human overall Opus advantage: +0.33 points [-0.06, +0.75]. Previous all-scope advantage: +1.01; change: -0.68.
- Previous Opus human-quality advantage persists with its numeric-subset interval above zero: `False`.
- Model-only and verifier-guided effects are reported separately above. Best-of-N gains are system-level and cannot be attributed wholly to the 8B model.
- Do not describe these results as overall MCQ superiority. GDR/Good@3, student response frequency, model-only human ratings, and inter-rater agreement remain unavailable.

Original all-scope files remain unchanged; this report is an additional intended-domain view.
