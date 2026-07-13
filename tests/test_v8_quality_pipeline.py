import json
import unittest

from src.judge_v8 import estimate_quality_run, parse_quality_verdict
from src.v8_teacher import (
    DETERMINISTIC_TEACHER_ROUTE,
    filter_teacher_records,
    supported_procedure_labels,
)


class V8QualityPipelineTests(unittest.TestCase):
    def test_strict_proxy_threshold_is_derived_not_model_declared(self):
        parsed = parse_quality_verdict(
            json.dumps(
                {
                    "binding_valid": True,
                    "misconception_specific": True,
                    "plausibility_score": 3,
                    "diagnostic_value_score": 4,
                }
            )
        )

        self.assertTrue(parsed["plausibility_pass"])
        self.assertEqual(parsed["quality_threshold"], "both_scores>=3/4")

        failed = parse_quality_verdict(
            json.dumps(
                {
                    "binding_valid": True,
                    "misconception_specific": True,
                    "plausibility_score": 2,
                    "diagnostic_value_score": 4,
                }
            )
        )
        self.assertFalse(failed["plausibility_pass"])

    def test_quality_parser_rejects_missing_or_out_of_range_scores(self):
        self.assertIsNone(parse_quality_verdict('{"binding_valid":true}'))
        self.assertIsNone(
            parse_quality_verdict(
                '{"binding_valid":true,"misconception_specific":true,'
                '"plausibility_score":5,"diagnostic_value_score":4}'
            )
        )

    def test_quality_run_estimate_is_call_and_token_capped(self):
        estimate = estimate_quality_run(
            [{"id": "q1", "distractors": [{}, {}, {}]}],
            max_output_tokens=120,
        )

        self.assertEqual(estimate["requests"], 3)
        self.assertEqual(estimate["max_output_tokens_total"], 360)
        self.assertIn("TrueFoundry", estimate["dollar_cost"])

    def test_deterministic_teacher_filter_uses_registered_procedures_without_verdicts(self):
        pool = [
            {
                "id": "q1",
                "question": "What is 1/2 + 1/3?",
                "correct": "5/6",
                "topic": "Adding and Subtracting Fractions",
            },
        ]
        predictions = [
            {
                "id": "q1",
                "generator_model": "anthropic-primary/claude-opus-4-8",
                "generation_route": DETERMINISTIC_TEACHER_ROUTE,
                "distractors": [
                    {
                        "misconception": "Adds the numerators and the denominators",
                        "computation": "(1 + 1) / (2 + 3) = 2/5",
                        "answer": "2/5",
                    },
                    {
                        "misconception": "Adds numerators and keeps the first denominator",
                        "computation": "(1 + 1) / 2 = 1",
                        "answer": "1",
                    },
                    {
                        "misconception": "Multiplies the fractions instead of adding them",
                        "computation": "1/2 * 1/3 = 1/6",
                        "answer": "1/6",
                    },
                ],
            },
        ]

        records, report = filter_teacher_records(
            pool,
            predictions,
            minimum_survivors=1,
        )

        self.assertIn(
            "Adds the numerators and the denominators",
            supported_procedure_labels("Adding and Subtracting Fractions"),
        )
        self.assertEqual(report["route"], DETERMINISTIC_TEACHER_ROUTE)
        self.assertEqual(report["candidates"], 1)
        self.assertEqual(report["survivors"], 1)
        self.assertTrue(report["ready"])
        self.assertFalse(report["opus_judge_used"])
        self.assertEqual(records[0]["meta"]["id"], "q1")
        self.assertEqual(
            records[0]["meta"]["teacher_model"],
            "anthropic-primary/claude-opus-4-8",
        )
        self.assertEqual(
            records[0]["meta"]["teacher_filter_route"],
            DETERMINISTIC_TEACHER_ROUTE,
        )
        self.assertEqual(len(records[0]["meta"]["procedure_ids"]), 3)
        self.assertNotIn("judge_models", records[0]["meta"])

    def test_deterministic_teacher_filter_rejects_unsupported_mapping_and_leakage(self):
        pool = [
            {
                "id": "q1",
                "question": "What is 1/2 + 1/3?",
                "correct": "5/6",
                "topic": "Adding and Subtracting Fractions",
            },
        ]
        prediction = {
            "id": "q1",
            "generator_model": "anthropic-primary/claude-opus-4-8",
            "generation_route": DETERMINISTIC_TEACHER_ROUTE,
            "distractors": [
                {
                    "misconception": "Adds everything",
                    "computation": "(1 + 1) / (2 + 3) = 2/5",
                    "answer": "2/5",
                },
                {
                    "misconception": "Adds numerators and keeps the first denominator",
                    "computation": "(1 + 1) / 2 = 1",
                    "answer": "1",
                },
                {
                    "misconception": "Multiplies the fractions instead of adding them",
                    "computation": "1/2 * 1/3 = 1/6",
                    "answer": "1/6",
                },
            ],
        }

        records, report = filter_teacher_records(
            pool,
            [prediction],
            minimum_survivors=1,
        )
        self.assertEqual(records, [])
        self.assertEqual(
            report["failure_reasons"],
            {"unsupported_procedure_mapping": 1},
        )
        self.assertFalse(report["ready"])

        prediction["distractors"][0]["misconception"] = (
            "Adds the numerators and the denominators"
        )
        records, report = filter_teacher_records(
            pool,
            [prediction],
            forbidden_questions=[
                {"id": "other", "question": " what is \\(1/2+1/3\\)? "}
            ],
            minimum_survivors=1,
        )
        self.assertEqual(records, [])
        self.assertEqual(report["failure_reasons"], {"leakage": 1})

    def test_deterministic_teacher_filter_enforces_predeclared_survivor_floor(self):
        records, report = filter_teacher_records(
            [],
            [],
            minimum_survivors=20,
        )

        self.assertEqual(records, [])
        self.assertEqual(report["minimum_survivors"], 20)
        self.assertFalse(report["ready"])


if __name__ == "__main__":
    unittest.main()
