import hashlib
import json
import unittest

from src import game_content
from src.prompts import SYSTEM_PROMPT, build_user


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_json_sha256(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def trusted_question():
    item = {
        "id": "GR-NUM-001",
        "question": "What is 5/8 + 1/4?",
        "correct": "7/8",
        "topic": "Adding and Subtracting Fractions",
        "difficulty": "medium",
        "visual_tool": "model_drone_fraction_strip",
        "trusted_steps": ["Rewrite 1/4 as 2/8.", "Add 5/8 + 2/8 = 7/8."],
        "solver": {"kind": "arithmetic", "expression": "5/8 + 1/4"},
    }
    return game_content.validate_question_bank([item], holdout_questions=[])[0]


def valid_raw_response():
    return json.dumps(
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


def raw_candidate(*, raw_response=None, question_record=None, **overrides):
    question = question_record or trusted_question()
    raw = raw_response if raw_response is not None else valid_raw_response()
    user_prompt = build_user(question["question"], question["correct"], question["topic"])
    record = {
        "schema_version": "glitch-rally-candidate-v1",
        "run_id": "run-2026-07-10-a",
        "question_id": question["id"],
        "question": question["question"],
        "correct": question["correct"],
        "topic": question["topic"],
        "question_hash": question["question_hash"],
        "model_id": "unsloth/Qwen3-4B-bnb-4bit",
        "model_revision": "1" * 40,
        "adapter_id": "j2ampn/qwen3-4b-distractor-lora-v7",
        "adapter_revision": "2" * 40,
        "system_prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "user_prompt_sha256": sha256_text(user_prompt),
        "prompt_sha256": stable_json_sha256(
            {"system": SYSTEM_PROMPT, "user": user_prompt}
        ),
        "generation_parameters": {
            "do_sample": False,
            "max_new_tokens": 512,
            "enable_thinking": False,
        },
        "source_batch_sha256": "3" * 64,
        "question_record_sha256": stable_json_sha256(question),
        "generator_version": "glitch-rally-generator-v1",
        "generator_source_sha256": game_content.current_generator_source_sha256(),
        "backend_source_sha256": game_content.current_backend_source_sha256(),
        "generated_at_utc": "2026-07-10T18:30:00Z",
        "raw_response": raw,
        "raw_response_sha256": sha256_text(raw),
    }
    record.update(overrides)
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"candidate_id", "generated_at_utc"}
    }
    record["candidate_id"] = f"candidate:v1:{stable_json_sha256(payload)}"
    if "candidate_id" in overrides:
        record["candidate_id"] = overrides["candidate_id"]
    return record


