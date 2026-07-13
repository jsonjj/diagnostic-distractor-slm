import math
import unittest

from src.benchmark_v8 import (
    compare_systems,
    paired_bootstrap_compare,
    paired_bootstrap_ratio_compare,
    paired_bootstrap_selective_compare,
    primary_metrics,
    relative_error_reduction,
    render_markdown_table,
)
from src.error_analysis_v8 import analyze_predictions


class V8BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.gold = [
            {
                "id": "q1",
                "question": "What is 6 / 2?",
                "correct": "3",
                "topic": "Division",
            },
            {
                "id": "q2",
                "question": "What is 2 + 3?",
                "correct": "5",
                "topic": "Addition",
            },
        ]
        self.predictions = [
            {
                "id": "q1",
                "distractors": [
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
                ],
            },
            {
                "id": "q2",
                "distractors": [
                    {
                        "misconception": "Subtracts instead",
                        "computation": "3 - 2 = 1",
                        "answer": "1",
                    },
                    {
                        "misconception": "Adds correctly",
                        "computation": "2 + 3 = 5",
                        "answer": "5",
                    },
                ],
            },
        ]

    def test_primary_table_contains_only_operational_quality_rates(self):
        metrics = primary_metrics(self.gold, self.predictions)

        self.assertIsNone(metrics["good_distractor_rate"]["score"])
        self.assertEqual(
            metrics["good_distractor_rate"]["status"], "NOT YET RUN"
        )
        self.assertIsNone(metrics["good_at_3"]["score"])
        self.assertEqual(metrics["valid_exactly_3_json"]["score"], 50.0)
        self.assertEqual(metrics["distinct_misconceptions"]["score"], 50.0)
        self.assertEqual(metrics["none_equals_key"]["score"], 50.0)
        self.assertEqual(metrics["distinct_answers"]["score"], 50.0)
        self.assertNotIn("Exact@3", metrics)
        self.assertNotIn("Partial@3", metrics)
        self.assertIsNone(metrics["numeric_binding_consistency"]["score"])
        self.assertEqual(
            metrics["numeric_binding_consistency"]["status"],
            "NOT YET RUN",
        )

    def test_confidence_error_metrics_come_from_independent_calibration_artifact(self):
        metrics = primary_metrics(
            self.gold,
            self.predictions,
            confidence_calibration={
                "accepted": True,
                "calibration_id": "opus-binding-v1",
                "n": 80,
                "ece": 0.08,
                "brier": 0.12,
            },
        )

        self.assertEqual(metrics["confidence_ece"]["score"], 8.0)
        self.assertEqual(metrics["confidence_brier"]["score"], 0.12)
        self.assertEqual(
            metrics["confidence_ece"]["method"],
            "out-of-fold binding calibration artifact",
        )

    def test_gdr_requires_every_local_binding_and_proxy_quality_gate(self):
        verdicts = []
        for item_id, count in (("q1", 3), ("q2", 2)):
            for index in range(count):
                verdicts.append(
                    {
                        "id": item_id,
                        "distractor_index": index,
                        "answer_type": "numeric",
                        "binding_valid": True,
                        "binding_method": "calibrated_opus_judge",
                        "binding_calibration_id": "opus-numeric-v1",
                        "binding_calibration_scope": "numeric",
                        "plausibility_pass": True,
                        "plausibility_method": "strict_opus_proxy",
                    }
                )

        metrics = primary_metrics(
            self.gold,
            self.predictions,
            verdicts=verdicts,
        )

        gdr = metrics["good_distractor_rate"]
        good_at_3 = metrics["good_at_3"]
        self.assertTrue(math.isclose(gdr["score"], 100 * 4 / 6))
        self.assertEqual((gdr["numerator"], gdr["denominator"]), (4, 6))
        self.assertEqual(good_at_3["score"], 50.0)
        self.assertEqual(
            (good_at_3["numerator"], good_at_3["denominator"]),
            (1, 2),
        )
        self.assertIn("ci95", gdr)
        self.assertIn("ci95", good_at_3)

        verdicts[0]["plausibility_pass"] = False
        failed_proxy = primary_metrics(
            self.gold,
            self.predictions,
            verdicts=verdicts,
        )
        self.assertEqual(
            failed_proxy["good_distractor_rate"]["numerator"], 3
        )
        self.assertEqual(failed_proxy["good_at_3"]["numerator"], 0)

    def test_verdict_sidecar_enables_numeric_binding_without_conflating_computation(self):
        verdicts = [
            {
                "id": "q1",
                "distractor_index": 0,
                "answer_type": "numeric",
                "valid": True,
                "binding_method": "calibrated_opus_judge",
                "binding_calibration_id": "opus-numeric-v1",
                "binding_calibration_scope": "numeric",
            },
            {
                "id": "q1",
                "distractor_index": 1,
                "answer_type": "numeric",
                "valid": False,
                "binding_method": "calibrated_opus_judge",
                "binding_calibration_id": "opus-numeric-v1",
                "binding_calibration_scope": "numeric",
            },
            {
                "id": "q1",
                "distractor_index": 2,
                "answer_type": "numeric",
                "valid": True,
                "binding_method": "calibrated_opus_judge",
                "binding_calibration_id": "opus-numeric-v1",
                "binding_calibration_scope": "numeric",
            },
        ]

        metrics = primary_metrics(
            self.gold,
            self.predictions,
            verdicts=verdicts,
        )

        self.assertTrue(
            math.isclose(
                metrics["numeric_binding_consistency"]["score"],
                100 * 2 / 3,
            )
        )
        self.assertEqual(
            metrics["numeric_binding_consistency"]["method"],
            "verdict_sidecar",
        )

    def test_error_analysis_quantifies_clusters_and_unseen_labels(self):
        report = analyze_predictions(
            self.gold,
            self.predictions,
            training_labels={"adds instead"},
        )

        self.assertEqual(report["totals"]["items"], 2)
        self.assertEqual(report["totals"]["wrong_count"], 1)
        self.assertEqual(report["totals"]["key_collision"], 1)
        self.assertEqual(report["totals"]["exact_unseen_labels"], 4)
        self.assertIn("Addition", report["by_topic"])
        self.assertEqual(report["by_topic"]["Addition"]["wrong_count"], 1)

    def test_quality_win_is_forty_percent_relative_error_reduction(self):
        self.assertTrue(
            math.isclose(relative_error_reduction(97.0, 95.0), 0.4)
        )
        self.assertTrue(
            math.isclose(relative_error_reduction(82.0, 70.0), 0.4)
        )
        self.assertIsNone(relative_error_reduction(100.0, 100.0))

    def test_paired_bootstrap_is_seeded_and_reports_absolute_delta(self):
        candidate = [1.0, 1.0, 1.0, 0.0]
        baseline = [1.0, 0.0, 0.0, 0.0]

        first = paired_bootstrap_compare(
            candidate,
            baseline,
            samples=500,
            seed=17,
        )
        second = paired_bootstrap_compare(
            candidate,
            baseline,
            samples=500,
            seed=17,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["candidate_score"], 75.0)
        self.assertEqual(first["baseline_score"], 25.0)
        self.assertEqual(first["absolute_delta"], 50.0)
        self.assertIn("absolute_delta_ci95", first)
        self.assertIn("error_reduction_ci95", first)

    def test_clustered_pair_comparison_preserves_question_pairing(self):
        result = paired_bootstrap_ratio_compare(
            [[True, True, True], [False, False, False]],
            [[True, False, False], [False, False, False]],
            samples=500,
            seed=23,
        )

        self.assertTrue(math.isclose(result["candidate_score"], 50.0))
        self.assertTrue(
            math.isclose(result["baseline_score"], 100 / 6)
        )
        self.assertTrue(
            math.isclose(result["error_reduction"], 0.4)
        )
        self.assertIn("absolute_delta_ci95", result)

    def test_system_comparison_reports_gdr_delta_and_targets(self):
        candidate_verdicts = []
        baseline_verdicts = []
        for item_id, count in (("q1", 3), ("q2", 2)):
            for index in range(count):
                common = {
                    "id": item_id,
                    "distractor_index": index,
                    "answer_type": "numeric",
                    "binding_valid": True,
                    "binding_method": "calibrated_opus_judge",
                    "binding_calibration_id": "opus-numeric-v1",
                    "binding_calibration_scope": "numeric",
                }
                candidate_verdicts.append(
                    {**common, "plausibility_pass": True}
                )
                baseline_verdicts.append(
                    {**common, "plausibility_pass": False}
                )

        result = compare_systems(
            self.gold,
            self.predictions,
            self.predictions,
            candidate_verdicts=candidate_verdicts,
            baseline_verdicts=baseline_verdicts,
            samples=200,
            seed=31,
        )

        gdr = result["good_distractor_rate"]
        self.assertEqual(gdr["candidate_score"], 100 * 4 / 6)
        self.assertEqual(gdr["baseline_score"], 0.0)
        self.assertIn("absolute_delta_ci95", gdr)
        self.assertTrue(gdr["meets_40pct_error_reduction_point"])

    def test_markdown_table_keeps_only_preregistered_headlines(self):
        report = {
            "systems": {
                "v8": primary_metrics(self.gold, self.predictions),
                "opus": primary_metrics(self.gold, self.predictions),
            }
        }

        text = render_markdown_table(report)

        self.assertIn("Good Distractor Rate", text)
        self.assertIn("Good@3", text)
        self.assertIn("NOT YET RUN", text)
        self.assertNotIn("Exact@3", text)
        self.assertNotIn("Partial@3", text)

    def test_selective_comparison_bootstraps_at_eighty_percent_coverage(self):
        result = paired_bootstrap_selective_compare(
            [
                [(True, 0.9), (True, 0.8), (False, 0.1)],
                [(True, 0.7), (False, 0.2)],
            ],
            [
                [(True, 0.9), (False, 0.8), (False, 0.1)],
                [(False, 0.7), (False, 0.2)],
            ],
            samples=200,
            seed=37,
        )

        self.assertEqual(result["candidate_score"], 75.0)
        self.assertEqual(result["baseline_score"], 25.0)
        self.assertEqual(result["coverage"], 80.0)
        self.assertIn("absolute_delta_ci95", result)


if __name__ == "__main__":
    unittest.main()
