import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import src.game_candidate_generation as generation_module
from src.game_colab_backend import (
    BACKEND_SOURCE_SHA256,
    make_test_backend_receipt,
)
from src.game_candidate_generation import (
    DEFAULT_ADAPTER_ID,
    DEFAULT_MODEL_ID,
    GENERATION_PARAMETERS,
    GENERATOR_VERSION,
    SCHEMA_VERSION,
    CandidateGenerationError,
    GenerationProvenance,
    candidate_identity_sha256,
    generate_candidate_batch,
    load_validated_question_batch,
    sha256_text,
    stable_json_sha256,
)
from src.game_content import (
    GameContentError,
    validate_generation_candidate,
    validate_question_bank,
)
from src.prompts import SYSTEM_PROMPT, build_user


ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = "1" * 40
ADAPTER_REVISION = "2" * 40
TEST_BACKEND_SOURCE_SHA256 = "f" * 64
RAW_RESPONSE = (
    '{"distractors":[{"misconception":"Adds every visible number",'
    '"computation":"5 + 8 + 1 + 4 = 18","answer":"18"}]}'
)
VALID_RAW_RESPONSE = json.dumps(
    {
        "distractors": [
            {
                "misconception": "Adds numerators and denominators directly",
                "computation": "(5 + 1) / (8 + 4) = 1/2",
                "answer": "1/2",
            },
            {
                "misconception": "Multiplies instead of adding",
                "computation": "(5 * 1) / (8 * 4) = 5/32",
                "answer": "5/32",
            },
            {
                "misconception": "Subtracts instead of adding",
                "computation": "5/8 - 1/4 = 3/8",
                "answer": "3/8",
            },
        ]
    },
    ensure_ascii=False,
    separators=(",", ":"),
)


def sample_question(question_id="GR-NUM-001", **overrides):
    item = {
        "id": question_id,
        "question": (
            "A rally car's battery is 5/8 charged. A solar patch adds 1/4 "
            "of a full charge. What fraction of a full charge does it have now?"
        ),
        "correct": "7/8",
        "topic": "Adding and Subtracting Fractions",
        "difficulty": "medium",
        "visual_tool": "model_drone_fraction_strip",
        "trusted_steps": [
            "Rewrite 1/4 as 2/8.",
            "Add equal pieces: 5/8 + 2/8 = 7/8.",
        ],
        "solver": {"kind": "arithmetic", "expression": "5/8 + 1/4"},
    }
    item.update(overrides)
    return item


def validated_questions(*items):
    with tempfile.TemporaryDirectory() as directory:
        questions_path = Path(directory) / "questions.jsonl"
        questions_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n" for item in items
            ),
            encoding="utf-8",
        )
        return load_validated_question_batch(
            questions_path,
            ROOT / "data/processed/eval_heldout.jsonl",
        )


def provenance(run_id="glitch-rally-v1-test"):
    return GenerationProvenance(
        run_id=run_id,
        model_revision=BASE_REVISION,
        adapter_revision=ADAPTER_REVISION,
        backend_source_sha256=TEST_BACKEND_SOURCE_SHA256,
    )


def backend_receipt(backend):
    return make_test_backend_receipt(
        backend=backend,
        model_id=DEFAULT_MODEL_ID,
        model_revision=BASE_REVISION,
        adapter_id=DEFAULT_ADAPTER_ID,
        adapter_revision=ADAPTER_REVISION,
        backend_source_sha256=TEST_BACKEND_SOURCE_SHA256,
    )


class FakeBackend:
    def __init__(self, responses=None):
        self.responses = list(responses or [RAW_RESPONSE])
        self.calls = []

    def __call__(self, system_prompt, user_prompt, generation_parameters):
        self.calls.append(
            (system_prompt, user_prompt, dict(generation_parameters))
        )
        return self.responses[len(self.calls) - 1]


class HashContractTests(unittest.TestCase):
    def test_generator_version_matches_the_validator_contract(self):
        self.assertEqual(GENERATOR_VERSION, "glitch-rally-generator-v1")

    def test_stable_json_hash_uses_sorted_compact_unicode_json(self):
        value = {"z": "café", "a": [2, 1]}
        canonical = '{"a":[2,1],"z":"café"}'

        self.assertEqual(
            stable_json_sha256(value),
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            stable_json_sha256({"a": [2, 1], "z": "café"}),
            stable_json_sha256(value),
        )


