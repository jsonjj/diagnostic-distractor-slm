# Mathbreakers: Glitch Rally Vertical Slice — Implementation Record

**Goal:** Prove the complete single-player loop in a static sixth-grade game: choose a
route, reveal a convincing mathematical counterfeit, trace its computation, select a
repair strategy, finish a three-checkpoint rally, and replay.

**Canonical runtime:** Dependency-free browser modules in `game/prototype/`. The earlier
Vite/React/TypeScript direction was not implemented and is no longer a prerequisite. A
future framework port is optional and must preserve the same content and state-machine
contracts.

**Content boundary:** The playable records are clearly labeled hand-authored fixtures.
Real SLM content may enter only as a sanitized pack produced by the implemented
generation, validation, owner-review, and export workflow.

## Current status

| Area | Implemented state |
|---|---|
| Product loop | Three checkpoints, run progress, banked Proof Boosts and repair attempts, finish summary, and clean replay |
| Encounter logic | Pure `choose → counterbreak → resolved` reducer plus whole-run reducer |
| Correct-route behavior | Reveals the reviewed `featuredCounterfeitId`; wrong routes reveal the exact selected counterfeit |
| Choice fairness | Stable ID-based answer and repair permutations avoid a fixed correct position |
| Presentation | Escaped HTML, papercraft four-lane Proof Road with selected-lane car travel, explicit route outcomes, phase summaries, nine family accents, responsive layout, native buttons, focus restoration, persistent live status, and reduced-motion CSS |
| Prototype trust | Three hand-authored fixtures cannot pass as approved production content |
| Release trust | Strict browser loader validates the exact Python pack shape, nested references, provenance, frozen-holdout receipt, and Web Crypto content hash |
| Startup wiring | No query selects the labeled prototype; `?pack=<id>` fetches one same-origin sanitized pack and fails closed with no fixture fallback |
| Provenance UI | Only exact loader-verified encounter objects receive `Glitch Forge · reviewed SLM`, `Reviewed SLM run complete`, and `SLM-powered checkpoint` labels; clones and fixtures remain prototype-labeled |
| Source content | 60 original, solver-checked sixth-grade Number questions |
| SLM generation | Verified free-Colab bundle/notebook with pinned revisions, deterministic decoding, atomic checkpointing, resume checks, and run manifest |
| Offline release pipeline | Strict candidate validation, hash-bound review queue, explicit owner confirmations, and sanitized deterministic export |
| Real pack | Not yet generated or reviewed; no real pack is checked in |
| Visual browser QA | Not completed in this session because the managed environment did not approve the local server required by the in-app browser |

The earlier handoff baseline was **81 passing Python tests and 66 passing Node tests**.
After the final runtime, UI, and builder work, fresh runs passed **81 Python tests** and
**109 Node tests** (101 prototype/runtime plus eight static-builder tests, including
nested filename cases). These are dated
verification snapshots, not a promise that counts never change; a release requires
fresh zero-failure runs.

## Implemented file map

### Game runtime

- `game/index.html` — redirects a static deployment to the executable.
- `game/build-static.mjs` — atomically assembles the dependency-free `game/dist/`
  allowlist and released pack files.
- `game/build-static.test.js` — proves the artifact is playable, complete, and excludes
  test-only files.
- `game/prototype/index.html` — browser shell.
- `game/prototype/app.js` — source boot, events, rendering, and focus.
- `game/prototype/bootstrap.js` — narrow same-origin pack selection, bounded fetch, strict
  loader handoff, and safe boot error.
- `game/prototype/runtime-effects.js` — transition-gated motion, focus targets, and one
  persistent live announcement.
- `game/prototype/encounter.js` — pure encounter and run reducers.
- `game/prototype/view-model.js` — stable presentation ordering and display state.
- `game/prototype/render.js` — escaped markup for the stage, choices, trace, repair, and
  run summary.
- `game/prototype/content.js` — separate prototype and approved-pack trust boundaries.
- `game/prototype/sample-encounter.js` — three explicitly non-release fixtures.
- `game/prototype/styles.css` — papercraft visual system, responsive layout, and motion.
- `game/prototype/*.test.js` — reducer, view-model, renderer, schema, pack, and full-flow
  tests.

### Content authoring

- `data/game/questions_v1.jsonl` — 60 original trusted questions.
- `src/game_candidate_generation.py` — deterministic, resumable candidate generation.
- `src/game_colab_backend.py` — pinned Unsloth model/adapter backend.
- `src/game_colab_bundle.py` — reproducible verified Colab upload bundle.
- `notebooks/generate_game_candidates_colab.ipynb` — free-T4 generation workflow.
- `src/game_content.py` — bank validation, strict candidate gate, review binding, and
  pack export.
- `src/game_content_cli.py` — `prepare-batch`, `validate-candidates`,
  `create-review-queue`, `apply-reviews`, and `export-pack` commands.
- `data/game/work/` — gitignored raw, validation, and review artifacts.
- `game/content/packs/` — sanitized releases only.
- `tests/test_game_*.py` — Python trust-pipeline coverage.

`game/package.json` now makes `node build-static.mjs` the canonical build. Its Vite
development scripts and React/KaTeX/Zod declarations, plus `game/vite.config.ts`,
`game/tsconfig.json`, and `game/src/test/setup.ts`, remain unused framework-era
scaffolding. There is no React source application, `package-lock.json`, or Vite
production build in the implemented slice.

