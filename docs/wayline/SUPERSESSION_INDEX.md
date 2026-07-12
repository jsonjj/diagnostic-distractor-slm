# Wayline Authority and Supersession Index

The Wayline documents are the controlling product direction as of 2026-07-12.

## Authoritative

- `docs/wayline/WAYLINE_MASTER_GDD.md`
- `docs/wayline/WAYLINE_LEARNING_AND_RUNTIME_SPEC.md`
- `docs/wayline/WAYLINE_ART_ANIMATION_ASSET_BIBLE.md`
- `docs/wayline/WAYLINE_HIGGSFIELD_OPENING_BRIEF.md`
- `docs/wayline/WAYLINE_ROUTE_TRIAL_UI_SPEC.md`
- `docs/wayline/WAYLINE_RUNTIME_PRIVACY.md`
- `docs/wayline/WAYLINE_MODEL_EXPORT_RUNBOOK.md`
- `docs/wayline/WAYLINE_INTERNAL_TEST_GUIDE.md`
- `docs/superpowers/plans/2026-07-11-wayline-master-roadmap.md`
- `docs/superpowers/plans/2026-07-11-wayline-learning-runtime.md`
- `docs/superpowers/plans/2026-07-11-wayline-reviewed-cache-release.md`
- `docs/superpowers/plans/2026-07-11-wayline-unity-vertical-slice.md`
- `docs/superpowers/plans/2026-07-12-wayline-fresh-assisted-route.md`
- `docs/superpowers/plans/2026-07-12-wayline-opening-and-living-atlas.md`

Implementation-status statements are controlled by the current runtime/privacy records
plus code, tests, and artifact inventory. Plan checkboxes describe intended execution;
they are not evidence that a production GGUF, cache release, or packaged runtime exists.
For export inputs, the receipt-bound bundle produced by
`services/wayline_forge/scripts/build_export_inputs.py` supersedes the model runbook's
manual ZIP command; the runbook's Colab, immutable-pin, parity, and download steps remain
authoritative.

## Preserved historical product directions

The following remain useful research and implementation references but no longer control product fiction, game mechanics, art direction, quiz cadence, or release scope:

- `GAME_DESIGN.md` and `GAME_ARCHITECTURE.md` — Legacy Glitch Rally browser prototype.
- `game/` and `data/game/` — Legacy runnable prototype and reviewed-content pipeline.
- `docs/FINAL_PRODUCT_PLAN.md`
- `docs/GLITCH_CONVOY_3D_ASSET_BRIEF.md`
- `docs/COUNTERFEIT_PROTOCOL_3D_ASSET_BRIEF.md`
- `docs/COUNTERFEIT_PROTOCOL_COMPETENCE_SYNTHESIS.md`
- `docs/superpowers/plans/2026-07-11-live-adaptive-glitch-convoy.md`
- `docs/superpowers/plans/2026-07-11-counterfeit-protocol-unity-product.md`
- `docs/superpowers/plans/2026-07-11-counterfeit-protocol-competence-engine.md`
- `docs/superpowers/plans/2026-07-11-project-kilnline-original-math-fighter.md`

## Reuse policy

- Preserve the legacy files; do not delete or silently rewrite them.
- Reuse tested ideas for strict parsing, provenance, holdout exclusion, deterministic solvers, accessibility, local persistence, and fail-closed fallback.
- Do not reuse old fiction, vehicle/rover mechanics, cardboard/ceramic character designs, immediate-feedback behavior, static-only model behavior, or old quiz lengths.
- Do not import `src/buggy_procedures.py` into the shipped product. It remains training-only.
- Convert the six approved Glitch Rally encounters through an explicit Wayline migration tool before they may enter the new cache.
