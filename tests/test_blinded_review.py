import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.blinded_review import (
    ORDER_SEED,
    SAMPLE_SEED,
    build_review_bundle,
    render_math_text,
    render_review_html,
    select_sample,
    validate_public_html,
    verify_review_package,
    write_review_package,
)
from src.score_blinded_review import (
    parse_ratings_file,
    score_reviews,
)


FAMILY_TOPICS = (
    "Place Value",
    "Adding and Subtracting Fractions",
    "Ordering Decimals",
    "Percentages of an Amount",
    "Adding and Subtracting Negative Numbers",
    "BIDMAS",
    "Factors and Highest Common Factor",
    "Estimation",
)


def _gold_fixture() -> list[dict]:
    rows = []
    for family_index, topic in enumerate(FAMILY_TOPICS):
        for item_index in range(6):
            row_id = f"{family_index}-{item_index}"
            question = f"What is {item_index + 2} + {family_index + 3}?"
            correct = str(item_index + family_index + 5)
            construct = "Carry out a direct calculation"
            if item_index >= 2:
                question = (
                    f"Sam compares \\( \\frac{{{item_index}}}{{7}} \\) with "
                    f"\\( {family_index + 1}.25 \\). Which statement is correct?"
                )
                correct = "Only Sam is correct"
                construct = "Compare representations in a multi-step problem"
            if item_index >= 4:
                question = (
                    "![A number line split into equal parts]() "
                    f"Estimate \\( -{item_index}.5 + \\frac{{3}}{{4}} \\)."
                )
                correct = f"-{item_index - 1}.75"
                construct = "Interpret a visual scale and calculate"
            rows.append(
                {
                    "id": row_id,
                    "question": question,
                    "correct": correct,
                    "topic": topic,
                    "construct": construct,
                }
            )
    return rows


def _predictions(gold: list[dict], marker: str) -> list[dict]:
    return [
        {
            "id": row["id"],
            "generator_model": f"secret-{marker}",
            "inference_track": f"secret-track-{marker}",
            "distractors": [
                {
                    "misconception": f"{marker} misconception {index}",
                    "computation": f"{index} + 1 = {index + 1}",
                    "answer": str(index + 1),
                    "confidence": {"probability": 0.99},
                }
                for index in range(3)
            ],
        }
        for row in gold
    ]


