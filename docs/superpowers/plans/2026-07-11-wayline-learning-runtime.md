# Wayline Learning Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a packaged, learner-safe Wayline Forge service that creates exact questions, obtains distractors from the released Qwen adapter, rejects unsafe generations, runs the truthful two-pass quiz, adapts from evidence, and supplies Unity through sealed contracts.

**Architecture:** A Python 3.12 FastAPI sidecar binds only to loopback and owns all question truth, answer keys, learner evidence, and boss gates. It talks to a local `llama.cpp` worker through a narrow provider interface and falls back to a reviewed SQLite cache; Unity sees only opaque public choices until the final reveal. Optional Sonnet handles bounded nonpersonal story text and is never required for correctness or adaptation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Uvicorn, HTTPX, JSON Schema, SQLite, standard-library `Fraction`/`Decimal`, `llama.cpp` Metal, PyInstaller, and `unittest`.

## Global Constraints

- Follow every constraint in `docs/superpowers/plans/2026-07-11-wayline-master-roadmap.md`.
- Preserve the research package under `src/`; product runtime code lives under `services/wayline_forge/`.
- Product math may parity-test research formulas but may not import `src/buggy_procedures.py`.
- Reject unknown fields, duplicate JSON keys, ambiguous procedure mappings, and unpinned model receipts.
- No correct answer, raw model output, or item correctness crosses the public API before final reveal.
- The initial response contains the exact truthful wrong count and no item-level hint.
- Only one full-batch revision is accepted.
- All state transitions and writes are idempotent and replayable.
- Do not install dependencies, download model weights, call TrueFoundry, stage, commit, or push until separately authorized during execution.

---

### Task 1: Create the isolated service skeleton

**Files:**
- Create: `services/wayline_forge/app/__init__.py`
- Create: `services/wayline_forge/app/settings.py`
- Create: `services/wayline_forge/tests/__init__.py`
- Create: `services/wayline_forge/tests/test_settings.py`
- Create: `services/wayline_forge/requirements-live.in`
- Create: `services/wayline_forge/requirements-live.lock`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `Settings.for_tests(runtime_root: Path) -> Settings` and `Settings.from_environment() -> Settings`.
- Runtime paths are relative to one packaged `runtime_root`; no research `.venv`, Torch, Unsloth, or developer absolute path is used.

- [ ] **Step 1: Write the failing settings test**

```python
from pathlib import Path
import unittest

from services.wayline_forge.app.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_test_defaults_are_loopback_and_secret_free(self):
        settings = Settings.for_tests(Path("/tmp/wayline-runtime"))
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 0)
        self.assertEqual(
            settings.model_manifest,
            Path("/tmp/wayline-runtime/resources/model_manifest_v1.json"),
        )
        self.assertEqual(
            settings.cache_path,
            Path("/tmp/wayline-runtime/resources/reviewed_cache_v1.sqlite"),
        )
        self.assertIsNone(settings.truefoundry_api_key)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python -m unittest services.wayline_forge.tests.test_settings -v
```

Expected: import failure because `services.wayline_forge.app.settings` does not exist.

- [ ] **Step 3: Implement immutable settings**

```python
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    runtime_root: Path
    host: str
    port: int
    model_manifest: Path
    cache_path: Path
    profile_db: Path
    truefoundry_base_url: str | None
    truefoundry_model: str | None
    truefoundry_api_key: str | None

    @classmethod
    def for_tests(cls, root: Path) -> "Settings":
        root = root.resolve()
        return cls(
            runtime_root=root,
            host="127.0.0.1",
            port=0,
            model_manifest=root / "resources/model_manifest_v1.json",
            cache_path=root / "resources/reviewed_cache_v1.sqlite",
            profile_db=root / "profiles/wayline_profiles_v1.sqlite",
            truefoundry_base_url=None,
            truefoundry_model=None,
            truefoundry_api_key=None,
        )

    @classmethod
    def from_environment(cls) -> "Settings":
        root = Path(os.environ["WAYLINE_RUNTIME_ROOT"]).resolve()
        return cls(
            runtime_root=root,
            host="127.0.0.1",
            port=int(os.getenv("WAYLINE_PORT", "0")),
            model_manifest=root / "resources/model_manifest_v1.json",
            cache_path=root / "resources/reviewed_cache_v1.sqlite",
            profile_db=root / "profiles/wayline_profiles_v1.sqlite",
            truefoundry_base_url=os.getenv("TFY_BASE_URL"),
            truefoundry_model=os.getenv("TFY_MODEL"),
            truefoundry_api_key=os.getenv("TFY_API_KEY"),
        )
```

