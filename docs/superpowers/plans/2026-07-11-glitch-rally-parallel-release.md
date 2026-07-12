# Glitch Rally Parallel Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the owner's confirmed six-approve/thirteen-reject review into a verified SLM content pack, make that pack the default game entrypoint, and complete release and browser QA without concurrent agents editing the same file.

**Architecture:** Work proceeds in dependency-ordered waves. Inside each wave, up to three subagents run concurrently because this environment supports four active agents total, including the root agent. Every path has exactly one writer; read-only agents return reports in messages, and the root agent alone performs convergence checks between waves.

**Tech Stack:** Python 3 review/export CLI, JSONL and JSON content contracts, browser-native JavaScript modules, Node's built-in test runner, dependency-free static builder, in-app browser QA.

**Execution status (2026-07-11):** Waves 1–3 and Convergence Gate 3 are complete. They
produced the six-encounter `glitch-rally-v1` source pack, made it the default root launch,
and passed the complete automated convergence suite. Direct `/prototype/` remains the
labeled hand-authored fixture. Wave 4's pure approved-content playthrough and final
privacy/offline/repository-scope audits are complete. Real-browser QA is blocked by
sandbox localhost bind/escalation failure and browser URL-policy rejection of direct
`file://` navigation. The in-app browser itself connected successfully, but it could not
reach a served game; viewport, keyboard, console, animation, and screenshot checks remain
pending.

Tasks 1–9 below now record the completed historical execution. Read-only auditor outputs
were returned through the execution handoff/current run rather than added as separate
report files.

## Global Constraints

- Every agent must read `/Users/jonat/Projects/diagnostic-distractor-slm/PROJECT_CONTEXT.md` from top to bottom before any action.
- Agents share one filesystem and technically can see the whole repository; isolation is enforced through explicit write allowlists, not separate OS permissions.
- One writer per file or directory. All unlisted paths are read-only.
- No agent may edit `PROJECT_CONTEXT.md`, `dataset_sample.jsonl`, the raw Colab artifacts, the frozen holdout, or another agent's owned path.
- No agent may commit, push, reset, clean, delete, or rewrite git history.
- The owner's confirmed reviewer alias is `owner-01`.
- Approve exactly `GR-NUM-010`, `GR-NUM-018`, `GR-NUM-024`, `GR-NUM-036`, `GR-NUM-037`, and `GR-NUM-055` unless a convergence audit finds a concrete mismatch.
- Reject the other thirteen rows in `review_queue_v1.jsonl` with empty `distractor_reviews` and their proposal reason.
- Approved rows must explicitly set all four trusted question, answer, steps, and holdout confirmations to `true`; rejected rows set those four fields to `false` because the owner attestation only covered approvals.
- Only an owner-reviewed, hash-bound, sanitized pack may enter `game/content/packs/` or `game/dist/`.
- Browser gameplay must not call Hugging Face or run the SLM live.

## Concurrency and ownership model

At most three subagents run beside the root agent. Tasks in the same wave start together and never write overlapping paths. The root waits for all three, inspects their outputs, and opens the next wave only after the gate passes.

