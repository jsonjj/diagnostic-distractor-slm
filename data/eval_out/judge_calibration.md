# Judge Calibration — Is Our Consistency Measuring Stick Trustworthy?

> The project's central claim is a consistency number (v4 53.5% vs Sonnet 94.7%, one-shot
> judge). Per the eval-maturity principle "calibrate before you ship the judge," we tested how
> well the judge agrees with ground truth. **Headline: the judge is reliable on numeric
> consistency (90%) but no better than a coin flip on non-numeric / conceptual cases (50%, with
> a 35% false-positive rate) — and that unreliable subset is where Sonnet lives, not us.**

## Two arms

### (A) Deterministic arm — 40 balanced pairs, ground truth from the engine
Pairs drawn from the buggy-procedure engine where consistency is KNOWN (half true = the
misconception's computed answer; half corrupted = a different plausible number). All numeric.

| Judge | Agreement | False-Positive | False-Negative | n |
|---|---|---|---|---|
| one-shot YES/NO | **90.0%** | 5.0% | 5.0% | 40 |
| solve-first/CoT | 87.5% | 7.5% | 5.0% | 40 |

Both clear the ≥0.8 bar on clean numeric consistency. The one-shot judge is marginally better
and has a lower false-positive rate → **one-shot is the judge of record.** (Notably the fancier
solve-first judge is *worse* — more test-time reasoning did not help here.)

### (B) Human arm — 20 real eval items, expert (human) labels
Weighted toward non-numeric answers (where no deterministic check exists). Labels were assigned
on the strict, correct criterion: *does the stated misconception UNIQUELY predict this answer?*
(the Brown & Burton diagnostic-quality bar) — not merely "is the answer plausible."

| Judge | Agreement w/ human | False-Positive | False-Negative | n |
|---|---|---|---|---|
| one-shot YES/NO | **50.0%** | **35.0%** | 15.0% | 20 (11 YES / 9 NO) |
| solve-first/CoT | 42.1% | 42.1% | 15.8% | 19 |

On real, largely non-numeric items the judge is **at chance**, and its errors are dominated by
**false positives** — it says "consistent" when the misconception does not actually produce that
answer. It rubber-stamps fluent-but-loosely-connected pairings.

## Why the two arms disagree (90% vs 50%) — and why it matters

They measure different populations:
- Numeric, unambiguous consistency → judge is excellent (90%).
- Non-numeric / conceptual / underspecified answers ("Neither", "reciprocal", answers that
  depend on a diagram, or misconceptions that only loosely predict a value) → judge is at
  chance and over-accepts (50%, 35% FP).

**The kicker — the unreliable subset is almost entirely Sonnet's, not ours:**

| Model | Numeric (judge 90% reliable) | Non-numeric (judge 50%, 35% FP) |
|---|---|---|
| v4 (ours) | **98%** | 2% |
| Sonnet | 66% | **34%** |

Our model almost never emits non-numeric distractors; Sonnet does so a third of the time. So
Sonnet's 94.7% headline was scored substantially on cases where the judge is lenient and
unreliable, while our 53.5% sits on judge-reliable numeric ground. **The true v4-vs-Sonnet
consistency gap is therefore likely smaller than 53.5 vs 94.7 — a stricter judge would deflate
Sonnet more than us.** (Direction is clear; exact size needs the split re-score, a to-do.)

## Consequences (how we report consistency from here)

1. **Report consistency split by subset**, not as one number:
   - *Numeric subset* — use the deterministic check where a computation exists (our v4/v5
     targets) or the one-shot judge (90% reliable). Trustworthy.
   - *Non-numeric subset* — flag as judge-unreliable; report but caveat. Prefer human spot-checks.
2. **One-shot judge is the judge of record** (beats solve-first on both arms).
3. **Do not take Sonnet's 94.7% at face value** — a third of it rides on the lenient subset.

## Why this is a RESULT, not a problem

This empirically supports the project's core premise (Feng 2024; DiVERT; the project's spiky
POV): **LLMs are unreliable at judging whether a wrong answer reflects a real student error.**
We showed an LLM judge agrees with expert reasoning only ~50% of the time on the nuanced cases
and over-accepts 35% of them. This is the strongest argument for the project's contribution —
making consistency **programmatically checkable** (the `computation` field + engine), which is
90% reliable, instead of judge-dependent, which collapses on hard cases.

## Limitations of this calibration
- Human arm is n=20, single labeler; indicative, not definitive.
- Deterministic arm is all-numeric by construction (that's its nature); it cannot speak to the
  non-numeric subset — which is exactly why the human arm exists.
- Next step to strengthen: a larger human set with more genuine negatives on real questions, and
  a split re-score of v4/v5/Sonnet consistency on numeric-only vs full eval.