- [ ] **Step 4: Pin the live-only dependency set**

`requirements-live.in` contains FastAPI, Pydantic v2, Uvicorn, HTTPX, JSON Schema, and PyInstaller. Resolve a Python 3.12 hash-locked `requirements-live.lock` in a disposable environment. Confirm the lock contains no `torch`, `transformers`, `peft`, `unsloth`, `jupyter`, or training dependency.

- [ ] **Step 5: Extend `.gitignore` narrowly**

Add only `services/wayline_forge/build/`, `dist/`, packaged runtime logs, local profile databases, downloaded GGUF files, and `.venv-live/`. Preserve every existing user entry.

- [ ] **Step 6: Verify GREEN**

Run the test from Step 2. Expected: one passing test.

- [ ] **Step 7: Execution checkpoint**

Report files, resolved dependency versions, and test output. If the owner has authorized commits, commit only this isolated skeleton; otherwise leave it unstaged.

---

### Task 2: Freeze strict cross-runtime contracts

**Files:**
- Create: `contracts/wayline/v1/session-create.schema.json`
- Create: `contracts/wayline/v1/battle-quiz-request.schema.json`
- Create: `contracts/wayline/v1/public-quiz-batch.schema.json`
- Create: `contracts/wayline/v1/initial-submit.schema.json`
- Create: `contracts/wayline/v1/wrong-count-result.schema.json`
- Create: `contracts/wayline/v1/revision-submit.schema.json`
- Create: `contracts/wayline/v1/final-quiz-result.schema.json`
- Create: `contracts/wayline/v1/boss-gate-result.schema.json`
- Create: `contracts/wayline/v1/fixtures/valid/three-item-batch.json`
- Create: `contracts/wayline/v1/fixtures/valid/two-wrong-result.json`
- Create: `contracts/wayline/v1/fixtures/valid/final-result.json`
- Create: `contracts/wayline/v1/fixtures/invalid/leaked-key.json`
- Create: `contracts/wayline/v1/fixtures/invalid/missing-confidence.json`
- Create: `contracts/wayline/v1/fixtures/invalid/unknown-field.json`
- Create: `services/wayline_forge/app/contracts.py`
- Create: `services/wayline_forge/tests/test_contracts.py`

**Interfaces:**
- Produces strict Pydantic models `BattleQuizRequest`, `PublicQuizBatch`, `InitialSubmission`, `WrongCountResult`, `RevisionSubmission`, `FinalQuizResult`, and `BossGateResult`.
- `PublicQuizBatch` exposes `option_id` and display text only; it has no key, correctness, procedure, or evidence field.

- [ ] **Step 1: Write fixture-validation tests**

```python
import json
from pathlib import Path
import unittest

from pydantic import ValidationError
from services.wayline_forge.app.contracts import PublicQuizBatch, WrongCountResult


FIXTURES = Path("contracts/wayline/v1/fixtures")


class ContractTests(unittest.TestCase):
    def test_public_batch_contains_no_answer_or_diagnosis_fields(self):
        payload = json.loads((FIXTURES / "valid/three-item-batch.json").read_text())
        model = PublicQuizBatch.model_validate(payload)
        serialized = model.model_dump_json().lower()
        for banned in ("correct_answer", "is_correct", "procedure_id", "misconception"):
            self.assertNotIn(banned, serialized)

    def test_wrong_count_is_exact_and_bounded(self):
        result = WrongCountResult.model_validate(
            {"schemaVersion": "wayline.v1", "batchId": "b-1", "itemCount": 3,
             "wrongCount": 2, "revisionRequired": True}
        )
        self.assertEqual(result.wrong_count, 2)

    def test_unknown_fields_fail(self):
        payload = json.loads((FIXTURES / "invalid/unknown-field.json").read_text())
        with self.assertRaises(ValidationError):
            PublicQuizBatch.model_validate(payload)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest services.wayline_forge.tests.test_contracts -v
```

Expected: imports or fixture lookups fail.

- [ ] **Step 3: Implement strict models**

Use `ConfigDict(extra="forbid", strict=True, populate_by_name=True)` on every public model. Define confidence exactly:

```python
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class Confidence(str, Enum):
    CERTAIN = "certain"
    LEANING = "leaning"
    GUESSING = "guessing"


class AnswerSelection(StrictModel):
    item_id: str = Field(alias="itemId", min_length=3, max_length=96)
    option_id: str = Field(alias="optionId", min_length=3, max_length=96)
    confidence: Confidence


class WrongCountResult(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    batch_id: str = Field(alias="batchId", min_length=3, max_length=96)
    item_count: int = Field(alias="itemCount", ge=3, le=10)
    wrong_count: int = Field(alias="wrongCount", ge=0, le=10)
    revision_required: bool = Field(alias="revisionRequired")

    def model_post_init(self, __context: object) -> None:
        if self.wrong_count > self.item_count:
            raise ValueError("wrongCount cannot exceed itemCount")
        if self.revision_required != (self.wrong_count > 0):
            raise ValueError("revisionRequired must equal wrongCount > 0")
```

- [ ] **Step 4: Write schemas and frozen valid/invalid fixtures**

Set `additionalProperties: false` at every object level. Require every item, option, selection, confidence, count, and version field. The invalid leaked-key fixture deliberately includes `correctAnswer`; both JSON Schema and Pydantic must reject it.

- [ ] **Step 5: Verify schema/Pydantic parity**

Run all fixtures through both validators. Expected: every valid fixture passes both; every invalid fixture fails both.

- [ ] **Step 6: Execution checkpoint**

Publish fixture hashes to the task report. Commit only with owner authorization.

---

### Task 3: Implement the exact curriculum compiler and product procedure registry

**Files:**
- Create: `services/wayline_forge/app/safe_numeric.py`
- Create: `services/wayline_forge/app/curriculum.py`
- Create: `services/wayline_forge/app/question_kernel.py`
- Create: `services/wayline_forge/app/procedure_registry.py`
- Create: `services/wayline_forge/resources/curriculum_v1.json`
- Create: `services/wayline_forge/resources/procedure_registry_v1.json`
- Create: `data/wayline/runtime/reference_prompts_v1.jsonl`
- Create: `services/wayline_forge/tests/test_safe_numeric.py`
- Create: `services/wayline_forge/tests/test_question_kernel.py`
- Create: `services/wayline_forge/tests/test_procedure_registry.py`

**Interfaces:**
- Produces `QuestionCompiler.compile(request: CompileRequest) -> QuestionBlueprint`.
- Produces `ProcedureRegistry.evaluate(procedure_id, blueprint) -> ExactValue` and canonical label/computation/feedback renderers.
- Initial curriculum contains only the 15 launch-core topics in the GDD and uses bounded procedure shapes matching actual training coverage.

- [ ] **Step 1: Write failing exactness and exclusion tests**

```python
from fractions import Fraction
import unittest

from services.wayline_forge.app.question_kernel import CompileRequest, QuestionCompiler


class QuestionKernelTests(unittest.TestCase):
    def test_same_seed_produces_same_blueprint(self):
        compiler = QuestionCompiler.for_tests()
        request = CompileRequest(
            world_id="decimara",
            skill_id="decimal_add_sub",
            family_id="decimal_add",
            difficulty=2,
            seed=731,
        )
        self.assertEqual(compiler.compile(request), compiler.compile(request))

    def test_decimal_answer_is_exact(self):
        blueprint = QuestionCompiler.for_tests().compile(
            CompileRequest("decimara", "decimal_add_sub", "decimal_add", 2, 731)
        )
        self.assertIsInstance(blueprint.canonical_answer.value, Fraction)

    def test_blueprint_has_allowed_procedures_and_holdout_receipt(self):
        blueprint = QuestionCompiler.for_tests().compile(
            CompileRequest("valuehold", "place_value", "place_value", 1, 19)
        )
        self.assertGreaterEqual(len(blueprint.allowed_procedure_ids), 3)
        self.assertEqual(blueprint.holdout_receipt.record_count, 140)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_safe_numeric \
  services.wayline_forge.tests.test_question_kernel \
  services.wayline_forge.tests.test_procedure_registry -v
```

Expected: missing modules.

- [ ] **Step 3: Implement typed blueprint creation**

```python
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class CompileRequest:
    world_id: str
    skill_id: str
    family_id: str
    difficulty: int
    seed: int


@dataclass(frozen=True)
class CanonicalAnswer:
    value: Fraction
    display: str


@dataclass(frozen=True)
class QuestionBlueprint:
    question_id: str
    world_id: str
    skill_id: str
    family_id: str
    template_id: str
    operands: tuple[str, ...]
    prompt: str
    canonical_answer: CanonicalAnswer
    trusted_steps: tuple[str, ...]
    allowed_procedure_ids: tuple[str, ...]
    difficulty: int
    seed: int
    content_sha256: str
    holdout_receipt: object
```