class StrictDistractorParsingTests(unittest.TestCase):
    def test_accepts_only_the_exact_three_item_json_contract(self):
        self.assertTrue(hasattr(game_content, "strict_parse_distractors"))

        parsed = game_content.strict_parse_distractors(valid_raw_response())

        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0]["answer"], "1/2")

    def test_rejects_prose_code_fences_and_extra_schema_fields(self):
        self.assertTrue(hasattr(game_content, "strict_parse_distractors"))
        samples = [
            f"Here you go: {valid_raw_response()}",
            f"```json\n{valid_raw_response()}\n```",
            json.dumps({"distractors": json.loads(valid_raw_response())["distractors"], "note": "x"}),
        ]

        for sample in samples:
            with self.subTest(sample=sample[:20]):
                with self.assertRaises(game_content.GameContentError):
                    game_content.strict_parse_distractors(sample)

    def test_rejects_wrong_counts_missing_fields_and_non_string_values(self):
        self.assertTrue(hasattr(game_content, "strict_parse_distractors"))
        distractors = json.loads(valid_raw_response())["distractors"]
        samples = [
            {"distractors": distractors[:2]},
            {"distractors": [dict(distractors[0]), dict(distractors[1]), {"answer": "4"}]},
            {
                "distractors": [
                    dict(distractors[0]),
                    dict(distractors[1]),
                    {**distractors[2], "answer": 0.375},
                ]
            },
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaises(game_content.GameContentError):
                    game_content.strict_parse_distractors(json.dumps(sample))

    def test_rejects_duplicate_json_keys_and_invisible_direction_controls(self):
        duplicate_key = valid_raw_response().replace(
            '"answer":"1/2"',
            '"answer":"999","answer":"1/2"',
            1,
        )
        payload = json.loads(valid_raw_response())
        payload["distractors"][0]["misconception"] = "Adds pieces\u202e backwards"
        bidi = json.dumps(payload, ensure_ascii=False)
        payload["distractors"][0]["misconception"] = "Invisible\u200b label"
        zero_width = json.dumps(payload, ensure_ascii=False)

        for sample in (duplicate_key, bidi, zero_width):
            with self.subTest(sample=sample[:40]):
                with self.assertRaises(game_content.GameContentError):
                    game_content.strict_parse_distractors(sample)

    def test_rejects_non_ascii_digits_in_numeric_fields(self):
        payload = json.loads(valid_raw_response())
        payload["distractors"][0]["answer"] = "１/２"
        payload["distractors"][0]["computation"] = "(５ + １) / (８ + ４) = １/２"

        result = game_content.validate_generation_candidate(
            raw_candidate(raw_response=json.dumps(payload, ensure_ascii=False)),
            trusted_question(),
        )

        self.assertEqual(result["status"], "rejected")
        self.assertIn(
            "strict_parse_failed",
            {issue["code"] for issue in result["issues"]},
        )


class CandidateValidationTests(unittest.TestCase):
    def test_valid_math_and_pinned_provenance_moves_to_owner_review(self):
        self.assertTrue(hasattr(game_content, "validate_generation_candidate"))

        result = game_content.validate_generation_candidate(
            raw_candidate(),
            trusted_question(),
            expected_source_batch_sha256="3" * 64,
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["distractors"]), 3)
        self.assertEqual(result["candidate_hash"], result["raw_candidate"]["candidate_id"])
        self.assertRegex(result["validation_hash"], r"^validation:v1:[0-9a-f]{64}$")

    def test_rejects_mutable_or_tampered_generation_provenance(self):
        self.assertTrue(hasattr(game_content, "validate_generation_candidate"))
        candidate = raw_candidate(
            adapter_revision="main",
            raw_response_sha256="0" * 64,
            system_prompt_sha256="f" * 64,
        )

        result = game_content.validate_generation_candidate(
            candidate,
            trusted_question(),
            expected_source_batch_sha256="3" * 64,
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            {"immutable_revision_required", "raw_response_hash_mismatch", "prompt_hash_mismatch"}
            <= {issue["code"] for issue in result["issues"]}
        )

    def test_rejects_public_run_ids_with_markup_controls_or_pii_shapes(self):
        for run_id in (
            "<img-src=x>",
            "owner@example.com",
            "run-safe\u202eexe",
            "RUN WITH SPACES",
        ):
            with self.subTest(run_id=run_id):
                result = game_content.validate_generation_candidate(
                    raw_candidate(run_id=run_id),
                    trusted_question(),
                )
                self.assertEqual(result["status"], "rejected")
                self.assertIn(
                    "invalid_run_id",
                    {issue["code"] for issue in result["issues"]},
                )

    def test_rejects_question_or_batch_substitution(self):
        self.assertTrue(hasattr(game_content, "validate_generation_candidate"))
        candidate = raw_candidate(question="What is 1 + 1?", source_batch_sha256="4" * 64)

        result = game_content.validate_generation_candidate(
            candidate,
            trusted_question(),
            expected_source_batch_sha256="3" * 64,
        )

        self.assertEqual(result["status"], "rejected")
        self.assertTrue(
            {"question_mismatch", "source_batch_hash_mismatch"}
            <= {issue["code"] for issue in result["issues"]}
        )

    def test_rejects_a_candidate_id_that_no_longer_binds_the_record(self):
        self.assertTrue(hasattr(game_content, "validate_generation_candidate"))
        candidate = raw_candidate()
        candidate["candidate_id"] = "candidate:v1:" + "0" * 64

        result = game_content.validate_generation_candidate(candidate, trusted_question())

        self.assertEqual(result["status"], "rejected")
        self.assertIn(
            "candidate_id_mismatch",
            {issue["code"] for issue in result["issues"]},
        )

    def test_rejects_duplicate_correct_or_unsupported_answers(self):
        self.assertTrue(hasattr(game_content, "validate_generation_candidate"))
        payload = json.loads(valid_raw_response())
        payload["distractors"][0]["answer"] = "7/8"
        payload["distractors"][0]["computation"] = "5/8 + 1/4 = 7/8"
        payload["distractors"][1]["answer"] = "0.875"
        payload["distractors"][1]["computation"] = "5/8 + 1/4 = 0.875"
        payload["distractors"][2]["answer"] = "37.5%"

        result = game_content.validate_generation_candidate(
            raw_candidate(raw_response=json.dumps(payload)),
            trusted_question(),
        )

        self.assertEqual(result["status"], "rejected")
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("answer_equals_correct", codes)
        self.assertIn("duplicate_answer", codes)
        self.assertIn("unsupported_answer", codes)

    def test_rejects_rhs_lhs_grounding_and_label_failures(self):
        self.assertTrue(hasattr(game_content, "validate_generation_candidate"))
        payload = json.loads(valid_raw_response())
        payload["distractors"][0]["computation"] = "(5 + 1) / (8 + 4) = 2/3"
        payload["distractors"][1]["computation"] = "9 * 9 = 81 = 5/32"
        payload["distractors"][2]["misconception"] = payload["distractors"][0]["misconception"].upper()

        result = game_content.validate_generation_candidate(
            raw_candidate(raw_response=json.dumps(payload)),
            trusted_question(),
        )

        self.assertEqual(result["status"], "rejected")
        codes = {issue["code"] for issue in result["issues"]}
        self.assertIn("rhs_answer_mismatch", codes)
        self.assertIn("equals_count", codes)
        self.assertIn("duplicate_misconception", codes)

    def test_rejects_a_misconception_label_without_letters_or_numbers(self):
        for label in ("!!!", "___"):
            with self.subTest(label=label):
                payload = json.loads(valid_raw_response())
                payload["distractors"][0]["misconception"] = label

                result = game_content.validate_generation_candidate(
                    raw_candidate(raw_response=json.dumps(payload)),
                    trusted_question(),
                )

                self.assertEqual(result["status"], "rejected")
                self.assertIn(
                    "invalid_misconception",
                    {issue["code"] for issue in result["issues"]},
                )


if __name__ == "__main__":
    unittest.main()
