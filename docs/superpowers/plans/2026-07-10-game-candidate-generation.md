# Glitch Rally Candidate Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, resumable offline batch generator that turns validated game questions into immutable-provenance raw SLM candidate records, plus a free Google Colab entrypoint for the final v7.1 model.

**Architecture:** A standard-library Python module owns canonical hashing, record construction, atomic JSONL persistence, and resume validation. Generation is dependency-injected, so unit tests use a fake backend while the Colab notebook owns all optional Unsloth, PEFT, Hugging Face, GPU, and download behavior.

**Tech Stack:** Python standard library, existing `src.prompts` and `src.game_content`, `unittest`; Google Colab with Unsloth, PEFT, and `huggingface_hub` only at execution time.

## Global Constraints

- Use `unsloth/Qwen3-4B-bnb-4bit` with `j2ampn/qwen3-4b-distractor-lora-v7`.
- Generate with `do_sample=False`, `max_new_tokens=512`, and `enable_thinking=False`.
- Retain exact raw responses and bind every record to 40-hex immutable Hugging Face revisions.
- Never read questions from, or write candidates derived from, the frozen 140-item holdout.
- Refuse silent output replacement; resume only exact compatible records and persist through atomic replacement.
- Tests must not import or require Unsloth, Torch, a GPU, or network access.
- Do not commit or push.

---

### Task 1: Define the Raw Candidate Contract and Batch Runner

**Files:**
- Create: `tests/test_game_candidate_generation.py`
- Create: `src/game_candidate_generation.py`

**Interfaces:**
- Consumes: `validate_question_bank(items, holdout_questions)`, `SYSTEM_PROMPT`, `build_user`, a callable `backend(system_prompt, user_prompt, generation_parameters) -> str`, and explicit run/model provenance.
- Produces: `load_validated_question_batch(...)`, `stable_json_sha256(...)`, `generate_candidate_batch(...)`, and JSONL records with schema `glitch-rally-candidate-v1`.

- [ ] **Step 1: Write failing tests for deterministic prompt calls and record hashes**

```python
records = generate_candidate_batch(
    questions=[validated_question],
    output_path=output,
    backend=fake_backend,
    provenance=provenance,
)
self.assertEqual(fake_backend.calls[0][2], {
    "do_sample": False,
    "max_new_tokens": 512,
    "enable_thinking": False,
})
self.assertEqual(records[0]["raw_response"], RAW_RESPONSE)
self.assertEqual(records[0]["raw_response_sha256"], sha256_text(RAW_RESPONSE))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_game_candidate_generation -v`

Expected: import failure because `src.game_candidate_generation` does not exist.

- [ ] **Step 3: Implement stable hashing and candidate construction**

```python
GENERATION_PARAMETERS = {
    "do_sample": False,
    "max_new_tokens": 512,
    "enable_thinking": False,
}

def stable_json_sha256(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)
```

Hash `prompt_sha256` from `{"system": SYSTEM_PROMPT, "user": user_prompt}`; hash each complete validated question record and the ordered list of validated records; compute `candidate_id` from the complete record except `candidate_id` and `generated_at_utc`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_game_candidate_generation -v`

Expected: deterministic-generation and hash-contract tests pass.

---

### Task 2: Make JSONL Persistence Atomic and Safely Resumable

**Files:**
- Modify: `tests/test_game_candidate_generation.py`
- Modify: `src/game_candidate_generation.py`

**Interfaces:**
- Consumes: candidate records from Task 1 and a destination `Path`.
- Produces: crash-safe JSONL output, strict existing-record verification, and explicit resume behavior.

- [ ] **Step 1: Write failing persistence tests**

```python
with self.assertRaisesRegex(CandidateGenerationError, "already exists"):
    generate_candidate_batch(..., resume=False)

resumed = generate_candidate_batch(..., resume=True)
self.assertEqual(fake_backend.calls, [])

tampered = existing | {"raw_response_sha256": "0" * 64}
with self.assertRaisesRegex(CandidateGenerationError, "invalid existing record"):
    generate_candidate_batch(..., resume=True)
```

Also simulate a backend exception on question two, then assert question one remains valid and a later resume generates only questions two onward.

- [ ] **Step 2: Run persistence tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_game_candidate_generation.CandidatePersistenceTests -v`

Expected: failures for missing overwrite protection, resume validation, or atomic checkpoint behavior.

- [ ] **Step 3: Implement validated resume and atomic replacement**

```python
def atomic_write_jsonl(path, records):
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
```

Validate schema version, run ID, source batch hash, question provenance, prompt hashes, immutable revisions, response hash, and candidate ID before skipping an existing `(run_id, question_id)` record.