| Wave | Agent | Writable paths | Read access | Output |
|---|---|---|---|---|
| 1 | Decision materializer | `data/game/work/review_decisions_owner_v1.jsonl` only | Proposal, immutable pending queue, validations, trusted questions, confirmed user message | Completed hash-bound decisions |
| 1 | Review-contract auditor | None | Proposal, queue, validation and review code | Message-only pass/fail report |
| 1 | Runtime preflight auditor | None | `game/`, game docs, current tests | Message-only baseline report |
| 2 | Pack release executor | `data/game/work/reviewed_v1.jsonl`, `game/content/packs/glitch-rally-v1.json` only | Confirmed decisions and immutable pipeline inputs | Reviewed artifact and sanitized pack |
| 2 | Default-entrypoint implementer | `game/index.html`, `game/build-static.test.js` only | Current pack-selection contract and tests | Root launch redirects to reviewed pack |
| 2 | Release-boundary auditor | None | Builder, loader, exporter, entrypoint contract | Message-only security checklist |
| 3 | Pack trust auditor | None | Exported pack, Python/JS validators | Message-only provenance/schema report |
| 3 | Static integration verifier | `game/dist/` only, through `npm run build` | Game runtime and exported pack | Fresh deployable artifact and test report |
| 3 | Documentation synchronizer | `README.md`, `GAME_ARCHITECTURE.md`, `GAME_DESIGN.md`, `game/README.md`, `game/content/packs/README.md`, this plan | Immutable final pack and independently run test output | Accurate final handoff docs |
| 4 | Browser QA operator | Screenshot files under the Codex visualization writable root only | Running localhost game | Desktop/tablet/mobile QA report and screenshots |
| 4 | Approved-content playthrough auditor | None | Pack and pure reducer/renderer | Message-only six-encounter playthrough report |
| 4 | Release privacy/security auditor | None | `game/dist/`, source pack, git status | Message-only leak/offline/final-risk report |

---

## Wave 1 — Confirmed decision preparation

### Task 1: Materialize the owner's review decisions

**Files:**
- Create: `data/game/work/review_decisions_owner_v1.jsonl`
- Read only: `data/game/work/review_proposal_v1.md`
- Read only: `data/game/work/review_queue_v1.jsonl`
- Read only: `data/game/work/validations_v1.jsonl`

**Interfaces:**
- Consumes: the owner's explicit confirmation in the current conversation and the exact pending decision objects already embedded in `review_queue_v1.jsonl`.
- Produces: a separate 19-row decisions file in original queue order, with six `approved`, thirteen `rejected`, and zero `pending` decisions. The pending queue remains immutable so the concurrent auditor cannot observe a partial edit.

- [x] **Step 1: Re-read immutable inputs**

Verify the proposal contains six approval headings, thirteen rejection bullets, and nineteen unique `GR-NUM-*` IDs:

