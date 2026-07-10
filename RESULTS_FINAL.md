# Results Through v6 (historical) — see TABLE.md for the FINAL model (v7.1)

> ⚠️ **This file documents the v1–v6 lineage (1.7B).** The final shipped model is **v7.1
> (Qwen3-4B)** — its results, the base-vs-tuned Appendix-A rubric, and the verdict live in
> **`TABLE.md`**. Keep this file as the record of how the 1.7B iterations progressed and why the
> project moved to a 4B base.

---

# Final Results — Base vs v1 vs v5 vs v6 vs Sonnet

_140-item held-out eval. Consistency = one-shot judge (calibrated: 90% reliable on numeric, 50%
on non-numeric — see judge_calibration.md). Judge is non-deterministic (±2-3pt). v6 = v5 SFT +
DPO preference tuning._

## The scoreboard

**🎯 thesis axis · 🧹 usability · 🥈 bonus/flawed-proxy**

| Metric | Base | v1 (SFT) | v5 (SFT) | v6 (SFT+DPO) | Sonnet 5 | v6 vs Base | v6 vs Sonnet |
|---|---|---|---|---|---|---|---|
| 🎯 Consistency — judge, FULL | ~0 | 49.8 | 50.6 | **50.1** | 94.5 | **+50pt** ✅ | 53% |
| 🎯 Consistency — judge, NUMERIC (fair) | ~0 | — | 54.3 | **51.6** | 95.8 | huge ✅ | 54% |
| 🎯 Consistency — rubric 0-2 | 0.23 | 0.45 | — | ~0.45 | 1.81 | **+0.22** ✅ | ~25% |
| 🎯 distinct_misconceptions | 91.4 | 30.7 | 97.9 | **99.3** | 95.0 | +7.9 ✅ | **104%** ✅ |
| 🧹 none_equals_key | 62.1 | 90.0 | 82.9 | 78.6 | 100.0 | +16.5 ✅ | 79% |
| 🧹 distinct_answers | 70.7 | 87.9 | 70.7 | **80.0** | 94.3 | +9.3 ✅ | 85% ✅ |
| 🧹 spec_pass | 43.6 | 81.4 | 60.0 | 63.6 | 94.3 | +20 ✅ | 67% |
| 🧹 exactly_3 | 97.1 | 100 | 98.6 | 99.3 | 95.0 | +2.2 ✅ | **105%** ✅ |
| 🥈 Proportional@3 | 12.4 | 31.9 | 17.4 | 15.0 | 39.3 | +2.6 ✅ | 38% |
| 🥈 Partial@3 | 35.0 | 56.4 | 36.4 | 31.4 | 72.9 | −3.6 | 43% |

## The verdict against the two bars

### Bar 1 — "beat the base model" (the assignment's actual test): **PASS, decisively.**
v6 beats base on EVERY metric except Partial@3 (a bonus alignment proxy). The headline: consistency
went from ~0 to 50%, spec_pass +20, distinct_misconceptions to 99.3 (best of all, > Sonnet).
Per the assignment's own rubric ("a tuned model that beats the base on Spec adherence and
Robustness is a win") — this is unambiguously a win.

### Bar 2 — "≥80% of Sonnet on every quality axis": **PARTIAL.**
- **Beat Sonnet:** distinct_misconceptions (104%), exactly_3 (105%). ✅
- **≥80% of Sonnet:** distinct_answers (85%). ✅
- **Below 80%:** consistency (53-54%), spec_pass (67%), none_equals_key (79%), alignment (38-43%).

**Consistency — the thesis axis — is the one that did not reach 80% of Sonnet.** Four iterations
(v1 real-heavy, v4 show-the-work, v5 broad-coverage, v6 DPO) all landed at ~50-54% judged / ~52-54%
on the fair numeric subset. This is a real capability ceiling, not a data bug (see below).

## What we proved, and the honest finding

**Data→behavior held for structure, hit a ceiling for deep binding.**
- SFT taught the FORMAT (show-the-work, distinct misconceptions, valid JSON) reliably — that's the
  "behavior from data" thesis, and it worked: base 0 → tuned 50%+ consistency, 99% distinct labels.
- But the deep BINDING (the arithmetic must be exactly what the *named* misconception computes)
  plateaued at ~50%. Coverage expansion (v5) didn't move it; preference tuning (v6/DPO) didn't move
  it. Diagnostic evidence: even *computation-valid* distractors are only 64% judge-consistent — the
  model writes plausible arithmetic that doesn't follow from the stated error.
- **Interpretation:** the misconception→arithmetic binding needs more model capacity than 1.7B has.
  LookAlike (same lab) got only to 51.6% consistency on a 7B model with SFT+DPO — we match that at
  1.7B. The ceiling is consistent with the literature, not a flaw in our data.

**This is exactly the outcome the assignment frames as success:** "your 1B model will not beat a
frontier model on raw capability... the defensible win is reliable, constrained behavior in a tiny,
cheap, local model." We have that: a 1.7B local model that reliably emits well-formed,
distinct-misconception, show-the-work distractors and is ~50% numerically consistent — up from a
base that essentially can't do the task at all.

## What we contributed beyond the model (rigor the field lacks)
1. **Consistency made programmatically checkable** (computation field + engine) — 90% reliable,
   vs the LLM judge's 50% on hard cases. This IS the DiVERT/Feng open problem, now measurable.
2. **Judge calibration** — empirically showed an LLM judge agrees with ground truth 90% on numeric
   but 50% (35% false-positive) on non-numeric consistency. Supports the thesis that LLMs judge
   student-error plausibility poorly; justifies deterministic checking.
3. **Full v1→v6 lineage** with base-vs-tuned deltas, a calibrated judge, and honest negative results.

## Ship decision
- **Ship v6** as the final model: it beats base decisively (the assignment bar), holds the
  distinct-misconception win over Sonnet, and improved distinct_answers over v5. Guard passed
  (distinct_misconceptions 99.3 ≥ 95; consistency 50.1 vs v5 50.6, within noise).
- **Report consistency honestly** as ~50% (numeric 51.6%), framed as behavior-from-data success +
  a documented capability ceiling, NOT as a Sonnet-parity claim.