class ValidatedQuestionLoadingTests(unittest.TestCase):
    def test_rejects_duplicate_keys_in_question_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            questions_path = Path(directory) / "questions.jsonl"
            encoded = json.dumps(sample_question(), ensure_ascii=False)
            questions_path.write_text(
                '{"id":"shadow",' + encoded[1:] + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                CandidateGenerationError,
                "question batch JSONL line 1.*duplicate JSON key.*id",
            ):
                load_validated_question_batch(
                    questions_path,
                    ROOT / "data/processed/eval_heldout.jsonl",
                )

    def test_rejects_duplicate_keys_in_frozen_holdout_jsonl(self):
        holdout_lines = (
            ROOT / "data/processed/eval_heldout.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        holdout_lines[0] = '{"id":"shadow",' + holdout_lines[0][1:]

        with tempfile.TemporaryDirectory() as directory:
            questions_path = Path(directory) / "questions.jsonl"
            holdout_path = Path(directory) / "holdout.jsonl"
            questions_path.write_text(
                json.dumps(sample_question()) + "\n",
                encoding="utf-8",
            )
            holdout_path.write_text(
                "\n".join(holdout_lines) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                CandidateGenerationError,
                "frozen holdout JSONL line 1.*duplicate JSON key.*id",
            ):
                load_validated_question_batch(questions_path, holdout_path)

    def test_receipt_pins_the_frozen_holdout_and_source_batch(self):
        batch = load_validated_question_batch(
            ROOT / "data/game/questions_v1.jsonl",
            ROOT / "data/processed/eval_heldout.jsonl",
        )

        self.assertTrue(
            isinstance(batch, generation_module.ValidatedQuestionBatch)
        )
        self.assertEqual(batch.holdout_count, 140)
        self.assertEqual(
            batch.holdout_sha256,
            "47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693",
        )
        self.assertEqual(batch.source_batch_sha256, stable_json_sha256(list(batch)))

    def test_loads_jsonl_through_the_question_bank_integrity_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions_path = root / "questions.jsonl"
            questions_path.write_text(
                json.dumps(sample_question(), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            loaded = load_validated_question_batch(
                questions_path,
                ROOT / "data/processed/eval_heldout.jsonl",
            )

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "GR-NUM-001")
        self.assertIn("canonical_question", loaded[0])
        self.assertTrue(loaded[0]["question_hash"].startswith("question:v1:"))

    def test_rejects_a_tampered_frozen_holdout_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions_path = root / "questions.jsonl"
            holdout_path = root / "holdout.jsonl"
            questions_path.write_text(
                json.dumps(sample_question()) + "\n",
                encoding="utf-8",
            )
            holdout_path.write_text(
                json.dumps({"id": "heldout-1", "question": "What is 1 + 1?"}) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                CandidateGenerationError,
                "frozen holdout receipt mismatch",
            ):
                load_validated_question_batch(questions_path, holdout_path)


class CandidateRecordTests(unittest.TestCase):
    def test_rejects_unsafe_or_identifying_run_ids(self):
        invalid = [
            "ab",
            "Run-Uppercase",
            "student@example.com",
            "contains spaces",
            "<script>",
            "rún-id",
            "_leading-symbol",
            "a" * 81,
        ]
        questions = validated_questions(sample_question())

        for run_id in invalid:
            with self.subTest(run_id=run_id), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(
                    CandidateGenerationError,
                    "run_id must match",
                ):
                    generate_candidate_batch(
                        questions=questions,
                        output_path=Path(directory) / "raw.jsonl",
                        backend=backend_receipt(FakeBackend()),
                        provenance=GenerationProvenance(
                            run_id=run_id,
                            model_revision=BASE_REVISION,
                            adapter_revision=ADAPTER_REVISION,
                            backend_source_sha256=TEST_BACKEND_SOURCE_SHA256,
                        ),
                    )

    def test_rejects_an_unbound_callable_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                CandidateGenerationError,
                "LoadedColabBackend receipt",
            ):
                generate_candidate_batch(
                    questions=validated_questions(sample_question()),
                    output_path=Path(directory) / "raw.jsonl",
                    backend=FakeBackend(),
                    provenance=provenance(),
                )

    def test_rejects_a_plain_self_asserted_question_list(self):
        forged = validate_question_bank([sample_question()], holdout_questions=[])

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                CandidateGenerationError,
                "ValidatedQuestionBatch receipt",
            ):
                generate_candidate_batch(
                    questions=forged,
                    output_path=Path(directory) / "raw.jsonl",
                    backend=FakeBackend(),
                    provenance=provenance(),
                )

    def test_record_binds_generator_and_declared_test_backend_sources(self):
        questions = validated_questions(sample_question())

        with tempfile.TemporaryDirectory() as directory:
            record = generate_candidate_batch(
                questions=questions,
                output_path=Path(directory) / "raw.jsonl",
                backend=backend_receipt(FakeBackend()),
                provenance=provenance(),
            )[0]

        source_path = Path(generation_module.__file__)
        expected = hashlib.sha256(source_path.read_bytes()).hexdigest()
        self.assertEqual(record["generator_source_sha256"], expected)
        self.assertEqual(
            record["backend_source_sha256"],
            TEST_BACKEND_SOURCE_SHA256,
        )

    def test_rejects_an_unpinned_backend_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                CandidateGenerationError,
                "backend_source_sha256.*64",
            ):
                generate_candidate_batch(
                    questions=validated_questions(sample_question()),
                    output_path=Path(directory) / "raw.jsonl",
                    backend=FakeBackend(),
                    provenance=GenerationProvenance(
                        run_id="bad-backend-source",
                        model_revision=BASE_REVISION,
                        adapter_revision=ADAPTER_REVISION,
                        backend_source_sha256="mutable",
                    ),
                )

    def test_fake_backend_record_cannot_cross_the_production_validator_boundary(self):
        questions = validated_questions(sample_question())

        with tempfile.TemporaryDirectory() as directory:
            records = generate_candidate_batch(
                questions=questions,
                output_path=Path(directory) / "raw.jsonl",
                backend=backend_receipt(FakeBackend([VALID_RAW_RESPONSE])),
                provenance=provenance(),
                clock=lambda: "2026-07-10T12:00:00Z",
            )

        result = validate_generation_candidate(
            records[0],
            questions[0],
            expected_source_batch_sha256=questions.source_batch_sha256,
        )

        self.assertEqual(result["status"], "rejected")
        self.assertIn(
            "backend_source_hash_mismatch",
            {issue["code"] for issue in result["issues"]},
        )

    def test_generation_uses_locked_parameters_and_retains_the_raw_response(self):
        questions = validated_questions(sample_question())
        backend = FakeBackend()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw_candidates.jsonl"
            records = generate_candidate_batch(
                questions=questions,
                output_path=output,
                backend=backend_receipt(backend),
                provenance=provenance(),
                clock=lambda: "2026-07-10T12:00:00Z",
            )

            persisted = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(
            backend.calls,
            [
                (
                    SYSTEM_PROMPT,
                    build_user(
                        questions[0]["question"],
                        questions[0]["correct"],
                        questions[0]["topic"],
                    ),
                    GENERATION_PARAMETERS,
                )
            ],
        )
        self.assertEqual(records, persisted)
        self.assertEqual(records[0]["raw_response"], RAW_RESPONSE)
        self.assertEqual(records[0]["raw_response_sha256"], sha256_text(RAW_RESPONSE))

    def test_record_contains_recomputable_prompt_batch_and_candidate_hashes(self):
        questions = validated_questions(sample_question())
        backend = FakeBackend()

        with tempfile.TemporaryDirectory() as directory:
            records = generate_candidate_batch(
                questions=questions,
                output_path=Path(directory) / "raw.jsonl",
                backend=backend_receipt(backend),
                provenance=provenance(),
                clock=lambda: "2026-07-10T12:00:00Z",
            )

        record = records[0]
        user_prompt = build_user(
            questions[0]["question"],
            questions[0]["correct"],
            questions[0]["topic"],
        )
        self.assertEqual(record["schema_version"], SCHEMA_VERSION)
        self.assertEqual(record["model_id"], DEFAULT_MODEL_ID)
        self.assertEqual(record["adapter_id"], DEFAULT_ADAPTER_ID)
        self.assertEqual(record["model_revision"], BASE_REVISION)
        self.assertEqual(record["adapter_revision"], ADAPTER_REVISION)
        self.assertEqual(record["system_prompt_sha256"], sha256_text(SYSTEM_PROMPT))
        self.assertEqual(record["user_prompt_sha256"], sha256_text(user_prompt))
        self.assertEqual(
            record["prompt_sha256"],
            stable_json_sha256({"system": SYSTEM_PROMPT, "user": user_prompt}),
        )
        self.assertEqual(
            record["question_record_sha256"],
            stable_json_sha256(questions[0]),
        )
        self.assertEqual(
            record["source_batch_sha256"],
            questions.source_batch_sha256,
        )
        self.assertEqual(record["candidate_id"], candidate_identity_sha256(record))

    def test_rejects_mutable_model_revisions_before_calling_the_backend(self):
        backend = FakeBackend()

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CandidateGenerationError, "immutable.*40"):
                generate_candidate_batch(
                    questions=validated_questions(sample_question()),
                    output_path=Path(directory) / "raw.jsonl",
                    backend=backend,
                    provenance=GenerationProvenance(
                        run_id="bad-revision",
                        model_revision="main",
                        adapter_revision=ADAPTER_REVISION,
                        backend_source_sha256=BACKEND_SOURCE_SHA256,
                    ),
                )

        self.assertEqual(backend.calls, [])