```bash
rg '^### GR-NUM-' data/game/work/review_proposal_v1.md
rg '^- `GR-NUM-' data/game/work/review_proposal_v1.md
rg -o 'GR-NUM-[0-9]{3}' data/game/work/review_proposal_v1.md | sort -u | wc -l
```

Expected: 6 approval headings, 13 rejection bullets, 19 unique IDs.

- [x] **Step 2: Record one review timestamp**

Run:

```bash
REVIEWED_AT_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "$REVIEWED_AT_UTC"
```

Insert the exact printed value, not the variable name, into all nineteen decisions. It
must be later than every candidate's `generated_at_utc`.

- [x] **Step 3: Patch only the decision objects**

Use `apply_patch` to create `review_decisions_owner_v1.jsonl` from the pending queue;
preserve every `review_payload`, `review_payload_hash`, row order, candidate hash, and
validation hash. Do not modify `review_queue_v1.jsonl`.

For the six approved IDs, set:

```json
{
  "decision": "approved",
  "reviewer": "owner-01",
  "reviewed_at_utc": "the exact UTC value printed in Step 2",
  "notes": "Owner approved per review_proposal_v1.md.",
  "trusted_question_verified": true,
  "trusted_answer_verified": true,
  "trusted_steps_verified": true,
  "holdout_origin_verified": true
}
```

Populate exactly three `distractor_reviews` from the matching approval section in `review_proposal_v1.md`, using zero-based indexes, `semantic_valid: true`, `age_appropriate: true`, and the proposed `glitch_family_id`, patch label, and explanation.

For every rejected ID, set:

```json
{
  "decision": "rejected",
  "reviewer": "owner-01",
  "reviewed_at_utc": "the exact UTC value printed in Step 2",
  "notes": "The complete matching reason under Proposed rejections in review_proposal_v1.md.",
  "trusted_question_verified": false,
  "trusted_answer_verified": false,
  "trusted_steps_verified": false,
  "holdout_origin_verified": false,
  "distractor_reviews": []
}
```

- [x] **Step 4: Verify decision counts without applying them**

Run:

```bash
jq -r '.decision.decision' data/game/work/review_decisions_owner_v1.jsonl | sort | uniq -c
jq -e 'select(.decision.decision == "approved") | (.decision.distractor_reviews | length) == 3' data/game/work/review_decisions_owner_v1.jsonl
jq -e 'select(.decision.decision == "rejected") | .decision.distractor_reviews == []' data/game/work/review_decisions_owner_v1.jsonl
```

Expected: 6 approved, 13 rejected, no pending; every approved row has three reviews and every rejected row has none.

### Task 2: Independently audit the review contract

**Files:** Read-only repository-wide; write none.

**Interfaces:**
- Consumes: owner confirmation, `review_proposal_v1.md`, pending queue, trusted source questions, and `apply_review_decision` in `src/game_content.py`.
- Produces: a message keyed by all nineteen question IDs, reporting any mismatch in approval membership, family allowlist, prompt uniqueness, trusted math, or confirmation scope.

- [x] **Step 1: Compare ID sets**

Confirm the six proposed approvals are exactly the six IDs in Global Constraints and the other thirteen queue IDs are proposed rejections.

- [x] **Step 2: Check approval payloads**

For each of the six, verify all three proposed family IDs occur in `GLITCH_FAMILIES`, repair prompts are non-empty and distinct within the candidate, and explanations accurately counter the immutable computation.

- [x] **Step 3: Check rejection semantics**

Confirm every rejection has a concrete proposal reason and will use an empty review list.

- [x] **Step 4: Return a report only**

Do not edit even if a mismatch exists. Report the exact ID, field, expected value, and observed value to the root agent.

### Task 3: Establish a clean runtime baseline

**Files:** Read-only `game/`; write none.

**Interfaces:**
- Consumes: current dependency-free runtime and test suite.
- Produces: baseline test counts, syntax status, and confirmation that default approved-pack selection can be changed only at the root entrypoint.

- [x] **Step 1: Run non-building game tests**

```bash
cd game
npm run test:prototype
npm run check:prototype
```

Expected: all prototype tests pass and `app.js` parses.

- [x] **Step 2: Inspect entrypoint boundaries**

Confirm `game/index.html` is the only root redirect, `bootstrap.js` already supports `?pack=glitch-rally-v1`, and default `/prototype/` remains an explicitly labeled fixture route.

- [x] **Step 3: Return a report only**

Do not run `npm run build`, because `game/dist/` belongs to Wave 3.

## Convergence Gate 1

The root agent must wait for all Wave 1 agents, inspect the sole edited queue, and run a nonshipping dry application into a unique `/tmp` output:

```bash
.venv/bin/python -m src.game_content_cli apply-reviews \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --validations data/game/work/validations_v1.jsonl \
  --decisions data/game/work/review_decisions_owner_v1.jsonl \
  --output /tmp/glitch-rally-reviewed-gate-v1.jsonl
```

Expected: 19 reviewed rows, 6 approved and 13 rejected. Do not start Wave 2 if the read-only audit disagrees with the materialized queue.

---

## Wave 2 — Pack creation and default launch

### Task 4: Apply reviews and export the sanitized pack

**Files:**
- Create: `data/game/work/reviewed_v1.jsonl`
- Create: `game/content/packs/glitch-rally-v1.json`
- All other paths read-only.

**Interfaces:**
- Consumes: Gate-1-approved `review_decisions_owner_v1.jsonl`, validations, raw candidates, generation manifest, trusted questions, and exact holdout.
- Produces: a 19-row reviewed artifact and a six-encounter, owner-approved static pack.

- [x] **Step 1: Apply decisions**

```bash
.venv/bin/python -m src.game_content_cli apply-reviews \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --validations data/game/work/validations_v1.jsonl \
  --decisions data/game/work/review_decisions_owner_v1.jsonl \
  --output data/game/work/reviewed_v1.jsonl
