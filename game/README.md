# Mathbreakers: Glitch Rally

The current executable is a dependency-free vertical slice under `prototype/`. The root
entrypoint selects the six-checkpoint, owner-reviewed `glitch-rally-v1` SLM pack and
proves the full single-player loop:

```text
choose a route → reveal the matching Glitch → fire the Patch Cannon
→ bank Proof Boosts → advance checkpoints → finish the rally → replay
```

The header tracks checkpoint progress and banked Proof Boosts. The finish card reports repaired checkpoints and Patch Cannon attempts before offering a clean run reset.

Direct `/prototype/` remains a clearly labeled three-checkpoint hand-authored graybox;
it is not SLM output.

## Run the game

From the repository root:

```bash
cd game
python3 -m http.server 4173 --bind 127.0.0.1
```

Then open `http://127.0.0.1:4173/`. The root redirect opens:

```text
http://127.0.0.1:4173/prototype/?pack=glitch-rally-v1
```

To inspect only the illustrative fixtures, open
`http://127.0.0.1:4173/prototype/`. Serving from `game/` keeps the same-origin
`content/packs/` directory available to the reviewed run.

The runtime has no package dependencies, backend, runtime model download, account,
analytics, or cross-origin network request.

## Run tests

```bash
cd game
npm test
npm run check:prototype
npm run build
```

## Content integrity

`prototype/sample-encounter.js` contains three hand-authored interaction fixtures covering fractions, decimals, and negative numbers. They are not SLM generations and cannot pass the prototype validator as production-approved content.

`prototype/content.js` also contains the separate `loadApprovedPack` boundary for
reviewed exports. It verifies the exact Python pack schema, canonical content hash,
frozen-holdout receipt, model and review provenance, chronology, family/visual registries,
nested encounter references, and numeric answer binding before returning a deeply frozen
runtime copy. Only those exact loader-returned encounter objects receive the internal
verified-approved brand used by the UI.

The root entrypoint selects the released pack with its lowercase pack ID:

```text
http://127.0.0.1:4173/prototype/?pack=glitch-rally-v1
```

With no query selection, direct `/prototype/` intentionally loads the clearly labeled
hand-authored fixtures.

The browser maps that ID to the same-origin `content/packs/glitch-rally-v1.json`, fetches
it, and passes the parsed object through
`loadApprovedPack`. Empty, repeated, path-like, URL-like, uppercase, and ambiguous IDs
are rejected. A missing, redirected, oversized, non-JSON, malformed, or unverified pack
stops on a safe error screen; it never silently falls back to prototype content.

The production content path remains:

```text
trusted gameplay question
→ Qwen3-4B + v7.1 LoRA
→ three raw counterfeits
→ hardened offline validation
→ owner review
→ approved static encounter pack
→ browser game
```

The frozen 140-item evaluation holdout must never enter a gameplay pack.

### Released pack

`content/packs/glitch-rally-v1.json` contains six reviewed encounters in this order:

```text
GR-NUM-010, GR-NUM-018, GR-NUM-024,
GR-NUM-036, GR-NUM-037, GR-NUM-055
```

- Created: `2026-07-11T15:13:47Z`
- Content hash:
  `pack:v1:940fa8804c1376bd1bfe792348f2195d49b94ffe1ac3e7dd26b67ad4f1e532cb`
- Question-bank hash:
  `626565ab322b9b0e4514c39c8df1743a39b44959c0b2e337778147855166ba38`
- Frozen holdout: 140 records,
  `47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693`

The pack contains only trusted question data, reviewed SLM counterfeits and repair copy,
and non-personal provenance hashes. It contains no reviewer alias, review notes, raw
response, rejected candidate, credential, or model weight.

## Reproduce or create a later SLM release

The first release has completed this workflow. The 60-question source bank is
`data/game/questions_v1.jsonl`. All commands below run
from the repository root and use free local CPU work except the single free-Colab T4
generation step.

First, preflight the entire bank and build the verified upload bundle:

```bash
mkdir -p data/game/work
.venv/bin/python -m src.game_content_cli prepare-batch \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --output data/game/work/questions_prepared_v1.jsonl
.venv/bin/python -m src.game_colab_bundle \
  --output glitch_rally_colab_bundle.zip
```

