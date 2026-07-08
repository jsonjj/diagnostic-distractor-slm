# Results Through v5 — The Honest Scoreboard + Root-Cause Analysis

_140-item held-out eval. Consistency numbers use the one-shot judge (calibrated: 90% reliable
on numeric answers, 50% on non-numeric — see judge_calibration.md). Judge is non-deterministic;
±2-3pt run-to-run variance._

## The full table

**🎯 = must beat/match Sonnet (thesis) · 🥈 = compete (bonus, flawed proxy) · 🧹 = must be usable**

| Metric | Base | v1 | v4 | v5 | Sonnet 5 | v6 target (≥80% Sonnet) |
|---|---|---|---|---|---|---|
| 🎯 Consistency — judge, FULL | ~0.23r | 49.8 | 53.5/52.6 | 50.6/53.3 | 94.5 | **≥76, goal >Sonnet** |
| 🎯 Consistency — judge, NUMERIC (fair) | — | — | 53.9 | 54.3 | **95.8** | **≥77** |
| 🎯 Consistency — free, hardened | n/a | n/a | 72.6 | 73.3 | n/a | ≥90 |
| 🎯 distinct_misconceptions | 91.4 | 30.7 | 95.7 | **97.9** | 95.0 | hold ≥95 (already win) |
| 🧹 none_equals_key | 62.1 | 90.0 | 77.9 | 82.9 | 100.0 | ≥95 |
| 🧹 distinct_answers | 70.7 | 87.9 | 71.4 | 70.7 | 94.3 | ≥90 |
| 🧹 spec_pass | 43.6 | 81.4 | 57.9 | 60.0 | 94.3 | ≥85 |
| 🥈 Proportional@3 | 12.4 | 31.9 | 16.2 | 17.4 | 39.3 | ≥31 (80%) |
| 🥈 Partial@3 | 35.0 | 56.4 | 32.9 | 36.4 | 72.9 | ≥58 |
| exactly_3 | 97.1 | 100 | 97.1 | 98.6 | 95.0 | ✓ |
| numeric composition | — | — | 98% | 95% | 66% | — |

## The verdict, without spin

- **Consistency (the thesis) is STUCK.** v1 49.8 → v4 53.5 → v5 50.6. Three dataset strategies
  (real-heavy, show-the-work, broad-coverage+de-skew) all land at ~50-54%.
- **The fair comparison is worse, not better, than the headline.** On the judge-reliable
  numeric subset, Sonnet = **95.8%**, us = **54%**. The hoped-for "Sonnet is inflated by
  unreliable non-numeric cases" did NOT hold — Sonnet is 95.8% even on numeric. The ~42pt gap
  is real.
- **What we DO win:** distinct_misconceptions (97.9 > Sonnet 95.0) — solidly, and it's a real
  diagnostic property. Plus cost/private/local (inherent).
- **Data is not the lever.** Coverage expansion moved coverage but not consistency → the
  bottleneck is not "what topics the model saw," it's "the model never learned to bind a named
  misconception to the exact arithmetic it produces."

## Root-cause analysis (v5 wrong-output taxonomy, free-metric)

Of 415 predicted distractors:
| Mode | % | Nature |
|---|---|---|
| PASS (well-formed, grounded, self-consistent) | 74.2% | good arithmetic... |
| lhs ≠ its own answer (e.g. `19+(10+10-1)=48`, really 47) | 10.4% | arithmetic sloppiness |
| degenerate / no operator | 10.4% | non-computations |
| ungrounded / fabricated operands (`24-40=-16` for `0.2÷0.4`) | 5.1% | invented arithmetic |

**Two-layer failure:**
1. **Arithmetic garbage (~24%)** — degenerate, ungrounded, or doesn't self-evaluate. Mechanically
   catchable: a runtime deterministic check would reject every one.
2. **The deeper ~24pt gap** between free-pass (74%) and judge-pass (~50%): even *clean,
   self-consistent* computations often DON'T reflect what the named misconception would compute
   (e.g. "subtract the digits" is not a real division error). Plain SFT taught the model to emit
   valid-looking arithmetic, **not to bind arithmetic to the stated error.**

## Why this points to a MECHANISM change for v6 (not more data)
- Layer 1 → **inference-time self-check**: model generates, our 90%-reliable deterministic
  checker verifies each computation, failed distractors are regenerated. Eliminates the ~24%
  garbage with zero retraining. Expected lift alone: ~50% → ~65-70%.
- Layer 2 → **preference optimization (DPO)**: train on (consistent) vs (model's own
  inconsistent) computation pairs — precisely LookAlike's method (same lab, 45.6→51.6 on 7B).
  Teaches the binding SFT never did.
- Both preserve v5's wins (distinct_misconceptions, well-formedness gains) — they add a filter
  and a preference signal rather than relearning from scratch. Warm-start from the v5 adapter.

_Full v6 plan: see the plan file._