```

Expected: command exits 0 and writes 19 reviewed rows.

- [x] **Step 2: Record a release timestamp**

```bash
RELEASED_AT_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "$RELEASED_AT_UTC"
```

The timestamp must not predate the review timestamp.

- [x] **Step 3: Export**

```bash
.venv/bin/python -m src.game_content_cli export-pack \
  --questions data/game/questions_v1.jsonl \
  --holdout data/processed/eval_heldout.jsonl \
  --reviewed data/game/work/reviewed_v1.jsonl \
  --pack-id glitch-rally-v1 \
  --released-at-utc "$RELEASED_AT_UTC" \
  --output game/content/packs/glitch-rally-v1.json
```

Expected: command exits 0; the pack has `encounterCount: 6`, exact approved provenance, and no reviewer alias, notes, raw response, or rejected content.

### Task 5: Make the reviewed pack the default root launch

**Files:**
- Modify: `game/index.html`
- Modify tests only: `game/build-static.test.js`
- All other paths read-only.

**Interfaces:**
- Consumes: the already implemented `?pack=<id>` loader contract.
- Produces: root static launch to `./prototype/?pack=glitch-rally-v1`; direct `/prototype/` remains the labeled fixture fallback.

- [x] **Step 1: Add a failing isolated entrypoint test**

Add a test named `root entrypoint selects the reviewed pack` to
`build-static.test.js`. It must read only `game/index.html` and require both the meta
refresh and visible link to use `./prototype/?pack=glitch-rally-v1`. Do not reuse the
artifact-building test because the pack executor owns `game/content/packs/` concurrently.

- [x] **Step 2: Verify red**

```bash
cd game
node --test --test-name-pattern='root entrypoint selects the reviewed pack' build-static.test.js
```

Expected: failure because `game/index.html` still points to `./prototype/`.

- [x] **Step 3: Modify only the root entrypoint**

Set the meta refresh and anchor `href` to `./prototype/?pack=glitch-rally-v1`. Change the root title to `Mathbreakers: Glitch Rally` and the visible copy to `Opening the reviewed SLM rally…`.

- [x] **Step 4: Verify green**

Run the focused command from Step 2. Expected: pass.

### Task 6: Audit release boundaries before convergence

**Files:** Read-only repository-wide; write none.

- [x] Confirm `build-static.mjs` validates every `.json` pack through `loadApprovedPack` before copying.
- [x] Confirm `loadApprovedPack` requires the pinned model, adapter, holdout receipt, owner approval, exact schemas, hashes, and chronology.
- [x] Confirm no reviewer alias, notes, raw response, validation report, or rejected candidate can enter the exported pack.
- [x] Return a message-only checklist; do not inspect partially written Wave-2 outputs.

## Convergence Gate 2

After all Wave 2 agents stop editing, the root agent must validate the actual pack, review counts, and entrypoint diff before Wave 3.

---

## Wave 3 — Independent verification and documentation

### Task 7: Audit the final pack trust contract

**Files:** Read-only; write none.

- [x] Load `game/content/packs/glitch-rally-v1.json` through `loadApprovedPack`.
- [x] Confirm exactly six encounters and the intended six question IDs.
- [x] Confirm every nested object is deeply frozen after load.
- [x] Confirm the content hash recomputes, the holdout receipt is exact, and no private review fields occur anywhere.
- [x] Return findings only.

### Task 8: Build and exercise the static artifact

**Files:**
- Generated-write ownership: `game/dist/` only through `npm run build`.
- Source repository read-only.

- [x] Run `cd game && npm test`.
- [x] Run `cd game && npm run build`.
- [x] Confirm `game/dist/content/packs/glitch-rally-v1.json` exists and the root redirect selects it.
- [x] Run all built JavaScript through `node --check`.
- [x] Run a six-encounter reducer/renderer simulation using the built pack.
- [x] Return exact counts and failures; do not patch source files.

### Task 9: Synchronize final documentation

**Files:**
- Modify: `README.md`
- Modify: `GAME_ARCHITECTURE.md`
- Modify: `GAME_DESIGN.md`
- Modify: `game/README.md`
- Modify: `game/content/packs/README.md`
- Modify: `docs/superpowers/plans/2026-07-11-glitch-rally-parallel-release.md`

- [x] Replace “real pack awaits owner review” language with the exact six-encounter release state.
- [x] State that root launch uses the reviewed SLM pack and direct `/prototype/` remains illustrative.
- [x] Run `cd game && npm test` without building `game/dist/`, then record that command's
  own fresh test count. Do not depend on another concurrent agent's report.
- [x] Preserve honest browser-QA status; do not claim screenshots or responsive checks that were not run.

## Convergence Gate 3

The root agent reruns the Python suite, complete Node suite, static build, notebook checks,
narrow automated security scans, pack loader, and `git diff --check`. Any failure creates
a new narrowly owned repair task; no auditor edits files opportunistically. This gate's
automated scans do not replace the independent final release audit in Task 12.

**Completed 2026-07-11:** 81/81 Python tests and 110/110 Node tests passed (101
prototype/runtime plus nine static-builder). The build emitted ten runtime files and one
pack; every built JavaScript file parsed. Seven executable notebook Python cells compiled,
with one intentional Colab magic cell skipped. The actual pack loaded, was deeply frozen,
and rejected tampering. `git diff --check` passed. This automated gate does not satisfy
the real-browser checks in Task 10.

---

## Wave 4 — Final browser and release QA

### Task 10: Browser QA

**Files:** Screenshot output only under `/Users/jonat/.codex/visualizations/2026/07/11/019f4eb8-5de5-7081-8589-8e46b6bd9068/`.

- [ ] Navigate root launch and confirm the reviewed-SLM title/copy.
- [ ] Complete all six checkpoints with a mix of correct and counterfeit first choices.
- [ ] Verify keyboard-only focus, live announcements, wrong-repair recovery, finish, and replay.
- [ ] Verify desktop, tablet, 320px reflow, reduced motion, and forced-colors behavior.
- [ ] Capture representative screenshots and inspect console errors.

**Blocked:** static responsive-rule inspection and a static six-encounter simulation
passed. The in-app browser connected successfully, but sandbox localhost
binding/escalation failed and direct `file://` navigation was rejected by browser URL
policy, so the game could not be served to it. The automated checks do not establish
viewport rendering, keyboard behavior, runtime console state, motion behavior, or
screenshot quality, so every browser checkbox remains open.

