import copy
import unittest

from src.prompts import SYSTEM_PROMPT, build_assistant, build_user
from src.v8_data import (
    DETERMINISTIC_TEACHER_ROUTE,
    assert_no_leakage,
    calibration_artifact_ok,
    deterministic_teacher_records_ok,
    jsonl_sha256,
    opus_access_preflight_ok,
    question_fingerprint,
    stable_partition_unused,
    validate_training_records,
)
from src.v8_targeted import generate_targeted_sft


class V8DataTests(unittest.TestCase):
    def test_unused_partition_is_stable_disjoint_and_excludes_legacy_ids(self):
        rows = [
            {"id": str(i), "question": f"What is {i} + 1?"}
            for i in range(10)
        ]

        teacher_a, benchmark_a = stable_partition_unused(
            rows,
            used_ids={"0", "1"},
            teacher_n=3,
            benchmark_n=4,
            seed=808,
        )
        teacher_b, benchmark_b = stable_partition_unused(
            list(reversed(rows)),
            used_ids={"0", "1"},
            teacher_n=3,
            benchmark_n=4,
            seed=808,
        )

        self.assertEqual(teacher_a, teacher_b)
        self.assertEqual(benchmark_a, benchmark_b)
        teacher_ids = {row["id"] for row in teacher_a}
        benchmark_ids = {row["id"] for row in benchmark_a}
        self.assertFalse(teacher_ids & benchmark_ids)
        self.assertFalse((teacher_ids | benchmark_ids) & {"0", "1"})

    def test_question_fingerprint_normalizes_formatting_but_not_math(self):
        self.assertEqual(
            question_fingerprint(" What  is \\( 2 + 3 \\)? "),
            question_fingerprint("what is 2+3?"),
        )
        self.assertNotEqual(
            question_fingerprint("what is 2+3?"),
            question_fingerprint("what is 2+4?"),
        )

    def test_leakage_check_rejects_id_or_normalized_question_overlap(self):
        train = [
            {
                "user": build_user("What is 2 + 3?", "5", "Addition"),
                "meta": {"id": "train-1"},
            }
        ]
        benchmark_same_text = [
            {"id": "test-2", "question": " what is \\(2+3\\)? "}
        ]
        benchmark_same_id = [
            {"id": "train-1", "question": "What is 8 + 9?"}
        ]

        with self.assertRaisesRegex(ValueError, "question"):
            assert_no_leakage(train, benchmark_same_text)
        with self.assertRaisesRegex(ValueError, "id"):
            assert_no_leakage(train, benchmark_same_id)

    def test_training_validation_enforces_all_structural_and_verifier_gates(self):
        record = {
            "system": SYSTEM_PROMPT,
            "user": build_user("What is 6 / 2?", "3", "Division"),
            "assistant": build_assistant(
                [
                    {
                        "misconception": "Adds instead",
                        "computation": "6 + 2 = 8",
                        "answer": "8",
                    },
                    {
                        "misconception": "Subtracts instead",
                        "computation": "6 - 2 = 4",
                        "answer": "4",
                    },
                    {
                        "misconception": "Reverses division",
                        "computation": "2 / 6 = 1/3",
                        "answer": "1/3",
                    },
                ]
            ),
            "meta": {"id": "train-1", "source": "synthetic"},
        }

        report = validate_training_records([record])

        self.assertEqual(report["records"], 1)
        self.assertEqual(report["verified_pairs"], 3)
        self.assertEqual(report["pair_consistency"], 1.0)

        invalid = copy.deepcopy(record)
        invalid["assistant"] = build_assistant(
            [
                {
                    "misconception": "Adds instead",
                    "computation": "6 + 2 = 8",
                    "answer": "3",
                },
                {
                    "misconception": "Subtracts instead",
                    "computation": "6 - 2 = 4",
                    "answer": "4",
                },
                {
                    "misconception": "Reverses division",
                    "computation": "2 / 6 = 1/3",
                    "answer": "1/3",
                },
            ]
        )
        with self.assertRaises(ValueError):
            validate_training_records([invalid])

    def test_jsonl_digest_is_deterministic_and_content_sensitive(self):
        rows = [{"b": 2, "a": 1}, {"id": "x"}]

        first = jsonl_sha256(rows)
        second = jsonl_sha256(rows)
        changed = jsonl_sha256([{"b": 3, "a": 1}, {"id": "x"}])

        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_opus_access_preflight_requires_exact_successful_model(self):
        self.assertTrue(
            opus_access_preflight_ok(
                {
                    "model": "anthropic-primary/claude-opus-4-8",
                    "stream_ok": True,
                    "nonstream_ok": True,
                    "repository_client_ok": True,
                }
            )
        )
        self.assertFalse(
            opus_access_preflight_ok(
                {
                    "model": "claude-opus-4-8",
                    "stream_ok": True,
                    "nonstream_ok": True,
                    "repository_client_ok": True,
                }
            )
        )

    def test_calibration_artifact_requires_acceptance_model_and_scope(self):
        artifact = {
            "accepted": True,
            "model": "anthropic-primary/claude-opus-4-8",
            "scope": "numeric misconception-answer binding",
        }
        self.assertTrue(calibration_artifact_ok(artifact, "numeric"))
        self.assertFalse(
            calibration_artifact_ok({**artifact, "accepted": False}, "numeric")
        )
        self.assertFalse(calibration_artifact_ok(artifact, "nonnumeric"))

    def test_deterministic_teacher_readiness_is_independent_of_rejected_opus_judge(self):
        records = []
        for index in range(20):
            records.append(
                {
                    "user": f"Question: {index}",
                    "meta": {
                        "id": f"teacher-{index}",
                        "source": "opus_distilled_real_question",
                        "teacher_model": "anthropic-primary/claude-opus-4-8",
                        "teacher_filter_route": DETERMINISTIC_TEACHER_ROUTE,
                        "teacher_filter_version": "v1",
                        "procedure_registry_id": "wayline-procedures-v1",
                        "procedure_ids": ["p1", "p2", "p3"],
                        "opus_judge_used": False,
                        "task_quality_proxy": "not_available",
                    },
                }
            )
        report = {
            "route": DETERMINISTIC_TEACHER_ROUTE,
            "filter_version": "v1",
            "procedure_registry_id": "wayline-procedures-v1",
            "minimum_survivors": 20,
            "survivors": 20,
            "ready": True,
            "opus_judge_used": False,
            "task_quality_proxy": "not_available",
        }

        self.assertTrue(
            deterministic_teacher_records_ok(records, report)
        )
        self.assertFalse(
            calibration_artifact_ok(
                {
                    "accepted": False,
                    "model": "anthropic-primary/claude-opus-4-8",
                    "scope": "numeric misconception-answer binding",
                },
                "numeric",
            )
        )
        self.assertFalse(
            deterministic_teacher_records_ok(records[:19], report)
        )
        records[0]["meta"]["opus_judge_used"] = True
        self.assertFalse(
            deterministic_teacher_records_ok(records, report)
        )

    def test_targeted_uncovered_topic_data_is_deterministic_and_verified(self):
        first = generate_targeted_sft(per_family=100, seed=91)
        second = generate_targeted_sft(per_family=100, seed=91)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 1200)
        report = validate_training_records(first)
        self.assertEqual(report["pair_consistency"], 1.0)
        topics = {record["meta"]["topic"] for record in first}
        self.assertIn("Equivalent Fractions", topics)
        self.assertIn("Standard Form", topics)
        self.assertIn("Rounding to Significant Figures", topics)
        self.assertIn(
            "Converting Mixed Number and Improper Fractions",
            topics,
        )


if __name__ == "__main__":
    unittest.main()