class CandidatePersistenceTests(unittest.TestCase):
    def test_resume_rejects_duplicate_keys_in_existing_candidate_jsonl(self):
        questions = validated_questions(sample_question())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.jsonl"
            generate_candidate_batch(
                questions=questions,
                output_path=output,
                backend=backend_receipt(FakeBackend()),
                provenance=provenance(),
            )
            encoded = output.read_text(encoding="utf-8").strip()
            output.write_text(
                '{"run_id":"shadow",' + encoded[1:] + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                CandidateGenerationError,
                "existing candidate output JSONL line 1.*duplicate JSON key.*run_id",
            ):
                generate_candidate_batch(
                    questions=questions,
                    output_path=output,
                    backend=backend_receipt(FakeBackend()),
                    provenance=provenance(),
                    resume=True,
                )

    def test_refuses_to_overwrite_an_existing_output_without_resume(self):
        questions = validated_questions(sample_question())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.jsonl"
            generate_candidate_batch(
                questions=questions,
                output_path=output,
                backend=backend_receipt(FakeBackend()),
                provenance=provenance(),
            )
            second_backend = FakeBackend()

            with self.assertRaisesRegex(CandidateGenerationError, "already exists"):
                generate_candidate_batch(
                    questions=questions,
                    output_path=output,
                    backend=backend_receipt(second_backend),
                    provenance=provenance(),
                )

        self.assertEqual(second_backend.calls, [])

    def test_resume_skips_an_existing_verified_candidate(self):
        questions = validated_questions(sample_question())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.jsonl"
            original = generate_candidate_batch(
                questions=questions,
                output_path=output,
                backend=backend_receipt(FakeBackend()),
                provenance=provenance(),
                clock=lambda: "2026-07-10T12:00:00Z",
            )
            resumed_backend = FakeBackend()

            resumed = generate_candidate_batch(
                questions=questions,
                output_path=output,
                backend=backend_receipt(resumed_backend),
                provenance=provenance(),
                resume=True,
                clock=lambda: "2026-07-10T13:00:00Z",
            )

        self.assertEqual(resumed, original)
        self.assertEqual(resumed_backend.calls, [])

    def test_resume_rejects_a_tampered_existing_record(self):
        questions = validated_questions(sample_question())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.jsonl"
            records = generate_candidate_batch(
                questions=questions,
                output_path=output,
                backend=backend_receipt(FakeBackend()),
                provenance=provenance(),
            )
            records[0]["raw_response_sha256"] = "0" * 64
            output.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(
                CandidateGenerationError,
                "invalid existing record.*raw_response_sha256",
            ):
                generate_candidate_batch(
                    questions=questions,
                    output_path=output,
                    backend=backend_receipt(FakeBackend()),
                    provenance=provenance(),
                    resume=True,
                )

    def test_interrupted_run_checkpoints_then_resume_generates_only_missing_items(self):
        second = sample_question(
            "GR-NUM-002",
            question=(
                "A rover battery is 3/4 charged. A repair adds 1/8 of a full "
                "charge. What fraction is charged now?"
            ),
            correct="7/8",
            trusted_steps=["Rewrite 3/4 as 6/8.", "Add 6/8 + 1/8 = 7/8."],
            solver={"kind": "arithmetic", "expression": "3/4 + 1/8"},
        )
        third = sample_question(
            "GR-NUM-003",
            question=(
                "A fuel cell is 1/2 full and receives another 1/4 of its full "
                "capacity. What fraction is full now?"
            ),
            correct="3/4",
            trusted_steps=["Rewrite 1/2 as 2/4.", "Add 2/4 + 1/4 = 3/4."],
            solver={"kind": "arithmetic", "expression": "1/2 + 1/4"},
        )
        questions = validated_questions(sample_question(), second, third)

        class InterruptingBackend:
            def __init__(self):
                self.calls = []

            def __call__(self, system_prompt, user_prompt, generation_parameters):
                self.calls.append(user_prompt)
                if len(self.calls) == 2:
                    raise RuntimeError("simulated GPU interruption")
                return RAW_RESPONSE

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.jsonl"
            interrupted_backend = InterruptingBackend()
            with self.assertRaisesRegex(RuntimeError, "simulated GPU interruption"):
                generate_candidate_batch(
                    questions=questions,
                    output_path=output,
                    backend=backend_receipt(interrupted_backend),
                    provenance=provenance(),
                )

            checkpointed = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [record["question_id"] for record in checkpointed],
                ["GR-NUM-001"],
            )

            resumed_backend = FakeBackend([RAW_RESPONSE, RAW_RESPONSE])
            completed = generate_candidate_batch(
                questions=questions,
                output_path=output,
                backend=backend_receipt(resumed_backend),
                provenance=provenance(),
                resume=True,
            )

        self.assertEqual(
            [record["question_id"] for record in completed],
            ["GR-NUM-001", "GR-NUM-002", "GR-NUM-003"],
        )
        self.assertEqual(len(resumed_backend.calls), 2)
        self.assertEqual(
            resumed_backend.calls[0][1],
            build_user(
                questions[1]["question"],
                questions[1]["correct"],
                questions[1]["topic"],
            ),
        )