Use a local `random.Random(seed)`, exact `Fraction`/`Decimal`, canonical JSON hashing, and explicit rejection loops with a hard maximum of 64 operand samples. Do not use `eval`, `ast` on untrusted text, floats for answer truth, or nondeterministic global randomness.

- [ ] **Step 4: Implement launch-core templates**

Create at least two changed-context templates for each supported narrow family: place value; mental add/sub; decimal add/sub; decimal multiply; one-decimal-place rounding; fraction add/sub; fraction multiply; fraction divided by integer; percent of amount; decimal-percent conversion; negative `-a + b`; HCF; mental multiply/divide; `a + b × c` BIDMAS; same-base index multiplication. Each template declares exactly which product procedures may be tested.

- [ ] **Step 5: Build an audited product procedure registry**

Each registry entry contains `procedure_id`, topic/family, aliases, exact executable formula name, applicability constraints, canonical label, canonical computation template, child-safe `can_come_from`, and `reliable_method`. Implement functions explicitly in product code. Add parity tests against frozen expected values derived from reviewed examples, not runtime imports from the training engine.

- [ ] **Step 6: Preserve the frozen holdout boundary**

Port canonicalization, exact 140-row digest validation, fingerprinting, and similarity exclusion through a small product-owned module. The reference prompts and every generated blueprint must carry the holdout receipt.

- [ ] **Step 7: Run property and fixture tests**

Generate 1,000 blueprints across launch families. Expected: deterministic replay, exact answers, at least three distinct applicable wrong procedures per blueprint, no correct-key collisions among allowed procedures, and zero holdout matches.

- [ ] **Step 8: Execution checkpoint**

Report coverage by world/family and all rejected degeneracy reasons. Commit only with owner authorization.

---

### Task 4: Add SLM providers and the fail-closed verifier

**Files:**
- Create: `services/wayline_forge/app/providers/__init__.py`
- Create: `services/wayline_forge/app/providers/distractor.py`
- Create: `services/wayline_forge/app/providers/recorded.py`
- Create: `services/wayline_forge/app/providers/llama_cpp.py`
- Create: `services/wayline_forge/app/slm_prompt.py`
- Create: `services/wayline_forge/app/distractor_verifier.py`
- Create: `services/wayline_forge/tests/fixtures/slm/accepted.json`
- Create: `services/wayline_forge/tests/fixtures/slm/key_collision.json`
- Create: `services/wayline_forge/tests/fixtures/slm/ambiguous_route.json`
- Create: `services/wayline_forge/tests/fixtures/slm/label_mismatch.json`
- Create: `services/wayline_forge/tests/test_distractor_verifier.py`
- Create: `services/wayline_forge/tests/test_llama_cpp_provider.py`

**Interfaces:**
- Consumes `QuestionBlueprint` and immutable model manifest.
- Produces `VerifiedDistractorSet` or typed `VerificationRejection`; there is no partially accepted set.

- [ ] **Step 1: Write verifier rejection tests**

```python
import unittest

from services.wayline_forge.app.distractor_verifier import DistractorVerifier


class DistractorVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = DistractorVerifier.for_tests()
        self.blueprint = self.verifier.reference_blueprint("decimal-add-731")

    def test_accepts_only_unique_exact_routes(self):
        result = self.verifier.verify_fixture(self.blueprint, "accepted.json")
        self.assertTrue(result.accepted)
        self.assertEqual(len(result.value.options), 4)
        self.assertEqual(len({o.option_id for o in result.value.options}), 4)

    def test_rejects_correct_key_collision(self):
        result = self.verifier.verify_fixture(self.blueprint, "key_collision.json")
        self.assertEqual(result.code, "correct_key_collision")

    def test_rejects_answer_matching_two_routes(self):
        result = self.verifier.verify_fixture(self.blueprint, "ambiguous_route.json")
        self.assertEqual(result.code, "ambiguous_procedure_mapping")

    def test_rejects_unapproved_label_alias(self):
        result = self.verifier.verify_fixture(self.blueprint, "label_mismatch.json")
        self.assertEqual(result.code, "label_procedure_mismatch")
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_distractor_verifier \
  services.wayline_forge.tests.test_llama_cpp_provider -v
```

Expected: missing provider and verifier modules.

