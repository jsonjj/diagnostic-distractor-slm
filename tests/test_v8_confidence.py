import math
import unittest

from src.confidence import (
    apply_binding_calibration,
    confidence_metrics,
    confidence_payload,
    ensure_confidence_schema,
    fit_binary_verdict_calibration,
)
from src.prompts import parse_distractors


class V8ConfidenceTests(unittest.TestCase):
    def test_legacy_prediction_gets_explicit_uncalibrated_confidence(self):
        prediction = {
            "id": "q1",
            "distractors": [
                {
                    "misconception": "Adds denominators",
                    "computation": "(1 + 1)/(2 + 3) = 0.4",
                    "answer": "0.4",
                }
            ],
        }

        enriched = ensure_confidence_schema(prediction)

        self.assertNotIn("confidence", prediction["distractors"][0])
        self.assertEqual(
            enriched["distractors"][0]["confidence"],
            {
                "target": "misconception_answer_consistency",
                "probability": None,
                "level": "not_calibrated",
                "calibrated": False,
                "source": "unavailable",
                "calibration_id": None,
            },
        )
        self.assertEqual(
            enriched["question_confidence"]["target"],
            "all_three_distractors_valid",
        )
        self.assertIsNone(enriched["question_confidence"]["probability"])

    def test_probability_requires_versioned_external_calibration(self):
        with self.assertRaisesRegex(ValueError, "calibration_id"):
            confidence_payload(
                0.91,
                target="misconception_answer_consistency",
                source="model_self_report",
            )

        payload = confidence_payload(
            0.91,
            target="misconception_answer_consistency",
            source="posthoc_logistic",
            calibration_id="numeric-real-v1",
        )

        self.assertTrue(payload["calibrated"])
        self.assertEqual(payload["level"], "high")
        self.assertEqual(payload["probability"], 0.91)

    def test_parser_keeps_only_valid_externally_calibrated_confidence(self):
        text = """{
          "distractors": [
            {
              "misconception": "Reverses division",
              "computation": "4 / 2 = 2",
              "answer": "2",
              "confidence": {
                "target": "misconception_answer_consistency",
                "probability": 0.82,
                "level": "medium",
                "calibrated": true,
                "source": "posthoc_isotonic",
                "calibration_id": "real-oof-v1"
              }
            },
            {
              "misconception": "Multiplies instead",
              "computation": "0.2 * 0.4 = 0.08",
              "answer": "0.08",
              "confidence": 0.99
            }
          ]
        }"""

        parsed = parse_distractors(text)

        self.assertEqual(parsed[0]["confidence"]["probability"], 0.82)
        self.assertNotIn("confidence", parsed[1])

    def test_brier_ece_and_selective_metrics_are_auditable(self):
        result = confidence_metrics(
            labels=[True, False],
            probabilities=[0.8, 0.2],
            bins=5,
            thresholds=(0.7, 0.9),
        )

        self.assertTrue(math.isclose(result["brier"], 0.04, abs_tol=1e-12))
        self.assertTrue(math.isclose(result["ece"], 0.2, abs_tol=1e-12))
        self.assertEqual(
            result["selective"],
            [
                {"threshold": 0.7, "coverage": 0.5, "accuracy": 1.0, "n": 1},
                {"threshold": 0.9, "coverage": 0.0, "accuracy": None, "n": 0},
            ],
        )

    def test_confidence_metrics_reject_invalid_inputs(self):
        with self.assertRaises(ValueError):
            confidence_metrics([True], [1.1])
        with self.assertRaises(ValueError):
            confidence_metrics([True], [])

    def test_binary_judge_calibration_is_smoothed_and_versioned(self):
        artifact = fit_binary_verdict_calibration(
            labels=[True, False, True, False],
            verdicts=[True, True, True, False],
            model="anthropic-primary/claude-opus-4-8",
            calibration_id="opus-binding-v1",
        )

        self.assertTrue(
            math.isclose(artifact["p_valid_given_yes"], 3 / 5)
        )
        self.assertTrue(
            math.isclose(artifact["p_valid_given_no"], 1 / 3)
        )
        self.assertEqual(artifact["calibration_id"], "opus-binding-v1")
        self.assertEqual(
            {"tp": artifact["tp"], "tn": artifact["tn"], "fp": artifact["fp"], "fn": artifact["fn"]},
            {"tp": 2, "tn": 1, "fp": 1, "fn": 0},
        )
        self.assertEqual(artifact["false_positive_rate"], 0.5)
        self.assertEqual(artifact["false_negative_rate"], 0.0)
        self.assertIn("brier", artifact)
        self.assertIn("ece", artifact)

    def test_calibration_attaches_numeric_binding_confidence_only(self):
        predictions = [
            {
                "id": "q1",
                "distractors": [
                    {"misconception": "A", "answer": "8"},
                    {"misconception": "B", "answer": "Neither"},
                ],
            }
        ]
        verdicts = [
            {
                "id": "q1",
                "distractor_index": 0,
                "answer_type": "numeric",
                "binding_valid": True,
            },
            {
                "id": "q1",
                "distractor_index": 1,
                "answer_type": "nonnumeric",
                "binding_valid": True,
            },
        ]
        artifact = {
            "calibration_id": "opus-binding-v1",
            "model": "anthropic-primary/claude-opus-4-8",
            "p_valid_given_yes": 0.9,
            "p_valid_given_no": 0.1,
        }

        enriched = apply_binding_calibration(
            predictions,
            verdicts,
            artifact,
        )

        self.assertEqual(
            enriched[0]["distractors"][0]["confidence"]["probability"],
            0.9,
        )
        self.assertIsNone(
            enriched[0]["distractors"][1]["confidence"]["probability"]
        )

        nonnumeric_artifact = {
            "calibration_id": "opus-nonnumeric-v1",
            "model": "anthropic-primary/claude-opus-4-8",
            "p_valid_given_yes": 0.8,
            "p_valid_given_no": 0.2,
        }
        fully_enriched = apply_binding_calibration(
            predictions,
            verdicts,
            artifact,
            nonnumeric_artifact=nonnumeric_artifact,
        )
        self.assertEqual(
            fully_enriched[0]["distractors"][1]["confidence"]["probability"],
            0.8,
        )


if __name__ == "__main__":
    unittest.main()
