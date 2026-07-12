# Diagnostic Distractor SLM

Fine-tune a small open model (final: Qwen3-4B, QLoRA) to generate diagnostic distractors for middle-school "Number" math MCQs: three wrong answers, each tagged to a distinct named student misconception and numerically consistent with it, so a selected option creates a hypothesis to probe and can inform reteaching.

## Wayline: The Broken Meridian

`unity/Wayline/` and `services/wayline_forge/` contain the current Mac-first game
vertical slice. Wayline is an original 2.5D science-fantasy weapon fighter whose
post-battle Route Trials use the final SLM to propose distractors. Trusted code owns
questions, scoring, verification, adaptation, and progression; raw or unverified model
output never reaches the player.

The implemented slice includes deterministic graybox combat, the accessible atlas trial
flow, exact batch-level wrong-count review, a fresh sealed assisted route, local campaign
and cosmetic save state, server-owned boss gates, Second Wind, and fail-closed SQLite
recovery. The Python 3.12 hash lock, PyInstaller specification, local arm64 onedir sidecar
build, validated production composition root, and fail-closed package assembler/auditor
are also implemented. That onedir build is only an intermediate artifact: the production
GGUF, pinned Apple-Silicon `llama-server`, production model manifest, artifact-specific
descriptor receipt, reviewed-cache release, assembled package manifest, bespoke rigged
characters, and a clean packaged live smoke remain release gates rather than implied
completed assets.

See [`docs/wayline/WAYLINE_MASTER_GDD.md`](docs/wayline/WAYLINE_MASTER_GDD.md),
[`docs/wayline/WAYLINE_LEARNING_AND_RUNTIME_SPEC.md`](docs/wayline/WAYLINE_LEARNING_AND_RUNTIME_SPEC.md),
and [`docs/wayline/SUPERSESSION_INDEX.md`](docs/wayline/SUPERSESSION_INDEX.md).

### Play the current Unity development slice

1. Install Unity Editor `6000.3.11f1` with macOS build support.
2. In Unity Hub, open `unity/Wayline` as a project.
3. Open `Assets/_Game/Scenes/Arena_Graybox.unity`.
4. Press Play, choose `Enter Valuehold`, then `Face the Surveyor`.
5. In combat, use `A/D` or the arrow keys to move, `J` for light attack,
   `K` for heavy attack, `L` to parry, `Left Shift` to guard, and `Space` to dodge.
6. After winning, answer every Route Trial item and choose `Certain`, `Leaning`,
   or `Guessing`. Submit once, use the single whole-batch review when offered,
   read the final methods, then return to the map from the reward screen.

The current composed acceptance route is deliberately labeled
`DETERMINISTIC LOCAL ACCEPTANCE DATA — NOT LIVE SLM`. That content is compiled only
for the Unity Editor or a development build. A non-development build fails closed until
the pinned GGUF, reviewed cache, `llama-server`, and final package receipts are present.

## Preserved legacy: Mathbreakers Glitch Rally

`game/` contains the earlier released six-checkpoint browser rally built around the SLM's
behavior. Each reviewed generated answer becomes a counterfeit route, its computation
powers a rival Glitch attack, and its misconception selects the repair challenge. The
root game entrypoint opens the owner-reviewed `glitch-rally-v1` pack by default; direct
`/prototype/` remains an explicitly labeled three-checkpoint hand-authored fixture.

The released pack contains `GR-NUM-010`, `GR-NUM-018`, `GR-NUM-024`, `GR-NUM-036`,
`GR-NUM-037`, and `GR-NUM-055`. It was generated offline with the final v7.1 adapter,
validated against the exact frozen 140-question holdout receipt, hash-bound to the owner
review, and sanitized before entering `game/content/packs/`. No model or external AI
service runs in the browser. Browser QA remains a separate release gate and is not
claimed by this documentation update.

It remains runnable and preserved for provenance, but it no longer controls the product
fiction, mechanics, quiz cadence, or release scope. See [`GAME_DESIGN.md`](GAME_DESIGN.md),
[`GAME_ARCHITECTURE.md`](GAME_ARCHITECTURE.md), and [`game/README.md`](game/README.md).

## Behavior Spec
> Given a middle-school "Number" math question and its correct answer, the model outputs exactly three distractors, each paired with a named student misconception and numerically consistent with it (exactly the value a student making that misconception would compute). The three distractor values are all different, none equals the correct answer, and none is an arbitrary or careless wrong number.

## Approach
- **Data (the deliverable):** real Eedi "Number" seed (Kaggle) + programmatic "buggy-procedure" augmentation, hard-filtered for error->distractor consistency.
- **Train:** QLoRA on Qwen3-4B via Unsloth (Colab).
- **Eval (built before training):** alignment (Exact/Partial/Proportional@K), consistency %, LLM-as-judge (Claude Sonnet 5 via TrueFoundry), base-vs-tuned table.

## Structure
- `data/raw/` - Eedi Kaggle CSVs (gitignored; CC BY-NC 4.0)
- `data/processed/` - generated SFT dataset (the artifact)
- `src/` - data prep, buggy-procedure engine, generation, consistency checker, eval
- `notebooks/` - Colab training + inference
- `game/` - Mathbreakers design, reviewed static release, and illustrative fixture
- `services/wayline_forge/` - fail-closed local learning runtime and Mac sidecar tooling
- `unity/Wayline/` - current Wayline combat, trial UI, campaign, and save vertical slice
- `docs/wayline/` - authoritative game, runtime, privacy, art, and release specifications

## Setup
1. `cp .env.example .env` and fill in `TFY_API_KEY` (and `HF_TOKEN` for Day 5).
2. `pip install -r requirements.txt` (local data-gen + eval).
3. Training deps install inside the Colab notebook (Unsloth).
