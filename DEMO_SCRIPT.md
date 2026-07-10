# Demo Video Script (3–5 min)

The one rule the assignment gives for the video: **show the model doing the thing the base model
fails to do reliably.** Everything below serves that. Keep it tight; ~4 minutes.

## What to have open before recording
- A terminal (for `python -m src.demo`)
- `TABLE.md` (the results)
- Optionally the HF page: https://huggingface.co/j2ampn/qwen3-4b-distractor-lora-v7

---

## 0:00–0:30 — The problem + the behavior spec
Say roughly:
> "A multiple-choice math question is only *diagnostic* if each wrong answer maps to a specific
> student misconception — so the option a kid picks tells the teacher exactly what to reteach.
> Writing those distractors well is skilled, slow teacher work, and prior research found even
> frontier LLMs do it inconsistently — the wrong answer often doesn't actually follow from the
> misconception it's tagged to.
> My behavior spec: given a Number-strand question + its correct answer, output exactly 3
> distractors, each with (1) a named misconception, (2) the show-the-work arithmetic that
> misconception produces, and (3) the answer it evaluates to — all numerically consistent."

## 0:30–1:00 — The bet (behavior from data)
> "I fine-tuned a small, local, open Qwen3 model (QLoRA, Unsloth) on data I generated: a
> buggy-procedure engine that produces guaranteed-consistent examples, plus real Eedi
> misconception-labeled questions verified by a teacher model. The point isn't to beat GPT on raw
> power — it's to make a tiny model reliably do this one narrow thing that a plain prompt can't."

## 1:00–2:30 — THE MONEY SHOT: base fails, tuned succeeds
Run in the terminal:
```
python -m src.demo
```
Walk through 2 examples it prints. For each, point out:
- **BASE model:** malformed / duplicate answers / no misconception mapping / no working → "the
  un-tuned model can't do it reliably."
- **TUNED model:** 3 distinct named misconceptions, each with show-the-work, `✓` where the
  computation checks out → "same base weights, but trained on my data, it now does the behavior."

Say the key line:
> "This is 'behavior from data': the base model scores near zero on the spec; after fine-tuning on
> data I generated, it produces well-formed, misconception-mapped, show-the-work distractors."

## 2:30–3:30 — The numbers (open TABLE.md)
Point at the table and say:
> "On 140 held-out real questions, scored by a calibrated LLM judge and programmatic checks:
> - The tuned model beats the base on **every** metric that matters — consistency went from ~0 to
>   ~60%, spec-pass 44→68.
> - It **matches or beats** the frontier model (Claude Sonnet) on the properties that make a
>   question diagnostic: distinct misconceptions and exactly-3.
> - Consistency reached ~60% — the first real gain past the ceiling that held across five earlier
>   data versions, and it beats the 7B prior-work state of the art at a smaller size."

Optional strong beat — the one-example head-to-head:
> "On this decimal-addition question, the frontier model actually wrote a self-contradictory
> distractor (`20 + 15 = 35` but reported `3.5`); my model's three were all internally consistent.
> The small specialist was more reliable on the exact property the project targets."

## 3:30–4:00 — Honest limitation + close
> "The honest ceiling: consistency is ~63% of the frontier model, not full parity — six data
> iterations across two model sizes show deep misconception→arithmetic consistency is a capacity
> limit for small models, not a data bug. But the thesis held: I made a tiny, private, free model
> reliably do a narrow diagnostic behavior that a prompt can't guarantee — behavior from data."

---

## Checklist to literally show on screen
- [ ] `python -m src.demo` running (base vs tuned side by side)
- [ ] TABLE.md (base-vs-tuned-vs-Sonnet numbers)
- [ ] (optional) the HF model page proving it's a real published model
- [ ] (optional) one dataset row (`data/processed/train_v7.jsonl`) to show the training data format