class ColabNotebookContractTests(unittest.TestCase):
    def test_notebook_uses_a_verified_upload_bundle_not_unpinned_main(self):
        notebook_path = ROOT / "notebooks/generate_game_candidates_colab.ipynb"
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        source = "\n".join(
            "".join(cell.get("source", [])) for cell in notebook["cells"]
        )
        self.assertIn("unsloth==2026.7.1", source)
        self.assertIn("files.upload", source)
        self.assertIn("PurePosixPath", source)
        self.assertIn("mkdtemp", source)
        self.assertIn("bundle_id", source)
        self.assertIn("generator_source_sha256", source)
        self.assertIn("backend_source_sha256", source)
        self.assertIn("load_pinned_colab_backend", source)
        self.assertIn('module_name.startswith("src.")', source)
        self.assertNotIn("class ColabUnslothBackend", source)
        self.assertNotIn("git clone", source)
        self.assertNotIn("REPO_URL", source)

    def test_notebook_pins_loads_once_generates_and_downloads(self):
        root = Path(__file__).resolve().parents[1]
        notebook_path = root / "notebooks/generate_game_candidates_colab.ipynb"

        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        source = "\n".join(
            "".join(cell.get("source", [])) for cell in notebook["cells"]
        )
        backend_source = (
            root / "src/game_colab_backend.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(notebook["nbformat"], 4)
        self.assertIn(DEFAULT_MODEL_ID, source)
        self.assertIn(DEFAULT_ADAPTER_ID, source)
        self.assertIn("from huggingface_hub import model_info", backend_source)
        self.assertEqual(backend_source.count("_resolved_revision("), 3)
        self.assertEqual(
            backend_source.count("FastLanguageModel.from_pretrained("),
            1,
        )
        self.assertEqual(backend_source.count("PeftModel.from_pretrained("), 1)
        self.assertIn("load_validated_question_batch", source)
        self.assertIn("generate_candidate_batch", source)
        self.assertIn("files.download", source)
        self.assertIn("enable_thinking", backend_source)


if __name__ == "__main__":
    unittest.main()
