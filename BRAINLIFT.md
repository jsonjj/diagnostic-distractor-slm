# Brainlift — Diagnostic Distractor SLM

## Behavior Spec (the falsifiable contract)

> Given a middle-school "Number" MCQ (question + correct answer + topic), the model outputs
> **exactly 3 distractors** as a single JSON object. Each distractor (1) names a **specific
> student misconception**, (2) shows the **computation** a student with that misconception
> performs on THIS question, and (3) gives an **answer** equal to that computation, distinct from
> the other two and from the correct answer.

A stranger can mark any output pass/fail against this. It is simultaneously the data-generation
rubric, the eval criterion, and the thesis.

## Spiky POV

Prior work (Feng 2024; DiVERT 2024, UMass ML4Ed) showed LLMs generate distractors that are
mathematically valid but not reliably tied to real student errors, and named **error–distractor
consistency** their #1 unsolved failure — which they never actually measured. My bet:
**a 1.7B open model, fine-tuned on misconception-labeled data, can reliably hold this behavior**,
and consistency can be made **programmatically checkable** instead of left to a judge. The point
was never to out-smart a frontier model — it was to prove behavior comes from data.

## Did data→behavior hold? Yes for structure; a capacity ceiling for deep binding.

**The base model essentially cannot do the task.** Well-prompted, it produces duplicate answers,
options equal to the key, no misconception mapping, and no shown work (see `src/demo.py`). Judged
consistency ~0; rubric consistency 0.23.

**Fine-tuning instilled the behavior reliably** (base → v6):

| Behavior property | Base | v6 (tuned) |
|---|---|---|
| Consistency (judge) | ~0 | **50.1%** |
| Consistency (rubric 0-2) | 0.23 | ~0.45 |
| 3 distinct misconceptions | 91.4 | **99.3** (> Sonnet's 95) |
| spec_pass (well-formed) | 43.6 | 63.6 |
| shows computation | never | every distractor |

The FORMAT and the distinct-misconception discipline transferred cleanly from data — that is the
"behavior from data" thesis, confirmed. The **deep binding** (the arithmetic must be exactly what
the *named* misconception computes) plateaued at ~50% across four iterations (v1 real-heavy → v4
show-the-work → v5 broad-coverage → v6 DPO). Diagnostic: even computation-VALID distractors are
only 64% judge-consistent — the model writes plausible arithmetic that doesn't always follow from
the stated error.

**Why the ceiling is a finding, not a failure:** LookAlike (same lab, 2025) reached only 51.6%
consistency on a **7B** model with SFT+DPO. We match that at **1.7B** — 4× smaller. The binding
needs more capacity than 1.7B has; no amount of data closed it. This is exactly what the task
framing predicts: the defensible win is reliable, constrained behavior in a tiny local model, not
frontier parity.

## Contributions beyond the model
1. **Consistency made programmatically checkable** — a show-the-work `computation` field + a
   symbolic engine that recomputes each misconception. 90% reliable vs an LLM judge's 50% on hard
   cases. This operationalizes the metric DiVERT left unmeasured.
2. **Judge calibration** — empirically, the LLM consistency judge agrees with ground truth 90% on
   numeric answers but only 50% (35% false-positive) on non-numeric/conceptual ones. Direct
   evidence for the premise that LLMs judge student-error plausibility poorly.
3. **Full v1→v6 lineage** with base-vs-tuned deltas, a calibrated judge, DPO, and honest negatives.

## Where the tuned model still fails (error analysis — is it a data problem?)
- **~50% of distractors** have arithmetic that self-evaluates but doesn't reflect the named
  misconception (the binding gap). **Not a data problem** — the synthetic data is 100% consistent
  by construction and DPO on preference pairs didn't fix it. It reads as a **capacity** limit.
- **Non-numeric/conceptual distractors** (answers like "Neither", "reciprocal") can't carry a
  computation; the model rarely produces consistent ones here. Scoped out; needs real
  student-response data (Eedi's private pick-rate dataset) to do properly — top future work.
- **Alignment to the exact teacher answer** is low (Prop@3 15 vs Sonnet 39) — but this is a
  known-flawed proxy (Feng: many human distractors are placeholders), deliberately not optimized.

## Verdict
Behavior-from-data **held** for the learnable structure of the task and produced a tiny, private,
local model that reliably does something the base model can't — decisively beating base on every
spec dimension and beating a frontier model on distinct-misconception coverage. The deep numeric
binding is a documented 1.7B capacity ceiling, consistent with 7B prior work.
