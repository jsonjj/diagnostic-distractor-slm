# v7.1 Error Analysis Used for v8 Data Design

Source: the frozen legacy 140-item development set. This analysis may guide v8 data because final claims use the separate `eval_v8_frozen.jsonl`.

## Observable v7.1 failures

Across 140 questions / 414 emitted distractors:

- wrong distractor count: 2 questions (1.4%);
- correct-key collision: 27 questions (19.3%);
- duplicate answers: 20 questions (14.3%);
- duplicate misconception strings: 6 questions (4.3%);
- hardened-valid computations: 315/414 emitted pairs (76.1%), or 315/420 expected slots (75.0%);
- invalid computations: 75; unparseable/missing computations: 24;
- answer mix: 402 numeric / 12 nonnumeric;
- generated labels absent as an exact normalized string from `train_v7`: 199/414 (48.1%).

The last count is not “unsupported misconception” truth. A new wording can be semantically valid; deciding support requires expert review or a validated semantic matcher.

The existing judged numeric-binding result is 60.4%, but historical logs do not retain per-pair verdicts. Binding failures therefore cannot honestly be sliced by topic without a new judged run.

## Highest deterministic failure slices

- Equivalent Fractions: 0/6 computations passed.
- Multiplying/Dividing Negative Numbers: 0/3 passed.
- Standard Form: 1/6 passed.
- Converting Fractions↔Percentages: 2/6 passed.
- Dividing Fractions: 1/3 passed.
- Place Value: 11/36 emitted computations invalid/unparseable, plus 3 key collisions.
- BIDMAS: 8/33 computations invalid/unparseable, 2 key collisions.
- Multiplying/Dividing Decimals: 8/45 computations invalid, 5 key collisions, 6 duplicate-answer questions.
- Rounding to Decimal Places: 7/21 computations invalid.
- Rounding to Nearest 10/100/etc: 4/15 computations invalid and 3 key collisions.

## Base and legacy Sonnet context

- Base: 53/140 key-collision questions, 37/140 duplicate-answer questions, 4/140 wrong-count questions; no computation field.
- Legacy Sonnet: 0 key collisions, 1/140 duplicate-answer question, 7/140 wrong-count questions; no computation field.

Legacy Sonnet's missing computation is a prompt/schema mismatch, not evidence of mathematical failure. The fair Opus comparison must use the v8 prompt.

## v8 response

The deterministic builder adds 1,200 verifier-passing rows (100 each) for Equivalent Fractions, full Fraction Division, Fraction→Decimal, Fraction→Percentage, LCM, Mixed→Improper Fractions, Negative Multiplication, Percentage Change, Significant-Figure Rounding, Simplifying Fractions, Square Roots, and Standard Form. These use new operands, not copied development items.

It also reserves 130 previously unused real Eedi questions for Opus
distillation. The executed role-separated route accepted 32/130 all-three
outputs through structure, key safety, distinctness, hardened
computation/grounding, exact audited-procedure aliases, deduplication, and
leakage gates. The rejected Opus judge was not used. No calibrated
task-quality/plausibility proxy is currently available.

This is the strongest one-shot data response available without student option counts. It does not guarantee 90% GDR; that remains a final measurement.
