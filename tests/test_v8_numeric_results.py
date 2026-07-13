import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from src.v8_numeric_results import (
    build_human_numeric_subset,
    build_numeric_report,
    build_scope_manifest,
    classify_numeric_item,
    numeric_primary_metrics,
    parse_numeric_display,
    render_numeric_markdown,
    write_numeric_artifacts,
)


class NumericEligibilityTests(unittest.TestCase):
    def test_supported_number_representations_parse_exactly(self):
        cases = {
            "42": (Fraction(42), "integer"),
            "-3": (Fraction(-3), "integer"),
            "0.25": (Fraction(1, 4), "decimal"),
            "6/14": (Fraction(3, 7), "fraction"),
            r"2 \frac{1}{3}": (Fraction(7, 3), "mixed_number"),
            "60%": (Fraction(60), "percentage"),
            r"4^{3}": (Fraction(64), "power"),
            r"-1.7\times10^{5}": (Fraction(-170_000), "standard_form"),
            r"\sqrt{81}": (Fraction(9), "root"),
            r"\sqrt[3]{27}": (Fraction(3), "root"),
            "0.666...": (Fraction(2, 3), "repeating_decimal"),
            "£28": (Fraction(28), "currency"),
            r"6 \mathrm{~mm}": (Fraction(6), "measurement"),
        }

        for text, (expected_value, expected_kind) in cases.items():
            with self.subTest(text=text):
                parsed = parse_numeric_display(text)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed.value, expected_value)
                self.assertEqual(parsed.kind, expected_kind)

    def test_nonnumeric_and_operation_choice_answers_are_rejected(self):
        rejected = (
            "OnlyTom",
            "Neitheriscorrect",
            "+100then-3",
            "2400+120+42",
            "53times53",
            "18000-2000",
            "3times(2+4)-5",
            "-4and6",
            "![a diagram]()",
            "CCXLIX",
            "1/61/6",
            r"10p^{3}q",
            r"\sqrt{2}",
        )

        for text in rejected:
            with self.subTest(text=text):
                self.assertIsNone(parse_numeric_display(text))

    def test_classifier_records_auditable_exclusion_reasons(self):
        cases = (
            ({"id": "numeric", "correct": "4^{3}"}, True, "numeric_power"),
            ({"id": "visual", "correct": "![diagram]()"}, False, "image_only_answer"),
            ({"id": "person", "correct": "OnlyKatie"}, False, "named_person_or_categorical_answer"),
            ({"id": "truth", "correct": "sometimestrue"}, False, "yes_no_or_truth_answer"),
            ({"id": "operation", "correct": "2400+120+42"}, False, "operation_choice_text"),
            ({"id": "pair", "correct": "-4and6"}, False, "compound_multi_value_answer"),
            ({"id": "roman", "correct": "CCXLIX"}, False, "roman_numeral_answer"),
            ({"id": "words", "correct": "Thirty-six"}, False, "verbal_or_concept_answer"),
            ({"id": "bad", "correct": "1/61/6"}, False, "unparseable_numeric_key"),
        )

        for item, included, reason in cases:
            with self.subTest(item=item["id"]):
                classification = classify_numeric_item(item)
                self.assertEqual(classification["included"], included)
                self.assertEqual(classification["reason"], reason)

    def test_manifest_is_reproducible_and_uses_only_gold_and_key_mapping(self):
        gold = [
            {"id": "q2", "correct": "OnlyTom", "question": "Who is correct?", "topic": "BIDMAS"},
            {"id": "q1", "correct": "3/4", "question": "Calculate.", "topic": "Fractions"},
            {"id": "q3", "correct": "![diagram]()", "question": "Choose a diagram.", "topic": "Fractions"},
        ]
        hidden_key = {
            "items": [
                {"review_item_id": "R02", "source_id": "q2"},
                {"review_item_id": "R01", "source_id": "q1"},
            ]
        }

        first = build_scope_manifest(gold, hidden_key)
        second = build_scope_manifest(list(reversed(gold)), hidden_key)

        self.assertEqual(first, second)
        self.assertEqual(first["frozen_scope"]["included"], 1)
        self.assertEqual(first["frozen_scope"]["excluded"], 2)
        self.assertEqual(first["frozen_scope"]["included_ids"], ["q1"])
        self.assertEqual(first["blind_review_scope"]["included_review_item_ids"], ["R01"])
        self.assertEqual(first["blind_review_scope"]["excluded_review_item_ids"], ["R02"])
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )


class NumericMetricTests(unittest.TestCase):
    def test_structural_metrics_use_exact_numeric_equivalence(self):
        gold = [
            {"id": "q1", "correct": "1/2", "question": "What is 1/2?", "topic": "Fractions"},
            {"id": "q2", "correct": "4", "question": "What is 2 + 2?", "topic": "Addition"},
        ]
        predictions = [
            {
                "id": "q1",
                "distractors": [
                    {"misconception": "a", "computation": "1 / 2 = 0.5", "answer": "0.5"},
                    {"misconception": "b", "computation": "1 + 0 = 1", "answer": "1"},
                    {"misconception": "c", "computation": "1 + 1 = 2", "answer": "2"},
                ],
            },
            {
                "id": "q2",
                "distractors": [
                    {"misconception": "a", "computation": "1 / 2 = 0.5", "answer": "1/2"},
                    {"misconception": "b", "computation": "2 / 4 = 0.5", "answer": "0.5"},
                    {"misconception": "c", "computation": "2 + 1 = 3", "answer": "3"},
                ],
            },
        ]

        metrics = numeric_primary_metrics(gold, predictions)

        self.assertEqual(
            (metrics["valid_exactly_3_json"]["numerator"], metrics["valid_exactly_3_json"]["denominator"]),
            (2, 2),
        )
        self.assertEqual(
            (metrics["none_equals_key"]["numerator"], metrics["none_equals_key"]["denominator"]),
            (1, 2),
        )
        self.assertEqual(
            (metrics["distinct_answers"]["numerator"], metrics["distinct_answers"]["denominator"]),
            (1, 2),
        )
        self.assertEqual(metrics["distinct_misconceptions"]["numerator"], 2)
        self.assertEqual(metrics["good_distractor_rate"]["status"], "UNAVAILABLE")
        self.assertEqual(metrics["numeric_binding_consistency"]["status"], "UNAVAILABLE")
        self.assertEqual(metrics["diagnostic_quality_proxy"]["status"], "UNAVAILABLE")
        self.assertEqual(metrics["confidence_ece"]["status"], "UNAVAILABLE")


class NumericHumanSubsetTests(unittest.TestCase):
    def test_human_subset_filters_on_gold_before_rescoring(self):
        gold = [
            {"id": "q1", "correct": "10", "question": "Calculate 5 + 5.", "topic": "Addition"},
            {"id": "q2", "correct": "OnlyTom", "question": "Who is correct?", "topic": "BIDMAS"},
            {"id": "q3", "correct": "50%", "question": "Write one half as a percentage.", "topic": "Percentages"},
        ]
        hidden_key = {
            "schema_version": "blinded-review-key-v1",
            "source_labels": ["v8_best_of_n", "opus"],
            "items": [
                {
                    "review_item_id": "R01",
                    "source_id": "q1",
                    "candidate_a_source": "opus",
                    "candidate_b_source": "v8_best_of_n",
                },
                {
                    "review_item_id": "R02",
                    "source_id": "q2",
                    "candidate_a_source": "v8_best_of_n",
                    "candidate_b_source": "opus",
                },
                {
                    "review_item_id": "R03",
                    "source_id": "q3",
                    "candidate_a_source": "v8_best_of_n",
                    "candidate_b_source": "opus",
                },
            ],
        }

        def system_ratings(value, issues=()):
            return {
                "diagnostic_usefulness": value,
                "student_plausibility": value,
                "teacher_actionability": value,
                "issues": list(issues),
            }

        blind_result = {
            "item_results": [
                {
                    "reviewer_code": "1",
                    "review_item_id": "R01",
                    "source_id": "q1",
                    "blind_preference": "B",
                    "systems": {
                        "v8_best_of_n": system_ratings(3, ("nonsense",)),
                        "opus": system_ratings(5),
                    },
                    "note": "",
                },
                {
                    "reviewer_code": "1",
                    "review_item_id": "R02",
                    "source_id": "q2",
                    "blind_preference": "B",
                    "systems": {
                        "v8_best_of_n": system_ratings(1, ("nonsense",)),
                        "opus": system_ratings(5),
                    },
                    "note": "Written answer mismatch.",
                },
                {
                    "reviewer_code": "1",
                    "review_item_id": "R03",
                    "source_id": "q3",
                    "blind_preference": "B",
                    "systems": {
                        "v8_best_of_n": system_ratings(4),
                        "opus": system_ratings(5),
                    },
                    "note": "",
                },
            ]
        }

        subset = build_human_numeric_subset(
            gold,
            blind_result,
            hidden_key,
            bootstrap_samples=200,
            seed=19,
        )

        self.assertEqual(subset["scope"]["included"], 2)
        self.assertEqual(subset["scope"]["excluded"], 1)
        self.assertEqual(subset["scope"]["included_review_item_ids"], ["R01", "R03"])
        self.assertEqual(subset["scope"]["excluded_review_item_ids"], ["R02"])
        self.assertEqual(subset["design"]["items"], 2)
        self.assertEqual(subset["systems"]["v8_best_of_n"]["overall_rating"]["mean"], 3.5)
        self.assertEqual(subset["systems"]["opus"]["overall_rating"]["mean"], 5.0)
        self.assertEqual(subset["systems"]["v8_best_of_n"]["preference"]["wins"], 1)
        self.assertEqual(subset["systems"]["opus"]["preference"]["wins"], 1)
        self.assertEqual(
            subset["response_consistency_audit"]["blind_preference_counts"],
            {"A": 0, "Tie": 0, "B": 2},
        )
        self.assertTrue(
            subset["response_consistency_audit"]["all_preferences_same_blind_label"]
        )
        self.assertEqual(len(subset["item_results"]), 2)


