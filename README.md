# Diagnostic Distractor SLM

Fine-tune a small open model (Qwen3-1.7B, QLoRA) to generate diagnostic distractors for middle-school "Number" math MCQs: three wrong answers, each tagged to a distinct named student misconception and numerically consistent with it, so the option a student picks reveals what to reteach.

## Behavior Spec
> Given a middle-school "Number" math question and its correct answer, the model outputs exactly three distractors, each paired with a named student misconception and numerically consistent with it (exactly the value a student making that misconception would compute). The three distractor values are all different, none equals the correct answer, and none is an arbitrary or careless wrong number.

## Approach
- **Data (the deliverable):** real Eedi "Number" seed (Kaggle) + programmatic "buggy-procedure" augmentation, hard-filtered for error->distractor consistency.
- **Train:** QLoRA on Qwen3-1.7B via Unsloth (Colab).
- **Eval (built before training):** alignment (Exact/Partial/Proportional@K), consistency %, LLM-as-judge (Claude Sonnet 5 via TrueFoundry), base-vs-tuned table.

## Structure
- `data/raw/` - Eedi Kaggle CSVs (gitignored; CC BY-NC 4.0)
- `data/processed/` - generated SFT dataset (the artifact)
- `src/` - data prep, buggy-procedure engine, generation, consistency checker, eval
- `notebooks/` - Colab training + inference

## Setup
1. `cp .env.example .env` and fill in `TFY_API_KEY` (and `HF_TOKEN` for Day 5).
2. `pip install -r requirements.txt` (local data-gen + eval).
3. Training deps install inside the Colab notebook (Unsloth).