- [ ] **Step 3: Define provider protocol**

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SlmRequest:
    question_id: str
    question: str
    correct_answer: str
    topic: str
    prompt_sha256: str


@dataclass(frozen=True)
class RawSlmGeneration:
    text: str
    model_sha256: str
    prompt_sha256: str
    generated_at_utc: str


class DistractorProvider(Protocol):
    async def generate(self, request: SlmRequest) -> RawSlmGeneration: ...
```

The recorded provider returns frozen test responses. The llama.cpp provider sends a bounded OpenAI-compatible completion request to loopback, validates response size/type, and attaches the pinned manifest receipt.

- [ ] **Step 4: Implement all-or-nothing verification**

Perform the twelve acceptance steps from `WAYLINE_LEARNING_AND_RUNTIME_SPEC.md`. Canonicalize accepted labels, computations, and feedback from the product registry. Randomize the four public options from the blueprint seed and generate opaque IDs; never expose route identity through option ordering or IDs.

- [ ] **Step 5: Add adversarial cases**

Cover duplicate JSON keys, extra prose, code fences, exponent bombs, huge integers, NaN/infinity, Unicode control characters, HTML, correct-answer restatement, duplicated routes, unsupported route names, operand substitution, mismatched computation, and forged model receipts.

- [ ] **Step 6: Verify GREEN**

Run Step 2. Expected: all accept/reject codes pass and no raw model field appears in the public serialized set.

- [ ] **Step 7: Execution checkpoint**

Report the complete rejection-code table. Commit only with owner authorization.

---

### Task 5: Export, parity-test, and cache the local model

**Files:**
- Create: `notebooks/export_wayline_gguf_colab.ipynb`
- Create: `services/wayline_forge/model_manifest.schema.json`
- Create: `services/wayline_forge/resources/model_manifest_v1.json`
- Create: `services/wayline_forge/app/reviewed_cache.py`
- Create: `services/wayline_forge/scripts/benchmark_model.py`
- Create: `services/wayline_forge/scripts/migrate_legacy_approved.py`
- Create: `services/wayline_forge/scripts/build_reviewed_cache.py`
- Create: `services/wayline_forge/tests/test_model_manifest.py`
- Create: `services/wayline_forge/tests/test_reviewed_cache.py`
- Create: `services/wayline_forge/tests/test_legacy_migration.py`
- Create: `data/wayline/runtime/reviewed_cache_manifest_v1.json`

**Interfaces:**
- Produces one pinned GGUF manifest and `ReviewedCache.get(CacheKey) -> VerifiedQuestionBundle | None`.
- Cache keys include world, skill, family, difficulty band, template exclusion set, and registry version.

- [ ] **Step 1: Write manifest and cache tests**

```python
import unittest

from services.wayline_forge.app.reviewed_cache import CacheKey, ReviewedCache


class ReviewedCacheTests(unittest.TestCase):
    def test_returns_same_skill_without_adjacent_template(self):
        cache = ReviewedCache.in_memory_fixture()
        bundle = cache.get(CacheKey(
            world_id="decimara",
            skill_id="decimal_add_sub",
            family_id="decimal_add",
            difficulty_band=2,
            excluded_template_ids=("decimal-market-v1",),
            registry_version="wayline-procedures-v1",
        ))
        self.assertEqual(bundle.skill_id, "decimal_add_sub")
        self.assertNotEqual(bundle.template_id, "decimal-market-v1")
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_model_manifest \
  services.wayline_forge.tests.test_reviewed_cache \
  services.wayline_forge.tests.test_legacy_migration -v
