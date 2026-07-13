# Blind v8 best-of-N vs Opus review

## Executive result

On the usable 1–5 ratings, **Opus won this one-reviewer, 24-item sample**. Opus averaged 4.50/5 overall versus 3.49/5 for v8 best-of-N, an advantage of 1.01 points (bootstrap 95% CI 0.54 to 1.50), or 25.3% of the four-point scale span.

The recorded overall-choice field nominally favors v8 best-of-N 13–11, but it is not credible as a clean preference result: every one of the 24 blind choices is `B`, including eight items where B has the lower sum across the three ratings. Several notes also favor A or criticize B while B remains selected. The raw vote is preserved and reported, not repaired or reinterpreted.

This is exploratory evidence from one reviewer. Inter-rater reliability cannot be computed.

## Inputs and completion validation

- Canonical input: `/Users/jonat/Downloads/blinded-ratings-1.json`
  - modified `2026-07-13T15:25:31-0500`; 10,859 bytes
  - SHA-256 `ec73f317fe50b88f5bde1776cb86ba509410b95c33275df3c2b7af716d7b87e5`
- Matching backup: `/Users/jonat/Downloads/blinded-ratings-1.csv`
  - modified `2026-07-13T15:25:33-0500`; 2,485 bytes
  - SHA-256 `615c5eb070b74097336c1f077196e22d05aa270e3ad6522797d23295a02fbda3`
- The JSON is canonical. Both originals were read in place and left unchanged.
- JSON and CSV normalize to exactly the same 24 rating records.
- Schema `blinded-ratings-v1` and rubric `blinded-set-rubric-v1` are correct.
- Anonymous reviewer code is `1`; the export contains no reviewer name.
- Declared sample size is 24 and all 24 items have a valid A/B/Tie choice, six valid 1–5 ratings, and reviewed issue lists.
- All 24 review IDs and all 24 underlying source IDs are unique and exactly match the sealed key.
- The export schema has no explicit pack-ID field. Pack identity was therefore established by the exact review-ID set plus reproduction of the sealed deterministic package.
- The review package reproduces from the frozen questions and both source files: all sampled IDs exist, all A/B pairs match, candidate order reproduces, and the public artifact is blind and offline.
- Review HTML SHA-256: `b9cf508e930ffb4d55c1545e6e0d37bbcbefcc3a5ef656309faa3fea978bb1ab`.
- Integrity qualification: the exports are unsigned. The matching formats and package reproduction show no structural mismatch, but they cannot cryptographically prove reviewer intent or rule out manual editing. The all-B response pattern is a substantive response-quality anomaly, not proof of tampering.

## Design

- `n = 24` paired questions, one anonymous reviewer.
- Deterministic stratified sample: three questions from each of eight predeclared Number families, with one lower-, middle-, and upper-complexity item per family.
- Sample seed `20260713`; independently randomized candidate-order seed `20260714`.
- Selection used frozen question metadata only, not candidate outputs or automatic scores.
- Rating dimensions are diagnostic usefulness (D), realistic student plausibility (P), and teacher clarity/actionability (T), each on 1–5.
- Paired intervals use a 10,000-draw question-cluster percentile bootstrap. Preference and issue-rate intervals are Wilson 95% intervals.

## Aggregate results

### Recorded overall choice

- v8 best-of-N: 13 wins, 11 losses, 0 ties; 54.2% of all votes and of decisive votes; Wilson 95% CI 35.1% to 72.1%.
- Opus: 11 wins, 13 losses, 0 ties; 45.8% of all votes and of decisive votes; Wilson 95% CI 27.9% to 64.9%.
- Raw difference: v8 +2 votes, or +8.3 percentage points of the 24 votes.
- No relative “preference improvement” is reported: a vote share is not an error rate, and the all-B anomaly makes the recorded-choice contrast unreliable.
- Mechanical audit: A `0`, Tie `0`, B `24`; selected candidate had a higher three-rating total on 10 items, an equal total on 6, and a lower total on 8. Status: **review required**.

### Human 1–5 ratings

- Diagnostic usefulness:
  - v8 best-of-N mean 3.83, median 4.00.
  - Opus mean 4.46, median 5.00.
  - Opus minus v8: +0.63 (bootstrap 95% CI +0.21 to +1.08), 15.6% of the four-point scale span.
- Realistic student plausibility:
  - v8 best-of-N mean 2.83, median 2.50.
  - Opus mean 4.54, median 5.00.
  - Opus minus v8: +1.71 (bootstrap 95% CI +0.96 to +2.46), 42.7% of the four-point scale span.
- Teacher clarity/actionability:
  - v8 best-of-N mean 3.79, median 4.00.
  - Opus mean 4.50, median 5.00.
  - Opus minus v8: +0.71 (bootstrap 95% CI +0.29 to +1.13), 17.7% of the four-point scale span.