class BlindedReviewBuildTests(unittest.TestCase):
    def test_sample_is_deterministic_balanced_and_metadata_only(self):
        gold = _gold_fixture()

        first = select_sample(gold, sample_size=24, seed=SAMPLE_SEED)
        second = select_sample(
            list(reversed(gold)),
            sample_size=24,
            seed=SAMPLE_SEED,
        )

        self.assertEqual(
            [row["id"] for row in first],
            [row["id"] for row in second],
        )
        self.assertEqual(len(first), 24)
        self.assertEqual(
            Counter(row["review_family"] for row in first),
            Counter(
                {
                    "whole-number operations & place value": 3,
                    "fractions": 3,
                    "decimals": 3,
                    "percentages & proportional conversion": 3,
                    "negative numbers": 3,
                    "order, powers & roots": 3,
                    "factors & multiples": 3,
                    "rounding, estimation & standard form": 3,
                }
            ),
        )
        self.assertEqual(
            Counter(row["challenge_band"] for row in first),
            Counter({"lower": 8, "middle": 8, "upper": 8}),
        )

    def test_bundle_maps_each_pair_and_keeps_sources_out_of_public_data(self):
        gold = _gold_fixture()
        first_predictions = _predictions(gold, "one")
        second_predictions = _predictions(gold, "two")

        public_items, hidden_key = build_review_bundle(
            gold,
            {
                "private_system_one": first_predictions,
                "private_system_two": second_predictions,
            },
            sample_size=24,
            sample_seed=SAMPLE_SEED,
            order_seed=ORDER_SEED,
        )

        self.assertEqual(len(public_items), 24)
        self.assertEqual(len(hidden_key["items"]), 24)
        key_by_review_id = {
            row["review_item_id"]: row for row in hidden_key["items"]
        }
        prediction_by_source = {
            "private_system_one": {
                row["id"]: row for row in first_predictions
            },
            "private_system_two": {
                row["id"]: row for row in second_predictions
            },
        }
        for public_item in public_items:
            key_row = key_by_review_id[public_item["review_item_id"]]
            for label in ("A", "B"):
                source = key_row[f"candidate_{label.lower()}_source"]
                expected = prediction_by_source[source][key_row["source_id"]]
                self.assertEqual(
                    public_item[f"candidate_{label.lower()}"],
                    [
                        {
                            "misconception": item["misconception"],
                            "computation": item["computation"],
                            "answer": item["answer"],
                        }
                        for item in expected["distractors"]
                    ],
                )
            public_blob = json.dumps(public_item)
            self.assertNotIn("private_system", public_blob)
            self.assertNotIn("generator_model", public_blob)
            self.assertNotIn("inference_track", public_blob)
            self.assertNotIn("confidence", public_blob)

        a_sources = {
            row["candidate_a_source"] for row in hidden_key["items"]
        }
        b_sources = {
            row["candidate_b_source"] for row in hidden_key["items"]
        }
        self.assertEqual(
            a_sources,
            {"private_system_one", "private_system_two"},
        )
        self.assertEqual(
            b_sources,
            {"private_system_one", "private_system_two"},
        )

    def test_review_html_is_offline_complete_and_source_blind(self):
        gold = _gold_fixture()
        public_items, _hidden_key = build_review_bundle(
            gold,
            {
                "private_system_one": _predictions(gold, "one"),
                "private_system_two": _predictions(gold, "two"),
            },
            sample_size=24,
        )

        html = render_review_html(public_items)
        validate_public_html(
            html,
            forbidden_labels=(
                "private_system_one",
                "private_system_two",
                "generator_model",
                "inference_track",
            ),
        )

        self.assertIn("Candidate A", html)
        self.assertIn("Candidate B", html)
        self.assertIn("Diagnostic usefulness", html)
        self.assertIn("Realistic student plausibility", html)
        self.assertIn("Clarity / teacher actionability", html)
        self.assertIn("Mathematically inconsistent", html)
        self.assertIn("Correct-answer collision", html)
        self.assertIn("Duplicate", html)
        self.assertIn("Nonsense", html)
        self.assertIn("Download JSON", html)
        self.assertIn("Download CSV", html)
        self.assertIn("localStorage", html)
        self.assertIn("current_index", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link ", html)

    def test_math_renderer_uses_mathml_and_preserves_image_alt_text(self):
        rendered = render_math_text(
            "![A bar split into 5 parts]() Calculate "
            "\\( \\frac{2}{5} \\times 10 = ? \\)"
        )

        self.assertIn("<math", rendered)
        self.assertIn("<mfrac>", rendered)
        self.assertIn("Visual prompt:", rendered)
        self.assertIn("A bar split into 5 parts", rendered)
        self.assertNotIn("\\frac", rendered)

    def test_generated_package_verifies_against_both_sources(self):
        gold = _gold_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold_path = root / "gold.jsonl"
            first_path = root / "first.jsonl"
            second_path = root / "second.jsonl"
            html_path = root / "review.html"
            key_path = root / "OWNER_ONLY.json"
            for path, rows in (
                (gold_path, gold),
                (first_path, _predictions(gold, "one")),
                (second_path, _predictions(gold, "two")),
            ):
                path.write_text(
                    "".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8",
                )
            write_review_package(
                gold_path=gold_path,
                prediction_paths={
                    "private_system_one": first_path,
                    "private_system_two": second_path,
                },
                html_path=html_path,
                key_path=key_path,
            )

            result = verify_review_package(
                gold_path=gold_path,
                prediction_paths={
                    "private_system_one": first_path,
                    "private_system_two": second_path,
                },
                html_path=html_path,
                key_path=key_path,
            )

        self.assertEqual(result["sample_size"], 24)
        self.assertTrue(result["all_pairs_match"])
        self.assertTrue(result["public_artifact_is_blind"])


class BlindedReviewScoringTests(unittest.TestCase):
    def setUp(self):
        self.key = {
            "schema_version": "blinded-review-key-v1",
            "items": [
                {
                    "review_item_id": "R01",
                    "source_id": "q1",
                    "candidate_a_source": "system_small",
                    "candidate_b_source": "system_large",
                },
                {
                    "review_item_id": "R02",
                    "source_id": "q2",
                    "candidate_a_source": "system_large",
                    "candidate_b_source": "system_small",
                },
            ],
        }
        self.ratings = {
            "schema_version": "blinded-ratings-v1",
            "reviewer_code": "reviewer-1",
            "ratings": [
                {
                    "review_item_id": "R01",
                    "preference": "A",
                    "candidate_a": {
                        "diagnostic_usefulness": 5,
                        "student_plausibility": 4,
                        "teacher_actionability": 5,
                        "issues": [],
                    },
                    "candidate_b": {
                        "diagnostic_usefulness": 2,
                        "student_plausibility": 3,
                        "teacher_actionability": 2,
                        "issues": ["nonsense"],
                    },
                    "note": "",
                },
                {
                    "review_item_id": "R02",
                    "preference": "B",
                    "candidate_a": {
                        "diagnostic_usefulness": 3,
                        "student_plausibility": 3,
                        "teacher_actionability": 3,
                        "issues": ["duplicate"],
                    },
                    "candidate_b": {
                        "diagnostic_usefulness": 4,
                        "student_plausibility": 5,
                        "teacher_actionability": 4,
                        "issues": [],
                    },
                    "note": "Clear difference.",
                },
            ],
        }

    def test_scoring_unblinds_preferences_ratings_and_flags(self):
        result = score_reviews(
            [self.ratings],
            self.key,
            bootstrap_samples=200,
            seed=19,
        )

        small = result["systems"]["system_small"]
        large = result["systems"]["system_large"]
        self.assertEqual(small["preference"]["wins"], 2)
        self.assertEqual(large["preference"]["wins"], 0)
        self.assertEqual(small["preference"]["tie_rate_pct"], 0.0)
        self.assertIn(
            "tie_rate_wilson_ci95",
            small["preference"],
        )
        self.assertEqual(
            small["ratings"]["diagnostic_usefulness"]["mean"],
            4.5,
        )
        self.assertEqual(
            large["ratings"]["diagnostic_usefulness"]["mean"],
            2.5,
        )
        self.assertEqual(small["issues"]["any"]["count"], 0)
        self.assertEqual(large["issues"]["any"]["count"], 2)
        self.assertIn(
            "bootstrap_ci95",
            result["paired_differences"]["diagnostic_usefulness"],
        )
        self.assertEqual(
            result["human_gdr_proxy"]["status"],
            "UNAVAILABLE",
        )
        self.assertEqual(
            result["good_at_3_proxy"]["status"],
            "UNAVAILABLE",
        )

    def test_multiple_reviewers_report_inter_rater_agreement(self):
        second = json.loads(json.dumps(self.ratings))
        second["reviewer_code"] = "reviewer-2"

        result = score_reviews(
            [self.ratings, second],
            self.key,
            bootstrap_samples=100,
            seed=23,
        )

        agreement = result["inter_rater_agreement"]
        self.assertEqual(agreement["status"], "MEASURED")
        self.assertEqual(agreement["reviewers"], 2)
        self.assertIn("preference_fleiss_kappa", agreement)
        self.assertIn("ordinal_weighted_kappa", agreement)

    def test_json_and_csv_exports_parse_to_the_same_rating_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path = root / "ratings.json"
            csv_path = root / "ratings.csv"
            json_path.write_text(json.dumps(self.ratings), encoding="utf-8")
            csv_path.write_text(
                "schema_version,reviewer_code,review_item_id,preference,"
                "a_diagnostic_usefulness,a_student_plausibility,"
                "a_teacher_actionability,a_issues,"
                "b_diagnostic_usefulness,b_student_plausibility,"
                "b_teacher_actionability,b_issues,note\n"
                "blinded-ratings-v1,reviewer-1,R01,A,5,4,5,,2,3,2,"
                "nonsense,\n"
                "blinded-ratings-v1,reviewer-1,R02,B,3,3,3,duplicate,"
                "4,5,4,,Clear difference.\n",
                encoding="utf-8",
            )

            parsed_json = parse_ratings_file(json_path)
            parsed_csv = parse_ratings_file(csv_path)

        self.assertEqual(parsed_json, parsed_csv)


if __name__ == "__main__":
    unittest.main()
