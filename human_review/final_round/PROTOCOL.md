# Blinded Distractor-Set Review Protocol

## Purpose and time

This review compares two anonymous distractor sets for the same sixth-grade Number question. It is designed for one focused **30–45 minute** sitting across **24 paired questions**.

The goal is not to guess who made a set. The goal is to judge which set would better help a teacher identify and respond to student thinking.

## Before starting

1. Open `review.html` locally in a current browser.
2. Use the owner-assigned reviewer code (for example, `R1`). Do not use your name.
3. Set aside one uninterrupted sitting if possible.
4. Do **not** inspect other repository files, browser developer tools, or the owner-only unblinding key.
5. Do **not** research candidate identity or compare outputs with public model examples.
6. Judge from the question, correct answer, and candidate content shown in the tool.

The page makes no network calls. Ratings are saved in that browser's local storage until exported.

## Blinded design

- The sample has 24 questions: three from each of eight predeclared Number families.
- Within each family, one question comes from each lower, middle, and upper metadata-complexity band.
- Complexity uses only the frozen question, construct, and correct-answer representation. It does not use candidate outputs or which candidate wins any automatic metric.
- The fixed sample seed is `20260713`.
- Candidate A/B order is randomized independently for every question with fixed seed `20260714`.
- Candidate identities and automatic metric scores are excluded from the page and exports.

The selection rule is deterministic: map frozen questions to the eight topic families; rank questions within a family by a predeclared representation/operation complexity score with SHA-256 tie-breaking; split the rank into thirds; then take the seeded SHA-256 choice from each family-band. The review order is separately hash-ranked.

The upper band is an **error-prone complexity proxy**, not observed student difficulty. No student option-pick frequencies are available.

## How to review each question

1. Read the question and correct answer.
2. Inspect all distractors in Candidate A and Candidate B. For each distractor, check whether the named misconception, computation, and answer form one coherent student error.
3. Rate each candidate independently on the three 1–5 dimensions below.
4. Explicitly mark either “No listed issue noticed” or every issue that applies.
5. Choose Candidate A, Tie, or Candidate B as the overall better complete set.
6. Add a short note only when it will help adjudicate a close call or explain a serious defect.

The tool prevents moving forward with required fields omitted. Previous answers remain editable.

## Rating anchors

### Diagnostic usefulness

- **1:** The set does not reveal interpretable student thinking; errors are arbitrary, repetitive, or disconnected from the question.
- **3:** The set offers some useful diagnostic signal, but one or more misconceptions are generic, overlapping, or weakly linked to the answers.
- **5:** The set cleanly separates three specific, teachable error patterns and would help identify what a student misunderstood.

### Realistic student plausibility

- **1:** The answers are giveaway wrong, nonsensical, or very unlikely to arise from a student's attempted method.
- **3:** At least some distractors could plausibly arise from student work, but realism is uneven.
- **5:** The distractors are highly believable products of common or understandable sixth-grade reasoning errors.

This is explicitly **human judgment**, not an estimate derived from observed student response frequency.

### Clarity / teacher actionability

- **1:** The set is too unclear or internally inconsistent to guide teaching.
- **3:** A teacher could interpret and use the set with some effort.
- **5:** The misconception labels, calculations, and answers make the next teaching response immediately clear.

### Overall preference

Choose the candidate with the stronger complete distractor set across usefulness, plausibility, mathematical coherence, diversity, and teacher actionability. Use **Tie** only when neither set has a meaningful overall advantage.

## Issue flags

- **Mathematically inconsistent:** A computation does not evaluate to its stated distractor answer, uses quantities unrelated to the question, or fails to instantiate the named error. Do not flag merely because the student's method is intentionally wrong.
- **Correct-answer collision:** A distractor answer equals the displayed correct answer.
- **Duplicate:** Two distractors in the same candidate repeat the same answer or substantially the same underlying misconception.
- **Nonsense:** Content is incoherent, impossible to interpret as student work, or unusable even as an intentionally wrong method.

## Export and return

1. When all 24 items are complete, download the **JSON** export.
2. Also download the **CSV** as a readable backup.
3. Return the files to the owner without renaming fields or editing values.
4. Keep candidate identity sealed until every independent reviewer has returned final files.

Exports contain only the anonymous reviewer code, review item codes, ratings, flags, and notes. They contain no candidate identity or unblinding map.

## Interpretation limits

One reviewer provides useful **exploratory** evidence only. A publishable comparative claim needs:

- at least two independent reviewers using the same blinded pack;
- inter-rater agreement reporting;
- item-level adjudication of substantive disagreements; and
- a documented final analysis rule.

This rubric scores complete sets rather than every distractor against every registered quality gate. Therefore the scoring script reports GDR and Good@3 human proxies as **unavailable** rather than manufacturing them from set-level ratings.

## Owner-only unblinding

Only after all final exports are returned, run:

```bash
python3 -m src.score_blinded_review \
  path/to/blinded-ratings-R1.json \
  path/to/blinded-ratings-R2.json \
  --key data/eval_out/OWNER_ONLY_DO_NOT_OPEN_UNTIL_REVIEW_COMPLETE.json \
  --confirm-review-complete
```

The script computes source-level win/tie/loss rates, Wilson intervals, paired question-bootstrap intervals for rating differences, issue rates, and inter-rater agreement when multiple reviewer files are supplied.