- Overall equal-weight mean across all 72 item-dimension ratings:
  - v8 best-of-N mean 3.49, median 4.00.
  - Opus mean 4.50, median 5.00.
  - Opus minus v8: +1.01 (bootstrap 95% CI +0.54 to +1.50), 25.3% of the four-point scale span.

The scale-span percentages contextualize absolute rating-point differences; they are not relative quality gains and are not error-rate reductions.

### Reviewer issue flags

- Any listed issue: v8 12/24 (50.0%, Wilson 95% CI 31.4% to 68.6%); Opus 2/24 (8.3%, 2.3% to 25.8%); v8 is 41.7 percentage points worse.
- Mathematically inconsistent: v8 0/24 (0.0%); Opus 0/24 (0.0%).
- Correct-answer collision: v8 0/24 (0.0%); Opus 1/24 (4.2%).
- Duplicate: v8 2/24 (8.3%); Opus 0/24 (0.0%).
- Nonsense: v8 10/24 (41.7%); Opus 1/24 (4.2%).

Absolute rates are primary here. Category-level relative ratios would be unstable in this one-reviewer sample, and two categories contain a zero rate.

### Unavailable human metrics

- Human GDR and Good@3 remain unavailable: this set-level rubric does not score every distractor against every registered gate.
- Model-only v8 was not in this blind pack, so it has no human vote, rating, or issue result.
- Inter-rater agreement is unavailable with one reviewer.
- Student response frequency remains unavailable; “student plausibility” is this reviewer’s judgment, not observed student option selection.

## Item-level unblinding

Each line gives `A/B mapping`; recorded blind choice and selected source; then `D/P/T` ratings and issue flags for v8 and Opus. “Choice lower/equal” compares the selected candidate with the other candidate’s three-rating total; it does not overwrite the reviewer’s recorded choice.

### 01 · R01 · source 1552 · Standard Form

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `1/1/1`, flags: nonsense. Opus `4/3/4`, flags: correct-answer collision.
- Note: “B shows a bunch of numbers and numeric answers but the question is asking for who was correct.”

### 02 · R02 · source 1866 · BIDMAS

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**.
- v8 `4/5/4`, flags: none. Opus `5/3/4`, flags: none.
- Note: “A has much less plausible answers.”

### 03 · R03 · source 624 · Mental Addition and Subtraction

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `4/2/4`, flags: nonsense. Opus `4/4/5`, flags: none.
- Note: “The question is asking for the digit, not the value.”

### 04 · R04 · source 1720 · Multiples and Lowest Common Multiple

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `4/3/4`, flags: none. Opus `4/4/5`, flags: none.
- Note: “A has more plausible results.”

### 05 · R05 · source 629 · Converting between Fractions and Percentages

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `3/1/3`, flags: nonsense. Opus `4/4/4`, flags: none.
- Note: “It is asking for who was right, not what numerical value.”

### 06 · R06 · source 675 · Ordering Decimals

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; rating totals equal.
- v8 `4/5/4`, flags: duplicate. Opus `4/5/4`, flags: none.
- Note: “B has a bit of a duplicate with 2 answer that are almost the same.”

### 07 · R07 · source 875 · Rounding to Decimal Places

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; rating totals equal.
- v8 `5/5/5`, flags: none. Opus `5/5/5`, flags: none. No note.

### 08 · R08 · source 973 · Converting between Decimals and Percentages

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; rating totals equal.
- v8 `5/5/5`, flags: none. Opus `5/5/5`, flags: none. No note.

### 09 · R09 · source 531 · Multiples and Lowest Common Multiple

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `5/4/5`, flags: none. Opus `5/5/5`, flags: none.
- Note: “A tiny bit more plausible.”

### 10 · R10 · source 1184 · Mental Multiplication and Division

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `4/1/4`, flags: nonsense. Opus `4/5/4`, flags: none. No note.

### 11 · R11 · source 1240 · Rounding to the Nearest Whole (10, 100, etc)

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**; rating totals equal.
- v8 `5/5/5`, flags: none. Opus `5/5/5`, flags: none. No note.

### 12 · R12 · source 1343 · BIDMAS

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `4/4/4`, flags: none. Opus `5/5/5`, flags: none.
- Note: “A little more plausible.”

### 13 · R13 · source 1440 · Converting between Fractions and Percentages

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `3/1/3`, flags: nonsense. Opus `4/5/5`, flags: none. No note.

### 14 · R14 · source 764 · Multiples and Lowest Common Multiple

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `3/1/3`, flags: nonsense. Opus `5/5/5`, flags: none. No note.

### 15 · R15 · source 136 · Multiplying and Dividing Negative Numbers

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `3/1/3`, flags: nonsense. Opus `5/5/5`, flags: none. No note.

### 16 · R16 · source 827 · Simplifying Fractions

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `3/1/3`, flags: nonsense. Opus `5/5/5`, flags: none. No note.