## What the vertical slice proves

### Gameplay

The current run crosses three themed checkpoint labels—Fraction Foundry, Decimal Docks,
and Integer Iceway—to prove that one loop works across several sixth-grade Number
topics. This is not a three-district campaign: there is no district map, progression
tree, boss, content catalog, Field Guide, garage, or persistent world restoration.

At each checkpoint:

1. four neutral routes are shown in a stable non-positional order;
2. the player selects and explicitly commits one route;
3. the selected counterfeit, or the encounter's reviewed featured counterfeit after a
   correct choice, becomes the rival;
4. its misconception and computation are exposed;
5. the player chooses a repair strategy and may retry without punishment;
6. the trusted proof repairs the road;
7. the run reducer advances and banks totals exactly once.

### SLM integration

The SLM is central to the intended release even though it never runs at play time:

```text
distractor answer → counterfeit route and rival result
computation → Glitch attack trace
misconception → authored Glitch family
reviewed repair → Patch Cannon strategy
```

Prototype fixtures demonstrate those mappings but never claim SLM provenance. Approved
packs carry the model, adapter, code, prompt, candidate, validation, review, and holdout
bindings necessary for the UI to identify their three counterfeits as reviewed offline
SLM output.

The default `/prototype/` URL loads only fixtures. A release URL explicitly selects a
same-origin pack, for example `/prototype/?pack=glitch-rally-v1`. If that selected file
is missing or fails any response, schema, reference, provenance, or content-hash check,
startup stops at a safe error screen rather than falling back to fixtures.

### Safety and accessibility

- no default timer, lives, public score, streak pressure, or progress loss;
- one answer is never treated as a permanent learner diagnosis;
- native keyboard-operable buttons and visible focus;
- large targets and color-independent selected/fault/repaired states;
- escaped bounded content and no executable model text;
- reduced-motion behavior that preserves state meaning;
- no account, child data collection, analytics, ads, chat, payment, or network model call.

## Completed authoring pipeline

The content workflow is implemented end to end in code:

```text
60-question original bank
→ exact 140-row holdout gate
→ verified Colab bundle
→ pinned deterministic v7.1 generation
→ raw candidates + bound run manifest
→ fail-closed validation
→ immutable review payload + owner decisions
→ revalidated reviewed records
→ deterministic sanitized browser pack
```

The holdout gate requires this exact receipt:

```text
47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693
```

The validator rejects malformed JSON, duplicate keys, unsafe Unicode/control content,
wrong schemas, duplicate/equivalent answers, ungrounded or inconsistent computations,
stale code/model/run provenance, and holdout violations. Approval additionally requires
the owner to confirm trusted question, answer, repair steps, and holdout origin and to
review every surviving distractor for semantics, age appropriateness, family, and repair
copy. There is no silent repair or partial approval.

## Scope boundary

The scopes are intentionally different:

- **Current technical vertical slice:** three fixture checkpoints with three district
  themes, used to prove cross-topic gameplay and run progression.
- **First content-complete MVP:** one polished district, expected to use roughly 15–20
  owner-approved SLM encounters plus the supporting Field Guide/reward content chosen
  after playtesting.
- **Later expansion:** additional full districts, bosses, garage cosmetics, persistent
  restoration, more encounter variants, and richer strategy gadgets.

The current slice excludes live inference, a backend, runtime GPU use, accounts, cloud
saves, teacher dashboards, analytics, multiplayer, 3D/physics driving, paid assets,
unreviewed procedural content, and any gameplay use of the frozen research holdout.

## Remaining work

### Owner content release

The code cannot create a genuine release pack without human authority. The owner must:

1. build/upload the verified bundle and run the notebook on a free Colab T4;
2. place the downloaded candidates and run manifest in `data/game/work/`;
3. run validation and create the review queue;
4. explicitly approve or reject every automatically valid candidate;
5. apply decisions and export the sanitized pack into `game/content/packs/`;
6. verify the game against that exact exported pack.

Until those steps occur, all playable content remains prototype-only.

### Visual verification

Serve `game/` on `127.0.0.1` and use a real browser to verify:

- a complete correct-route and wrong-route run;
- approved-pack loading and fail-closed pack errors;
- keyboard order, focus restoration, and visible focus;
- reduced-motion emulation;
- desktop and tablet layouts with no clipping or overlap;
- neutral pre-commit choices and no position-based correctness tell;
- zero unexpected console errors or external requests.

This pass remains open because the managed local-server approval was unavailable in the
current session, not because automated checks can substitute for it.

### Optional product expansion

After real-content playtesting, decide whether to add the planned Field Guide, strategy
gadgets, boss encounter, garage rewards, and local persistence. A React port is also an
optional maintenance choice, not a blocker for static release.

## Verification commands

Run from the repository root unless noted:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python -m compileall -q src tests

cd game
npm run test:prototype
npm test
npm run check:prototype
npm run build
node --check prototype/content.js
node --check prototype/encounter.js
node --check prototype/render.js
node --check prototype/view-model.js
```

Also validate the notebook JSON and code cells, scan the runtime for remote/network or
secret-bearing code, and run `git diff --check`. Report fresh results rather than relying
only on the snapshot counts above.