class NumericReportTests(unittest.TestCase):
    def test_report_and_markdown_keep_scope_and_unavailable_metrics_explicit(self):
        gold = [
            {"id": "q1", "correct": "10", "question": "Calculate 5 + 5.", "topic": "Addition"},
            {"id": "q2", "correct": "OnlyTom", "question": "Who is correct?", "topic": "BIDMAS"},
            {"id": "q3", "correct": "50%", "question": "Write one half as a percentage.", "topic": "Percentages"},
        ]

        def predictions(label):
            return [
                {
                    "id": item["id"],
                    "generator_model": label,
                    "distractors": [
                        {"misconception": "a", "computation": "5 + 4 = 9", "answer": "9"},
                        {"misconception": "b", "computation": "5 + 6 = 11", "answer": "11"},
                        {"misconception": "c", "computation": "5 + 7 = 12", "answer": "12"},
                    ],
                }
                for item in gold
            ]

        hidden_key = {
            "schema_version": "blinded-review-key-v1",
            "source_labels": ["v8_best_of_n", "opus"],
            "items": [
                {
                    "review_item_id": "R01",
                    "source_id": "q1",
                    "candidate_a_source": "opus",
                    "candidate_b_source": "v8_best_of_n",
                },
                {
                    "review_item_id": "R02",
                    "source_id": "q2",
                    "candidate_a_source": "v8_best_of_n",
                    "candidate_b_source": "opus",
                },
                {
                    "review_item_id": "R03",
                    "source_id": "q3",
                    "candidate_a_source": "v8_best_of_n",
                    "candidate_b_source": "opus",
                },
            ],
        }

        def ratings(value):
            return {
                "diagnostic_usefulness": value,
                "student_plausibility": value,
                "teacher_actionability": value,
                "issues": [],
            }

        blind_result = {
            "item_results": [
                {
                    "reviewer_code": "1",
                    "review_item_id": key_row["review_item_id"],
                    "source_id": key_row["source_id"],
                    "blind_preference": "B",
                    "systems": {
                        "v8_best_of_n": ratings(4),
                        "opus": ratings(5),
                    },
                    "note": "",
                }
                for key_row in hidden_key["items"]
            ]
        }
        systems = {
            "opus": predictions("opus"),
            "v8_model_only": predictions("model"),
            "v8_best_of_n": predictions("best"),
        }

        report, manifest = build_numeric_report(
            gold,
            systems,
            blind_result,
            hidden_key,
            benchmark_source={"protocol": {"frozen_items": 3}},
            bootstrap_samples=100,
            human_bootstrap_samples=100,
        )
        markdown = render_numeric_markdown(report, manifest)

        self.assertEqual(report["schema_version"], "v8-numeric-benchmark-v1")
        self.assertEqual(report["protocol"]["numeric_items"], 2)
        self.assertEqual(report["protocol"]["excluded_items"], 1)
        self.assertEqual(set(report["systems"]), {"opus", "v8_model_only", "v8_best_of_n"})
        self.assertIn("v8_model_only_vs_opus", report["comparisons"])
        self.assertIn("v8_best_of_n_vs_opus", report["comparisons"])
        self.assertEqual(report["human_review"]["scope"]["included"], 2)
        self.assertEqual(
            report["systems"]["opus"]["good_distractor_rate"]["status"],
            "UNAVAILABLE",
        )
        self.assertEqual(
            report["systems"]["opus"]["confidence_ece"]["direction"],
            "lower_is_better",
        )
        self.assertEqual(
            report["verdict"]["overall_numeric_scope"],
            "NO HOLISTIC WINNER DEMONSTRATED",
        )
        self.assertIn("Numeric-only intended-domain view", markdown)
        self.assertIn("not overall MCQ superiority", markdown)
        self.assertIn("No holistic numeric-scope winner", markdown)
        self.assertIn("q2", markdown)
        self.assertIn("R02", markdown)
        self.assertIn("UNAVAILABLE", markdown)

    def test_artifact_writer_is_deterministic_and_does_not_replace_source_evidence(self):
        gold = [
            {
                "id": "q1",
                "correct": "10",
                "question": "Calculate 5 + 5.",
                "topic": "Addition",
            }
        ]
        prediction = [
            {
                "id": "q1",
                "generator_model": "fixture",
                "distractors": [
                    {"misconception": "a", "computation": "5 + 4 = 9", "answer": "9"},
                    {"misconception": "b", "computation": "5 + 6 = 11", "answer": "11"},
                    {"misconception": "c", "computation": "5 + 7 = 12", "answer": "12"},
                ],
            }
        ]
        hidden_key = {
            "schema_version": "blinded-review-key-v1",
            "source_labels": ["v8_best_of_n", "opus"],
            "items": [
                {
                    "review_item_id": "R01",
                    "source_id": "q1",
                    "candidate_a_source": "opus",
                    "candidate_b_source": "v8_best_of_n",
                }
            ],
        }
        system_rating = {
            "diagnostic_usefulness": 5,
            "student_plausibility": 5,
            "teacher_actionability": 5,
            "issues": [],
        }
        blind_result = {
            "item_results": [
                {
                    "reviewer_code": "1",
                    "review_item_id": "R01",
                    "source_id": "q1",
                    "blind_preference": "B",
                    "systems": {
                        "v8_best_of_n": system_rating,
                        "opus": system_rating,
                    },
                    "note": "",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_path = root / "gold.jsonl"
            benchmark_path = root / "benchmark.json"
            blind_path = root / "blind.json"
            key_path = root / "key.json"
            prediction_paths = {
                name: root / f"{name}.jsonl"
                for name in ("opus", "v8_model_only", "v8_best_of_n")
            }
            gold_path.write_text(
                "".join(json.dumps(row) + "\n" for row in gold),
                encoding="utf-8",
            )
            for path in prediction_paths.values():
                path.write_text(
                    "".join(json.dumps(row) + "\n" for row in prediction),
                    encoding="utf-8",
                )
            benchmark_text = json.dumps({"protocol": {"frozen_items": 1}}) + "\n"
            benchmark_path.write_text(benchmark_text, encoding="utf-8")
            blind_path.write_text(json.dumps(blind_result), encoding="utf-8")
            key_path.write_text(json.dumps(hidden_key), encoding="utf-8")
            json_out = root / "numeric.json"
            manifest_out = root / "manifest.json"
            markdown_out = root / "numeric.md"

            write_numeric_artifacts(
                gold_path=gold_path,
                prediction_paths=prediction_paths,
                benchmark_path=benchmark_path,
                blind_result_path=blind_path,
                hidden_key_path=key_path,
                json_out=json_out,
                manifest_out=manifest_out,
                markdown_out=markdown_out,
                bootstrap_samples=50,
                human_bootstrap_samples=50,
            )
            first = (
                json_out.read_bytes(),
                manifest_out.read_bytes(),
                markdown_out.read_bytes(),
            )
            write_numeric_artifacts(
                gold_path=gold_path,
                prediction_paths=prediction_paths,
                benchmark_path=benchmark_path,
                blind_result_path=blind_path,
                hidden_key_path=key_path,
                json_out=json_out,
                manifest_out=manifest_out,
                markdown_out=markdown_out,
                bootstrap_samples=50,
                human_bootstrap_samples=50,
            )
            second = (
                json_out.read_bytes(),
                manifest_out.read_bytes(),
                markdown_out.read_bytes(),
            )

            self.assertEqual(first, second)
            self.assertEqual(
                json.loads(json_out.read_text(encoding="utf-8"))["schema_version"],
                "v8-numeric-benchmark-v1",
            )
            self.assertEqual(
                json.loads(manifest_out.read_text(encoding="utf-8"))["schema_version"],
                "v8-numeric-scope-manifest-v1",
            )
            self.assertEqual(benchmark_path.read_text(encoding="utf-8"), benchmark_text)


if __name__ == "__main__":
    unittest.main()