### 17 · R17 · source 1659 · Dividing Fractions

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**; rating totals equal.
- v8 `5/5/4`, flags: none. Opus `5/5/4`, flags: none. No note.

### 18 · R18 · source 204 · Place Value

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `3/1/3`, flags: nonsense. Opus `5/5/5`, flags: none. No note.

### 19 · R19 · source 920 · Rounding to Significant Figures

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**.
- v8 `3/2/3`, flags: duplicate. Opus `1/1/1`, flags: nonsense.
- Note: “A supplied nothing. B was bad. theyre both terrible.”

### 20 · R20 · source 1819 · Square Roots, Cube Roots, etc

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `4/4/4`, flags: none. Opus `4/5/4`, flags: none.
- Note: “A is more plausible.”

### 21 · R21 · source 755 · Adding and Subtracting with Decimals

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**; rating totals equal.
- v8 `5/5/5`, flags: none. Opus `5/5/5`, flags: none. No note.

### 22 · R22 · source 292 · Multiplying and Dividing Negative Numbers

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `5/4/5`, flags: none. Opus `5/5/5`, flags: none.
- Note: “A is a bit more plausible.”

### 23 · R23 · source 263 · Adding and Subtracting Fractions

- A = Opus; B = v8 best-of-N. Recorded choice B → **v8 best-of-N**; choice lower.
- v8 `4/1/4`, flags: none. Opus `4/5/4`, flags: none. No note.

### 24 · R24 · source 1371 · Ordering Negative Numbers

- A = v8 best-of-N; B = Opus. Recorded choice B → **Opus**.
- v8 `3/1/3`, flags: nonsense. Opus `5/5/5`, flags: none. No note.

## v8 versus Opus across all available v8 evaluations

Human ratings below apply only to verifier-guided v8 best-of-N. The model-only system was not included in the blind pack.

### v8 model-only versus Opus

- Valid exactly-three output: +2.9 percentage points; 100.0% relative error-rate reduction.
- No distractor equals the key: +0.0 pp; 0.0% relative error-rate reduction.
- Three distinct answers: −4.3 pp; 28.6% more error relative to Opus (reported in the benchmark as −28.6% error-rate reduction).
- Three distinct misconception labels: +2.9 pp; 100.0% relative error-rate reduction.
- Hardened computation validity: +39.0 pp; 65.3% relative error-rate reduction.
- Human vote, rating, and issue metrics: unavailable because model-only v8 was not reviewed.

### v8 verifier-guided best-of-N versus Opus

- Valid exactly-three output: +2.9 pp; 100.0% relative error-rate reduction.
- No distractor equals the key: +2.1 pp; 37.5% relative error-rate reduction at the point estimate, not demonstrated by its interval.
- Three distinct answers: +11.4 pp; 76.2% relative error-rate reduction.
- Three distinct misconception labels: +2.9 pp; 100.0% relative error-rate reduction.
- Hardened computation validity: +44.5 pp; 74.5% relative error-rate reduction.
- Recorded blind choice: +2 votes and +8.3 pp of vote share for v8, but unusable as a clean preference contrast because all 24 responses are B.
- Human diagnostic-usefulness rating: v8 −0.63 points versus Opus, equal to −15.6% of the four-point scale span.
- Human plausibility rating: v8 −1.71 points, −42.7% of the scale span.
- Human teacher-actionability rating: v8 −0.71 points, −17.7% of the scale span.
- Human overall rating: v8 −1.01 points, −25.3% of the scale span.
- Any human issue flag: v8 +41.7 pp versus Opus.

### Still unavailable for both v8 tracks and Opus

- Registered Good Distractor Rate, Good@3, numeric misconception-to-answer binding, selective GDR, and accepted calibration metrics remain unavailable.
- The blind set-level ratings do not backfill those registered metrics and do not establish the ≥90% GDR or ≥40% GDR-error-reduction win rule.

## Verification

- Focused blind-review/scorer tests: 9 passed.
- Full Python suite: 158 passed.
- Sealed review-package reproduction: passed.
- Companion JSON, JSON/CSV equivalence, 24-item report completeness, and absence of hidden A/B mapping fields in the public machine result: passed.
- Canvas TypeScript check: no errors.
- No paid API or Unity run was performed.

## Honest conclusion

The deterministic evaluation still favors v8 best-of-N on schema reliability, answer diversity, misconception-label diversity, and especially computation consistency; model-only v8 also has a large computation-consistency advantage but trails Opus on distinct answers.

For this blind one-reviewer sample, **Opus wins the credible human-rating evidence**: higher means and medians on every dimension, bootstrap intervals excluding zero in Opus’s favor, and far fewer issue flags. The nominal 13–11 v8 choice count is retained for completeness but should not be used as evidence of a v8 preference win because the all-B pattern conflicts with the ratings and notes. No publishable comparative or registered GDR claim is demonstrated.
