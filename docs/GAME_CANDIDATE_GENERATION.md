# Glitch Rally Raw Candidate Generation

Raw candidate files are an audit artifact between offline SLM inference and strict
content validation. They are not an approved game pack. A candidate can reach the game
only after deterministic validation and hash-bound owner review.

## Free Colab run

First build a deterministic upload bundle from the current working tree:

```bash
.venv/bin/python -m src.game_colab_bundle \
  --output glitch_rally_colab_bundle.zip
```

Then open `notebooks/generate_game_candidates_colab.ipynb` in Google Colab, select a
free T4 GPU, choose a unique `RUN_ID`, run all cells, and upload that bundle when
prompted. The notebook:

1. validates every original question against the frozen holdout exclusion gate;
2. verifies an exact source-file allowlist, bundle manifest, generator and backend
   source hashes, and the pinned 140-record holdout receipt before importing code;
3. resolves the requested Hugging Face base and adapter revisions to immutable commits;
4. loads the pinned 4-bit base and final v7.1 adapter once through the verified backend;
5. generates with `do_sample=False`, `max_new_tokens=512`, and
   `enable_thinking=False`;
6. atomically checkpoints one JSONL record after every response; and
7. downloads the raw JSONL output plus a run manifest with the bundle, runtime,
   immutable model, and output hashes.

Keep the same run ID, model revisions, question batch, and output path when resuming.
Start a new output file for any changed input. The writer never silently overwrites an
existing output.

## JSONL schema: `glitch-rally-candidate-v1`

Every line is one JSON object with exactly these fields:

| Field | Meaning |
|---|---|
| `schema_version` | Literal `glitch-rally-candidate-v1`. |
| `generator_version` | Version of the CPU-safe orchestration module. |
| `generator_source_sha256` | Hash of the exact CPU orchestration module bytes. |
| `backend_source_sha256` | Hash of the exact Colab loading/decoding backend bytes. |
| `run_id` | Non-PII lowercase run identity matching `[a-z0-9][a-z0-9._-]{2,79}`. |
| `candidate_id` | `candidate:v1:` plus the stable candidate identity hash. |
| `question_id` | Original game question ID such as `GR-NUM-001`. |
| `question` | Exact question supplied to `build_user`. |
| `correct` | Exact trusted answer supplied to `build_user`. |
| `topic` | Exact trained Number taxonomy label supplied to `build_user`. |
| `question_hash` | The question fingerprint produced by `src.game_content`. |
| `model_id` | Base model repository, default `unsloth/Qwen3-4B-bnb-4bit`. |
| `model_revision` | Resolved immutable 40-hex base-model commit. |
| `adapter_id` | Adapter repository, default `j2ampn/qwen3-4b-distractor-lora-v7`. |
| `adapter_revision` | Resolved immutable 40-hex adapter commit. |
| `system_prompt_sha256` | Hash of the exact `SYSTEM_PROMPT` text. |
| `user_prompt_sha256` | Hash of the exact `build_user(...)` result. |
| `prompt_sha256` | Hash of the stable JSON prompt pair described below. |
| `generation_parameters` | Locked deterministic generation mapping. |
| `source_batch_sha256` | Hash of the ordered validated question-record list. |
| `question_record_sha256` | Hash of the complete enriched validated question record. |
| `generated_at_utc` | UTC generation timestamp for audit display. |
| `raw_response` | Exact decoded model completion, retained even when malformed. |
| `raw_response_sha256` | Hash of the exact raw response text. |

## Canonical hashing

Plain text hashes are lowercase SHA-256 hex over the exact UTF-8 bytes, with no newline
added. Stable JSON hashes first serialize with:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

The hash payloads are:

```text
system_prompt_sha256  = text_sha256(SYSTEM_PROMPT)
user_prompt_sha256    = text_sha256(build_user(question, correct, topic))
prompt_sha256         = stable_json_sha256({"system": SYSTEM_PROMPT,
                                            "user": build_user(...)})
question_record_sha256 = stable_json_sha256(complete validated question record)
source_batch_sha256    = stable_json_sha256(ordered validated question-record list)
raw_response_sha256    = text_sha256(raw_response)
```

`candidate_id` is `candidate:v1:` plus the stable JSON hash of every candidate field
except `candidate_id` and `generated_at_utc`. It therefore binds the question, prompt,
model and adapter commits, generator and backend source, deterministic parameters, run,
batch, and exact response, while remaining stable if only the audit timestamp changes.

`load_validated_question_batch` returns a sealed `ValidatedQuestionBatch`, not a plain
list. Its receipt exposes:

```text
holdout_count       = 140
holdout_sha256      = 47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693
source_batch_sha256 = stable_json_sha256(ordered validated question records)
```

Generation refuses self-asserted dictionaries, a different or malformed holdout, and
duplicate JSON keys in question, holdout, or resumed candidate JSONL.

## CPU-only use and tests

`src.game_candidate_generation` imports no Torch, Transformers, PEFT, Unsloth, or network
client. It accepts a sealed `LoadedColabBackend` receipt. The production receipt comes
only from `load_pinned_colab_backend`; tests can bind a fake explicitly with:

```python
receipt = make_test_backend_receipt(
    backend=fake_backend,
    model_id="test/base",
    model_revision="1" * 40,
    adapter_id="test/adapter",
    adapter_revision="2" * 40,
    backend_source_sha256="f" * 64,
)
```

Test receipts cannot claim the reviewed production backend hash, so their candidates
fail the production validator by construction. The optional GPU/model dependencies are
imported lazily only when the verified Colab loader is called.

## Trust boundary

The hashes provide reproducibility, review binding, and accidental-mutation detection
under a trusted local operator. They are unkeyed hashes, not signatures or remote
attestation. Someone already authorized to edit the workspace could modify code and
recompute all hashes; owner review must still confirm the intended source bundle and run
manifest before approving content.