### Task 11: Approved-content playthrough audit

**Files:** Read-only; write none.

- [x] Simulate all six correct routes and every counterfeit route.
- [x] Confirm each correct route reveals `featuredCounterfeitId` and each wrong route reveals the selected counterfeit.
- [x] Confirm every repair mapping resolves exactly once and the six-run totals bank correctly.
- [x] Return a message-only report.

**Completed:** the audit covered six correct routes, 18 counterfeit routes, and 72 repair
branches. All-correct choices banked 6 Proof Boosts / 6 Patch Cannon attempts;
all-counterfeit choices banked 0 / 6; the mixed run banked 3 / 9.

### Task 12: Privacy, offline, and release audit

**Files:** Read-only; write none.

- [x] Scan `game/dist/` for external URLs, credentials, reviewer fields, raw responses, review notes, holdout records, source questions, and rejected candidates.
- [x] Confirm the only runtime fetch is the same-origin approved-pack path.
- [x] Confirm no uncommitted user file outside the game scope was modified by release work.
- [x] Return prioritized findings; explicitly state when none exist.

**Completed:** 110/110 Node tests and 54/54 focused security tests passed. The built
artifact contained exactly 12 files in total, including one released pack; source and
built packs were semantically identical, the content hash recomputed, and deep-freeze,
tamper, and private-field rejection checks passed. Every module import was relative, the
only runtime fetch was the same-origin `GET` for the approved pack, and scans found no
external URL, telemetry, storage, remote asset, credential, reviewer, raw-response, or
rejected-ID leak. No release-work modification outside the game scope was identified.

## Final handoff

The root agent alone reports completion. The report must distinguish automated evidence from browser evidence, identify the exact released pack, provide fresh test counts, state that no commit or push occurred, and list any genuinely unresolved external blocker.