```

Expected: missing modules/resources.

- [ ] **Step 3: Build the Colab export notebook**

The notebook resolves immutable base/adapter SHAs, merges the LoRA, exports full precision, invokes a pinned `llama.cpp` conversion revision, quantizes `Q4_K_M`, computes SHA-256, writes tokenizer/model metadata, and downloads the GGUF plus manifest. It must not train or modify the frozen adapter.

- [ ] **Step 4: Enforce parity gates**

Run original adapter and GGUF deterministically over `data/wayline/runtime/reference_prompts_v1.jsonl` and all six owner-approved legacy encounters. Fail if an approved encounter changes its accepted procedure set, any receipt is unpinned, or exactly-three/distinct-answer/key-safe/product-verifier pass rates regress by more than five percentage points.

- [ ] **Step 5: Implement reviewed SQLite cache**

Store sealed public bundle, sealed answer key/procedure mapping, blueprint receipt, model/verifier/registry receipts, review alias/decision, and content hash. Enforce unique content hash, transactional writes, schema version, and read-only mode in learner builds.

- [ ] **Step 6: Migrate six approved legacy encounters explicitly**

Read `data/game/work/review_decisions_owner_v1.jsonl` and the approved pack, recompute trusted answers, re-run Wayline holdout and product-registry validation, map only compatible records, and write a migration report. Do not copy the Glitch Rally schema directly or convert rejected records.

- [ ] **Step 7: Verify GREEN and benchmark**

Expected: cache and manifest tests pass; benchmark report includes Mac tokens/sec, first-token latency, completion latency, RSS, total unified-memory pressure, verifier rate, and cache fallback rate.

- [ ] **Step 8: Execution checkpoint**

The owner may need to run the free Colab export notebook and place the downloaded GGUF/manifest in the documented runtime directory. Do not upload, publish, or bundle the artifact without separate approval.

---

### Task 6: Implement immutable evidence, adaptation, and gates

**Files:**
- Create: `services/wayline_forge/app/events.py`
- Create: `services/wayline_forge/app/evidence_reducer.py`
- Create: `services/wayline_forge/app/adaptive_planner.py`
- Create: `services/wayline_forge/app/boss_gate.py`
- Create: `services/wayline_forge/app/profile_store.py`
- Create: `services/wayline_forge/tests/test_evidence_reducer.py`
- Create: `services/wayline_forge/tests/test_adaptive_planner.py`
- Create: `services/wayline_forge/tests/test_boss_gate.py`
- Create: `services/wayline_forge/tests/test_profile_store.py`
- Create: `services/wayline_forge/tests/fixtures.py`

**Interfaces:**
- Produces pure `reduce_events(events) -> LearnerState`, `plan_slots(state, battle_tier) -> tuple[SlotIntent, ...]`, and `evaluate_gate(state, world_id) -> BossGateResult`.
- Profile storage is an append-only event log plus rebuildable derived projections.

- [ ] **Step 1: Write reducer/gate tests**

```python
import unittest

from services.wayline_forge.app.evidence_reducer import reduce_events
from services.wayline_forge.app.boss_gate import evaluate_boss_gate
from services.wayline_forge.tests.fixtures import event


class EvidenceTests(unittest.TestCase):
    def test_one_wrong_answer_is_not_active_diagnosis(self):
        state = reduce_events([event.wrong("align_by_ends", confidence="certain")])
        self.assertEqual(state.procedure("align_by_ends").status, "suspected")

    def test_distinct_compatible_questions_can_activate(self):
        state = reduce_events([
            event.wrong("align_by_ends", template="a", confidence="leaning"),
            event.wrong("align_by_ends", template="b", confidence="certain"),
        ])
        self.assertEqual(state.procedure("align_by_ends").status, "active")

    def test_gate_uses_first_pass_latest_ten_and_subskill_coverage(self):
        state = event.ready_valuehold_state(latest_ten_correct=7)
        self.assertTrue(evaluate_boss_gate(state, "valuehold").unlocked)
```

`services/wayline_forge/tests/fixtures.py` defines the deterministic `event` factory used above, including distinct question/template IDs, confidence, first/final correctness, battle wins, subskill exposures, and latest-ten ordering. It creates test events only and cannot be imported by production modules.

- [ ] **Step 2: Run and verify RED**

Run all four task test modules. Expected: missing modules.

- [ ] **Step 3: Implement append-only events and pure replay**

Record the exact fields from the runtime specification. Derived state never mutates the event. Serialize canonical JSON and hash `previous_event_hash + canonical_event` to detect local corruption.

- [ ] **Step 4: Implement evidence states exactly**

Encode candidate, suspected, active, fragile, secure, resolved, and mastery rules verbatim from the runtime specification. A confidence value changes evidence weight only through those named rules.

- [ ] **Step 5: Implement fixed-length slot planning**

Return exactly 3, 4, 4, 5, or 8 intents by battle tier and 10 for the final boss. Fill in priority order: active probe, fragile transfer, under-sampled world skill, spaced prior-world skill, novel current skill. Enforce adjacent item/template/operand exclusion.

- [ ] **Step 6: Implement approved gates and assisted route**

Require four wins, 16 valid items, 7/10 first-pass correct, and core-subskill coverage. Boss clear is 6/8 after revision; campaign finale 8/10. Create three-item Seal Trials and the assisted route after two misses without erasing battle wins.

- [ ] **Step 7: Verify replay and migration**

Rebuild state twice from the same event log and assert byte-identical projections. Interrupt a transaction after event append but before projection update; restart must recover by replay.

- [ ] **Step 8: Execution checkpoint**

Report every transition table and gate fixture. Commit only with owner authorization.

---

### Task 7: Build the atomic quiz orchestrator and API

**Files:**
- Create: `services/wayline_forge/app/quiz_machine.py`
- Create: `services/wayline_forge/app/orchestrator.py`
- Create: `services/wayline_forge/app/api.py`
- Create: `services/wayline_forge/app/launcher.py`
- Create: `services/wayline_forge/tests/test_quiz_machine.py`
- Create: `services/wayline_forge/tests/test_orchestrator.py`
- Create: `services/wayline_forge/tests/test_api.py`
- Create: `services/wayline_forge/tests/test_idempotency.py`
- Create: `services/wayline_forge/tests/api_fixtures.py`

**Interfaces:**
- Implements all endpoints in the runtime specification.
- `prepare_batch` returns only when every slot is live verified or cache verified.
- Initial and revision submissions are complete atomic payloads.

- [ ] **Step 1: Write zero-wrong, revision, and idempotency API tests**

```python
import unittest