- [ ] **Step 4: Run persistence tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_game_candidate_generation.CandidatePersistenceTests -v`

Expected: overwrite, resume, tamper, duplicate, and interrupted-run tests all pass.

---

### Task 3: Add the Free Colab Execution Path and Verify the Slice

**Files:**
- Create: `notebooks/generate_game_candidates_colab.ipynb`
- Modify: `tests/test_game_candidate_generation.py`

**Interfaces:**
- Consumes: repository checkout, validated game questions, frozen holdout only for exclusion checking, and a T4-or-better Colab GPU.
- Produces: a downloaded raw candidate JSONL file whose records contain immutable base and adapter commit SHAs.

- [ ] **Step 1: Add a failing notebook-contract test**

```python
notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
source = "\n".join(
    line for cell in notebook["cells"] for line in cell.get("source", [])
)
self.assertIn("model_info", source)
self.assertIn("FastLanguageModel.from_pretrained", source)
self.assertIn("PeftModel.from_pretrained", source)
self.assertIn("files.download", source)
```

- [ ] **Step 2: Run the notebook test and verify RED**

Run: `.venv/bin/python -m unittest tests.test_game_candidate_generation.ColabNotebookContractTests -v`

Expected: failure because the notebook does not exist.

- [ ] **Step 3: Create the Colab notebook**

Resolve both requested revisions through `model_info(...).sha`, load the pinned 4-bit base once, attach the pinned v7.1 adapter once, call `FastLanguageModel.for_inference(model)`, pass one injected backend through `generate_candidate_batch`, and download the output with `google.colab.files.download`.

- [ ] **Step 4: Run fresh verification**

Run: `.venv/bin/python -m unittest tests.test_game_candidate_generation -v`

Run: `.venv/bin/python -m unittest discover -s tests -v`

Run: `.venv/bin/python -m py_compile src/game_candidate_generation.py tests/test_game_candidate_generation.py`

Run: `git diff --check`

Expected: all tests pass, compilation exits zero, and the diff check reports no whitespace errors.

---

### Task 4: Close the Uncommitted-Colab Provenance Gap

**Files:**
- Create: `tests/test_game_colab_bundle.py`
- Create: `src/game_colab_bundle.py`
- Modify: `notebooks/generate_game_candidates_colab.ipynb`
- Modify: `src/game_candidate_generation.py`

**Interfaces:**
- Consumes: an exact allowlist of generator source files, the original game question bank, and the frozen 140-row holdout.
- Produces: a deterministic zip with a hash manifest, a sealed `ValidatedQuestionBatch` receipt, and a fresh-directory Colab upload/extraction path.

- [ ] **Step 1: Write failing receipt and bundle tests**

```python
batch = load_validated_question_batch(questions_path, frozen_holdout_path)
self.assertEqual(batch.holdout_count, 140)
self.assertEqual(batch.holdout_sha256, FROZEN_HOLDOUT_SHA256)

first = build_colab_bundle(ROOT, first_path)
second = build_colab_bundle(ROOT, second_path)
self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
self.assertEqual(first["files"], second["files"])
```

Assert that generation rejects a plain self-asserted list, bundle verification rejects
unexpected or traversal paths, every allowed file hash matches, and every candidate
contains the exact generator source SHA-256.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_game_colab_bundle -v`

Expected: import failure because `src.game_colab_bundle` does not exist.

- [ ] **Step 3: Implement the deterministic bundle and sealed receipt**

Use a fixed zip timestamp and permissions, an exact relative-path allowlist, stable JSON
for the manifest, and SHA-256 over each exact file. Refuse an existing output. Require
the exact 140-record frozen holdout hash before returning the batch receipt; generation
accepts only that receipt and records `generator_source_sha256`.

- [ ] **Step 4: Replace clone-main with verified upload**

The notebook first uploads one bundle, rejects duplicate/unexpected/absolute/parent
paths, verifies the manifest and every file hash in memory, and writes into a fresh
temporary directory before importing project code. Install the known training runtime
with `unsloth==2026.7.1`.

- [ ] **Step 5: Run full verification**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Run: `.venv/bin/python -m py_compile src/game_candidate_generation.py src/game_colab_bundle.py`

Run: `.venv/bin/python -m json.tool notebooks/generate_game_candidates_colab.ipynb`

Expected: all tests pass and both Python and notebook syntax checks exit zero.

---

### Task 5: Bind the Actual Colab Backend Source

**Files:**
- Create: `tests/test_game_colab_backend.py`
- Create: `src/game_colab_backend.py`
- Modify: `src/game_candidate_generation.py`
- Modify: `src/game_colab_bundle.py`
- Modify: `notebooks/generate_game_candidates_colab.ipynb`

**Interfaces:**
- Consumes: requested Hugging Face revisions and the locked deterministic parameters.
- Produces: lazy GPU/model loading, exact decoded completions, and a required `backend_source_sha256` candidate field.

- [ ] **Step 1: Write failing backend tests**

```python
self.assertEqual(
    BACKEND_SOURCE_SHA256,
    hashlib.sha256(Path(backend_module.__file__).read_bytes()).hexdigest(),
)
self.assertEqual(fake_tokenizer.chat_kwargs["enable_thinking"], False)
self.assertEqual(result, " exact decoded completion ")
```

- [ ] **Step 2: Run the backend tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_game_colab_backend -v`

Expected: import failure because `src.game_colab_backend` does not exist.

- [ ] **Step 3: Implement a lazily imported pinned backend**

Keep Torch, Unsloth, PEFT, NumPy, and Hugging Face imports inside
`load_pinned_colab_backend`. Resolve both revisions to 40-hex commits, load the base and
adapter once, and expose a callable backend that refuses generation parameters other
than the locked mapping.

- [ ] **Step 4: Bind and bundle the backend source**

Add `backend_source_sha256` to `GenerationProvenance`, every candidate, resume checks,
the exact raw schema, the bundle manifest, and the run manifest. Import the backend from
the verified fresh bundle instead of defining model code in the notebook.

- [ ] **Step 5: Verify the complete trust chain**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: backend, bundle, generator, validator, review, export, and game tests all pass.