Open `notebooks/generate_game_candidates_colab.ipynb` in free Google Colab, select a
T4 GPU, run every cell, and upload only `glitch_rally_colab_bundle.zip` when prompted.
The notebook pins the model and adapter to resolved Hugging Face commit SHAs, loads the
model once, generates deterministically, checkpoints every question, and downloads:

- `glitch_rally_raw_candidates_v1.jsonl`
- `glitch_rally_generation_run_v1.json`

Place both downloads under `data/game/work/`, then run:

```bash
.venv/bin/python -m src.game_content_cli validate-candidates \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --candidates data/game/work/glitch_rally_raw_candidates_v1.jsonl \
  --run-manifest data/game/work/glitch_rally_generation_run_v1.json \
  --output data/game/work/validations_v1.jsonl

.venv/bin/python -m src.game_content_cli create-review-queue \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --validations data/game/work/validations_v1.jsonl \
  --output data/game/work/review_queue_v1.jsonl
```

Each queue row has exactly one immutable `review_payload` and one editable `decision`.
Do not change the payload or either hash. For every queued candidate:

- set `decision` to `approved` or `rejected`;
- use a non-identifying reviewer alias and an ISO-8601 UTC review time;
- for approval, set all four trusted-question/answer/steps/holdout checks to `true`;
- for each distractor, confirm `semantic_valid` and `age_appropriate`, select one
  listed `glitch_family_id`, and write its repair prompt and explanation;
- for rejection, explain why in `notes` and set `distractor_reviews` to `[]`.

All automatically valid candidates must receive exactly one decision. Apply them and
export the sanitized runtime pack:

```bash
.venv/bin/python -m src.game_content_cli apply-reviews \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --validations data/game/work/validations_v1.jsonl \
  --decisions data/game/work/review_decisions_owner_v1.jsonl \
  --output data/game/work/reviewed_v1.jsonl

.venv/bin/python -m src.game_content_cli export-pack \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --reviewed data/game/work/reviewed_v1.jsonl \
  --pack-id glitch-rally-v1 \
  --released-at-utc 2026-07-11T15:13:47Z \
  --output game/content/packs/glitch-rally-v1.json
```

Those arguments reproduce the first release metadata; a later pack must use its own real
UTC release time and a new stable pack ID. A failed check rejects the whole candidate;
it is never partially repaired or silently promoted. Raw and review artifacts remain
under the gitignored `data/game/work/`. Only the sanitized pack is eligible to ship.

## Build and deploy the static game

From `game/`, build the deployable artifact without installing React, Vite, or any other
package:

```bash
npm run build
```

The dependency-free builder rebuilds `game/dist/` with the root redirect,
the playable prototype HTML/CSS/JavaScript, and every released JSON file whose name
matches the public pack-ID contract and whose contents pass the full `loadApprovedPack`
validation boundary. Each passing pack is re-serialized from its verified object so
shadowed duplicate values cannot ride along in the published bytes. Malformed, raw, or
reviewer-containing JSON fails the build instead of being published. Tests and Node-only
fixtures are not shipped. The released `glitch-rally-v1.json` pack is included only after
passing that same validation boundary.

Deploy the contents of `game/dist/` to any static host, or inspect it from the
repository root:

```bash
python3 -m http.server 4173 --bind 127.0.0.1 --directory game/dist
```

Then open `http://127.0.0.1:4173/`. The older React/Vite configuration is a future
architecture option; it is not required by the current tested artifact.

Automated checks do not replace browser QA. The release still needs the Wave 4
desktop/tablet/mobile, keyboard, reduced-motion, forced-colors, real-browser
six-checkpoint playthrough, screenshot, and console pass; this README does not claim
those checks have run.

See [`../GAME_DESIGN.md`](../GAME_DESIGN.md),
[`../GAME_ARCHITECTURE.md`](../GAME_ARCHITECTURE.md), and the
[`parallel release plan`](../docs/superpowers/plans/2026-07-11-glitch-rally-parallel-release.md).