from services.wayline_forge.tests.api_fixtures import QuizApiFixtureTestCase


class QuizApiTests(QuizApiFixtureTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_nonzero_initial_reveals_only_exact_count(self):
        app, client = await self.make_fixture(wrong_items={"q1", "q3"})
        response = await client.post("/v1/quiz-batches/b1/initial", json=self.initial())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["wrongCount"], 2)
        serialized = response.text.lower()
        self.assertNotIn("q1", serialized)
        self.assertNotIn("correctanswer", serialized)

    async def test_duplicate_initial_returns_same_receipt(self):
        app, client = await self.make_fixture(wrong_items={"q2"})
        first = await client.post("/v1/quiz-batches/b1/initial", json=self.initial("req-7"))
        second = await client.post("/v1/quiz-batches/b1/initial", json=self.initial("req-7"))
        self.assertEqual(first.json(), second.json())

    async def test_second_revision_is_rejected(self):
        app, client = await self.make_fixture(wrong_items={"q2"})
        await client.post("/v1/quiz-batches/b1/initial", json=self.initial())
        await client.post("/v1/quiz-batches/b1/revision", json=self.revision("r-1"))
        again = await client.post("/v1/quiz-batches/b1/revision", json=self.revision("r-2"))
        self.assertEqual(again.status_code, 409)
