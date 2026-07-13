# v8 Role-Separated Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the one-shot v8 preparation without reusing Opus as its own judge by admitting Opus teacher rows only through deterministic, repository-owned gates.

**Architecture:** Opus remains a bounded generator. `src.v8_teacher` resolves each generated misconception to an exact alias in the audited Wayline procedure registry, then requires exactly three distinct mappings plus the existing schema, key, answer, computation, grounding, deduplication, and leakage gates. `src.v8_data` treats that named route as independent of the rejected Opus judge calibration and requires a predeclared 20-row survivor floor.

**Tech Stack:** Python 3, `unittest`, JSONL artifacts, existing exact-arithmetic verifier, audited Wayline procedure registry.

## Global Constraints

- Preserve `data/eval_out/opus_binding_calibration_v8.json` as rejected evidence.
- Use `anthropic-primary/claude-opus-4-8` only as teacher/frontier generator.
- Make at most 130 new teacher-generation tasks and skip all 390 Opus self-judge tasks.
- Never claim observed student frequency or calibrated plausibility without an accepted independent judge artifact.
- Keep mixed/nonnumeric confidence unavailable; no model self-reported confidence.
- Do not run Unity, commit, push, reset, clean, or check out files.

---

### Task 1: Deterministic teacher filter

**Files:**
- Modify: `tests/test_v8_quality_pipeline.py`
- Modify: `src/v8_teacher.py`

**Interfaces:**
- Produces: `supported_procedure_labels(topic: str) -> tuple[str, ...]`
- Produces: `filter_teacher_records(pool, predictions, forbidden_questions=(), minimum_survivors=20) -> (records, report)`

- [ ] Write tests proving registered labels survive without verdicts, unsupported labels fail, duplicate procedure mappings fail, leakage fails, and fewer than 20 survivors leaves `ready` false.
- [ ] Run `python -m unittest tests.test_v8_quality_pipeline` and verify the new tests fail because the deterministic API does not exist.
- [ ] Implement exact registry-alias resolution, structural/computation/grounding gates, record-level deduplication, forbidden-boundary checks, route metadata, and an auditable rejection report.
- [ ] Re-run the focused tests and require all to pass.

### Task 2: Bounded teacher-generation mode

**Files:**
- Modify: `tests/test_tfy_model_roles.py`
- Modify: `src/run_frontier.py`

**Interfaces:**
- Consumes: `supported_procedure_labels`
- Produces: CLI flag `--deterministic-teacher`

- [ ] Write a test proving deterministic-teacher requests include only repository-registered labels and predictions record `generation_route=deterministic_teacher_filter`.
- [ ] Run the focused test and verify it fails before implementation.
- [ ] Add the opt-in prompt guidance and include route/prompt content in the resumable cache key.
- [ ] Re-run the focused tests and require all to pass.

### Task 3: Fail-closed readiness semantics

**Files:**
- Modify: `tests/test_v8_data.py`
- Modify: `src/v8_data.py`

**Interfaces:**
- Produces: `deterministic_teacher_records_ok(records, minimum_survivors=20) -> bool`
- Produces: CLI flag `--require-deterministic-teacher`

- [ ] Write tests proving the deterministic route can be ready while `opus_judge_ready` remains false and malformed/undersized artifacts remain blocked.
- [ ] Run the focused test and verify it fails before implementation.
- [ ] Add the teacher artifact/report to the manifest, require the 20-row floor, preserve rejected calibration state, and make `training_ready` depend on exact Opus access plus deterministic teacher readiness—not Opus judge readiness.
- [ ] Re-run the focused tests and require all to pass.

### Task 4: Execute the capped preparation

**Files:**
- Generate: `data/processed/v8_teacher_predictions_opus.jsonl`
- Generate: `data/processed/real_train_seed_v8_opus.jsonl`
- Generate: `data/processed/v8_teacher_filter_report.json`
- Update: `data/processed/train_v8.jsonl`
- Update: `data/processed/v8_manifest.json`

- [ ] Run the 130-request/66,560-output-token estimate.
- [ ] Run generation with `--max-calls 130 --deterministic-teacher`.
- [ ] Run deterministic filtering with no verdict file and record every rejection reason.
- [ ] If survivors are below 20, stop with `training_ready=false`.
- [ ] Otherwise build with `--require-deterministic-teacher` and verify every artifact hash, both leakage boundaries, and every training pair.

### Task 5: Documentation and verification

**Files:**
- Modify: `TABLE_V8_PLAN.md`
- Modify: `docs/V8_RUNBOOK.md`
- Modify: `DATASET_V8.md`

- [ ] Document the rejected Opus judge unchanged, role separation, saved 390-call budget, deterministic survivor floor, unavailable plausibility proxy, and numeric-only confidence limitation.
- [ ] Run all `test_v8_*.py`, then the full Python suite and v7.1 error analysis.
- [ ] Confirm the Unity diff fingerprint is unchanged and give the owner exactly one Colab action based on `training_ready`.
