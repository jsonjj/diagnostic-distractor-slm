from fractions import Fraction
import json
from pathlib import Path
import unittest

from src.game_content import (
    GameContentError,
    canonicalize_question,
    question_fingerprint,
    question_similarity,
    solve_question,
    validate_question_bank,
)


def sample_question(**overrides):
    item = {
        "id": "GR-NUM-001",
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


class QuestionIdentityTests(unittest.TestCase):
    def test_canonicalization_normalizes_latex_case_and_operator_variants(self):
        left = r"\( 0.2 \div 0.4 = \)"
        right = "0.2 ÷ 0.4="

        self.assertEqual(canonicalize_question(left), canonicalize_question(right))
        self.assertEqual(question_fingerprint(left), question_fingerprint(right))

    def test_similarity_flags_close_paraphrases_but_not_different_questions(self):
        original = "A tank holds 3/4 liter and gains 1/8 liter. How much is there?"
        close = "A tank holds 3/4 litre and gains 1/8 litre. How much is there now?"
        different = "A car travels 6.4 km and has 2.75 km remaining."

        self.assertGreater(question_similarity(original, close), 0.90)
        self.assertLess(question_similarity(original, different), 0.60)


class TrustedSolverTests(unittest.TestCase):
    def test_solves_supported_question_authority_kinds_exactly(self):
        cases = [
            ({"kind": "arithmetic", "expression": "5/8 + 1/4"}, Fraction(7, 8)),
            ({"kind": "round_decimal", "value": "47.386", "places": 2}, Fraction(4739, 100)),
            ({"kind": "gcd", "values": [36, 48]}, Fraction(12)),
            ({"kind": "lcm", "values": [8, 12]}, Fraction(24)),
            ({"kind": "decimal_to_fraction", "value": "0.375"}, Fraction(3, 8)),
        ]

        for solver, expected in cases:
            with self.subTest(solver=solver):
                self.assertEqual(solve_question(solver), expected)

    def test_rejects_coerced_or_malformed_solver_values(self):
        invalid = [
            {"kind": "gcd", "values": [3.9, 4]},
            {"kind": "gcd", "values": [True, 4]},
            {"kind": "round_decimal", "value": "47.386", "places": True},
            {"kind": "arithmetic", "expression": "2^100000"},
            {"kind": "arithmetic", "expression": "2 + 2 = 999"},
            {
                "kind": "arithmetic",
                "expression": '2 + 2 = __import__("os")',
            },
            {"kind": "round_decimal", "value": "9" * 100, "places": 2},
            {"kind": "decimal_to_fraction", "value": "0." + "1" * 100},
        ]

        for solver in invalid:
            with self.subTest(solver=solver):
                with self.assertRaises(GameContentError):
                    solve_question(solver)


class QuestionBankValidationTests(unittest.TestCase):
    def test_accepts_original_question_and_computes_its_hash(self):
        validated = validate_question_bank([sample_question()], holdout_questions=[])

        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["question_hash"], question_fingerprint(sample_question()["question"]))

    def test_rejects_answer_that_disagrees_with_trusted_solver(self):
        with self.assertRaisesRegex(GameContentError, "trusted solver produces 7/8, not 3/4"):
            validate_question_bank(
                [sample_question(correct="3/4")],
                holdout_questions=[],
            )

    def test_rejects_formatted_latex_or_non_string_correct_answers(self):
        invalid_answers = [
            "1,000",
            r"\frac{7}{8}",
            0.875,
            " 7/8 ",
            "+07/08",
            "000.875",
            ".875",
        ]

        for answer in invalid_answers:
            with self.subTest(answer=answer):
                with self.assertRaisesRegex(
                    GameContentError,
                    "correct answer must be a simple integer, decimal, or fraction",
                ):
                    validate_question_bank(
                        [
                            sample_question(
                                correct=answer,
                                solver={"kind": "arithmetic", "expression": "7/8"},
                            )
                        ],
                        holdout_questions=[],
                    )

    def test_rejects_duplicate_ids_and_unknown_model_topics(self):
        duplicate = sample_question(question="A different original question with 5/8 and 1/4.")
        unknown = sample_question(id="GR-NUM-002", topic="Generic Number Stuff")

        with self.assertRaisesRegex(
            GameContentError,
            "duplicate question ID.*topic is not in the trained Number taxonomy",
        ):
            validate_question_bank(
                [sample_question(), duplicate, unknown],
                holdout_questions=[],
            )

    def test_rejects_the_same_canonical_question_under_a_different_id(self):
        duplicate_text = sample_question(id="GR-NUM-002")

        with self.assertRaisesRegex(GameContentError, "duplicate canonical question"):
            validate_question_bank(
                [sample_question(), duplicate_text],
                holdout_questions=[],
            )

    def test_rejects_noncanonical_or_unsafe_authored_display_fields(self):
        invalid_items = [
            sample_question(id=" GR-NUM-001 "),
            sample_question(question="  What is 5/8 + 1/4?  "),
            sample_question(question="What is 5/8 + 1/4?\u202e"),
            sample_question(visual_tool=123),
            sample_question(visual_tool="../../script"),
            sample_question(trusted_steps=[{"not": "text"}, 42]),
            sample_question(trusted_steps=["x" * 241]),
        ]

        for item in invalid_items:
            with self.subTest(item=item):
                with self.assertRaises(GameContentError):
                    validate_question_bank([item], holdout_questions=[])

    def test_rejects_exact_and_near_frozen_holdout_matches(self):
        question = sample_question()
        exact_holdout = {"id": "heldout-exact", "question": question["question"]}
        near_holdout = {
            "id": "heldout-near",
            "question": question["question"].replace("does it have now", "is there now"),
        }

        with self.assertRaisesRegex(GameContentError, "frozen holdout.*heldout-exact"):
            validate_question_bank([question], holdout_questions=[exact_holdout])

        with self.assertRaisesRegex(GameContentError, "near-duplicate.*heldout-near"):
            validate_question_bank([question], holdout_questions=[near_holdout])

    def test_committed_v1_bank_has_sixty_valid_non_holdout_questions(self):
        root = Path(__file__).resolve().parents[1]
        questions = [
            json.loads(line)
            for line in (root / "data/game/questions_v1.jsonl").read_text().splitlines()
            if line.strip()
        ]
        holdout = [
            json.loads(line)
            for line in (root / "data/processed/eval_heldout.jsonl").read_text().splitlines()
            if line.strip()
        ]

        validated = validate_question_bank(questions, holdout_questions=holdout)

        self.assertEqual(len(validated), 60)
        self.assertEqual(len({item["question_hash"] for item in validated}), 60)
        self.assertTrue(all(item["source"] == "original-game-v1" for item in validated))


if __name__ == "__main__":
    unittest.main()