```

`QuizApiFixtureTestCase` builds a temporary SQLite database, recorded distractor provider, reviewed cache, deterministic three-item batch, authenticated ASGI client, and complete `initial(request_id)` / `revision(request_id)` payloads. It closes the client and database in `asyncTearDown`, so the tests do not share state.

- [ ] **Step 2: Run and verify RED**

Run the four task modules. Expected: missing API/orchestrator.

- [ ] **Step 3: Implement explicit quiz states**

Use `preparing`, `ready`, `initial_locked`, `revision_open`, `revealed`, and `closed`. Validate legal transitions in one pure function. Persist state and submission receipt in the same SQLite transaction.

- [ ] **Step 4: Implement bounded batch preparation**

Compile every planned slot, call the provider serially, verify, and fill with a reviewed same-skill item at eight seconds. At ten seconds cancel remaining calls and return a complete batch. If no safe bundle exists for a required slot, fail closed before Unity starts the trial and ask the campaign controller to use a fully reviewed emergency batch.

- [ ] **Step 5: Implement sealed scoring**

Validate one selection/confidence per item, verify all opaque option IDs belong to that batch, store the initial payload, calculate exact count from the sealed key, and return no item result. On revision, reveal both passes, canonical method, candidate error wording, and observation receipts.

- [ ] **Step 6: Add loopback launch security**

Generate an ephemeral 256-bit launch token, bind `127.0.0.1`, require the token header, cap request bodies, disable API docs in learner builds, use strict CORS/origin allowlists, and terminate child llama-server when the parent service exits.

- [ ] **Step 7: Verify GREEN and failure recovery**

Test process restart after each state, stale IDs, duplicate payloads, missing confidence, option tampering, model timeout, corrupt cache row, database lock, and Unity disconnect. Expected: exact state recovery or fail-closed error; never a fresh revision.

- [ ] **Step 8: Execution checkpoint**

Report endpoint fixtures and failure matrix. Commit only with owner authorization.

---

### Task 8: Add bounded Sonnet wording and prove privacy

**Files:**
- Create: `services/wayline_forge/app/providers/narrative.py`
- Create: `services/wayline_forge/app/providers/template_narrative.py`
- Create: `services/wayline_forge/app/providers/truefoundry_narrative.py`
- Create: `services/wayline_forge/app/story_linter.py`
- Create: `services/wayline_forge/resources/story_templates_v1.json`
- Create: `services/wayline_forge/tests/test_story_linter.py`
- Create: `services/wayline_forge/tests/test_narrative_privacy.py`

**Interfaces:**
- `NarrativeProvider.skin(request: StorySkinRequest) -> StorySkin` receives only enumerated style IDs and symbolic placeholders.
- Canonical math and feedback remain usable when this provider is unavailable.

- [ ] **Step 1: Write payload-privacy and linter tests**

Assert serialized Sonnet requests contain no profile/session ID, learner name, choices, confidence, correctness, evidence state, operands, canonical answer, procedure output, API secret, or raw SLM response. Assert responses are rejected for missing/duplicated placeholders, numeric literals, answer leakage, blame language, second-person diagnosis, HTML/control characters, or more than 180 display characters.

- [ ] **Step 2: Run and verify RED**

Run both task modules. Expected: missing narrative modules.

- [ ] **Step 3: Implement authored fallback first**

Provide at least six story frames per demo world and four canonical feedback tones. Template output is the production default and passes the same linter as provider output.

- [ ] **Step 4: Implement optional TrueFoundry provider**

Read credentials only in the sidecar, use bounded timeouts/retries, omit learner data, and never log the key or payload. The current research header enables provider logging, so learner mode sends only nonpersonal symbolic requests. Any uncertainty returns the authored fallback.

- [ ] **Step 5: Verify GREEN**

Run privacy tests with fake captured requests. Expected: every banned field is absent and all malformed outputs fall back deterministically.

- [ ] **Step 6: Execution checkpoint**

Do not make a live TrueFoundry call unless the owner explicitly authorizes API use for that execution turn.

---

### Task 9: Package and validate the Mac sidecar

**Files:**
- Create: `services/wayline_forge/WaylineForge.spec`
- Create: `services/wayline_forge/scripts/build_mac_sidecar.py`
- Create: `services/wayline_forge/scripts/run_generation_soak.py`
- Create: `services/wayline_forge/tests/test_packaged_layout.py`
- Create: `services/wayline_forge/tests/test_generation_soak.py`
- Create: `docs/wayline/WAYLINE_MODEL_LICENSES.md`
- Create: `docs/wayline/WAYLINE_RUNTIME_PRIVACY.md`
- Create: `docs/wayline/WAYLINE_GENERATION_SOAK_REPORT.md`

**Interfaces:**
- Produces one Apple-Silicon sidecar folder with launcher, hashed resources, read-only cache, model manifest, and expected llama-server relative path.

- [ ] **Step 1: Write packaged-layout and soak tests**

Assert every resource exists at its manifest path, every digest matches, no `.env`/key/research dataset is present, and a fake 1,000-item run never returns an unverified bundle.

- [ ] **Step 2: Run and verify RED**

Expected: missing package spec/layout.

- [ ] **Step 3: Build deterministic package layout**

Use PyInstaller directory mode, explicit hidden imports, no UPX, no writable code directory, and a generated manifest. Bundle the MIT-licensed Apple-Silicon llama-server binary only after recording its exact source revision and build flags. Keep the several-gigabyte GGUF as a hashed optional bundle or first-run download selected by the release owner.

- [ ] **Step 4: Run live-model soak**

Across at least 1,000 generated items, record provider latency, verifier acceptance/rejection codes, fallback selection, memory, crashes, and displayed provenance. Acceptance is zero unverified displays, zero false keys, zero missing bounded fallback, and no service leak after Unity exit.

- [ ] **Step 5: Complete privacy and license records**

Document Qwen base, LoRA adapter, llama.cpp, Python dependencies, cache content, TrueFoundry boundary, local data paths, export/delete, and all provider retention assumptions.

- [ ] **Step 6: Verify GREEN**

Run all service tests plus packaged smoke on a clean local macOS account. Expected: zero failures and successful create/prepare/initial/revision/gate/delete flow.

- [ ] **Step 7: Execution checkpoint**

Report artifact hashes and sizes. Do not publish or sign with a paid identity without explicit approval.
