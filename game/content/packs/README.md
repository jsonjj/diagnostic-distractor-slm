# Released encounter packs

This directory is the only runtime destination for sanitized encounter packs produced
by:

```bash
.venv/bin/python -m src.game_content_cli export-pack ...
```

Do not hand-author or copy intermediate JSON here. Raw candidates, run manifests,
validation reports, review queues, reviewer aliases, decisions, and rejection notes
belong in gitignored `data/game/work/`.

`game/prototype/content.js` is the browser trust boundary. It accepts only the exact
`glitch-rally-pack-v1` contract, verifies the fixed 140-row holdout assertion, nested
references and provenance, and the canonical `pack:v1:` SHA-256 content hash before
returning deeply frozen encounters. Passing that loader is defense in depth; it does not
replace offline validation or owner review.

The released source pack is `glitch-rally-v1.json`. It contains six owner-reviewed SLM
encounters:

```text
GR-NUM-010, GR-NUM-018, GR-NUM-024,
GR-NUM-036, GR-NUM-037, GR-NUM-055
```

Its canonical content hash is
`pack:v1:940fa8804c1376bd1bfe792348f2195d49b94ffe1ac3e7dd26b67ad4f1e532cb`.
The question-bank hash is
`626565ab322b9b0e4514c39c8df1743a39b44959c0b2e337778147855166ba38`,
and the excluded frozen holdout remains bound to the exact 140-record receipt
`47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693`.

Serve `game/` and open the root URL; `game/index.html` selects:

```text
/prototype/?pack=glitch-rally-v1
```

The pack ID is mapped only to the same-origin `content/packs/` directory. Missing,
redirected, non-JSON, oversized, malformed, or unverified selected packs stop on a safe
error screen; the app does not silently substitute prototype fixtures. Opening direct
`/prototype/` with no `pack` query remains the explicitly labeled hand-authored
prototype.

For deployment, `cd game && npm run build` uses the dependency-free static builder to
copy released pack files and the runtime allowlist into `game/dist/`. The browser still
performs full pack verification when a release URL is opened.

The pack binds `unsloth/Qwen3-4B-bnb-4bit` at revision
`cad0bedfdd862093a12af478cb974ab2addd0e0a` and
`j2ampn/qwen3-4b-distractor-lora-v7` at revision
`dd30dcea2755b7a2659faa908714e31335349408`. Generation and review remain offline;
the browser downloads neither model and calls no AI service.

Browser QA is a separate Wave 4 gate. No responsive-layout, keyboard, reduced-motion,
forced-colors, screenshot, or console result is claimed here until that pass completes.
