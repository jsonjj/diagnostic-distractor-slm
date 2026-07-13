import unittest

from src.v8_results import build_final_report, render_final_markdown


class V8ResultsTests(unittest.TestCase):
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
                "id": item["id"],
                "generator_model": "test-model",
                "distractors": [
                    {
                        "misconception": f"mistake {index}",
                        "computation": f"2 + {index} = {index + 2}",
                        "answer": str(index + 2),
                    }
                    for index in range(3)
                ],
            }
            for item in self.gold
        ]

    def test_builds_both_role_separated_comparisons_and_unavailable_verdict(self):
        report = build_final_report(
            self.gold,
            {
                "opus": self.predictions,
                "v8_model_only": self.predictions,
                "v8_best_of_n": self.predictions,
            },
            metadata={
                "evaluation_usage": {"completed_calls": 2},
                "verification": {
                    "full_python_tests": {"passed": 149},
                    "v8_python_tests": {"passed": 46},
                    "manifest_verified": True,
                },
            },
        )

        self.assertEqual(
            set(report["comparisons"]),
            {"v8_model_only_vs_opus", "v8_best_of_n_vs_opus"},
        )
        for system in report["systems"].values():
            self.assertEqual(
                system["good_distractor_rate"]["status"],
                "UNAVAILABLE",
            )
            self.assertEqual(
                system["numeric_binding_consistency"]["status"],
                "UNAVAILABLE",
            )
        for decision in report["decision_rules"].values():
            self.assertEqual(decision["overall"], "NOT DEMONSTRATED")
            self.assertIsNone(decision["meets_absolute_90pct_gdr"])
            self.assertEqual(
                set(decision["selected_primary_metrics"]),
                {
                    "good_distractor_rate",
                    "good_at_3",
                    "numeric_binding_consistency",
                    "diagnostic_quality_proxy",
                    "selective_gdr_at_80pct_coverage",
                },
            )
            self.assertTrue(
                all(
                    metric["status"] == "UNAVAILABLE"
                    and metric["meets_40pct_error_reduction"] is None
                    for metric in decision[
                        "selected_primary_metrics"
                    ].values()
                )
            )

        markdown = render_final_markdown(report)
        self.assertIn("Verifier-guided best-of-N is a system result", markdown)
        self.assertIn("every selected primary quality metric", markdown)
        self.assertIn("UNAVAILABLE", markdown)
        self.assertIn("v8_model_only_vs_opus", markdown)
        self.assertIn("v8_best_of_n_vs_opus", markdown)
        self.assertIn("149 full Python tests passed", markdown)
        self.assertIn("46 v8-specific tests passed", markdown)

    def test_rejects_incomplete_frontier_coverage(self):
        with self.assertRaisesRegex(ValueError, "opus coverage"):
            build_final_report(
                self.gold,
                {
                    "opus": self.predictions[:-1],
                    "v8_model_only": self.predictions,
                    "v8_best_of_n": self.predictions,
                },
            )


if __name__ == "__main__":
    unittest.main()
