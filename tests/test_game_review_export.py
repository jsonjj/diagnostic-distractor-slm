from copy import deepcopy
import inspect
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from src import game_content
from tests.test_game_candidates import raw_candidate, trusted_question


def nested_keys(value):
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys.update(nested_keys(nested))
        return keys
    if isinstance(value, list):
        keys = set()
        for nested in value:
            keys.update(nested_keys(nested))
        return keys
    return set()


def validation_for_review():
    question = trusted_question()
    batch_hash = game_content.stable_json_sha256([question])
    candidate = raw_candidate(source_batch_sha256=batch_hash)
    return game_content.validate_generation_candidate(
        candidate,
        question,
        expected_source_batch_sha256=batch_hash,
    )


def frozen_holdout():
    path = Path(__file__).resolve().parents[1] / "data/processed/eval_heldout.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def second_question():
    item = {
        "id": "GR-NUM-002",
        "question": "A test car logs 1.2 kilometers, then 0.35 kilometer more. How far does it travel?",
        "correct": "1.55",
        "topic": "Adding and Subtracting with Decimals",
        "difficulty": "easy",
        "visual_tool": "place_value_grid",
        "trusted_steps": ["Write 1.2 as 1.20.", "Add 1.20 + 0.35 = 1.55."],
        "solver": {"kind": "arithmetic", "expression": "1.2 + 0.35"},
    }
    return game_content.validate_question_bank([item], holdout_questions=[])[0]


def two_reviewed_candidates():
    first = trusted_question()
    second = second_question()
    questions = [first, second]
    batch_hash = game_content.stable_json_sha256(questions)
    decimal_response = json.dumps(
        {
            "distractors": [
                {
                    "misconception": "Drops both decimal points",
                    "computation": "12 + 35 = 47",
                    "answer": "47",
                },
                {
                    "misconception": "Subtracts instead of adding",
                    "computation": "1.2 - 0.35 = 0.85",
                    "answer": "0.85",
                },
                {
                    "misconception": "Moves the second decimal one place",
                    "computation": "1.2 + 3.5 = 4.7",
                    "answer": "4.7",
                },
            ]
        }
    )
    first_validation = game_content.validate_generation_candidate(
        raw_candidate(question_record=first, source_batch_sha256=batch_hash),
        first,
        expected_source_batch_sha256=batch_hash,
    )
    second_validation = game_content.validate_generation_candidate(
        raw_candidate(
            question_record=second,
            raw_response=decimal_response,
            source_batch_sha256=batch_hash,
        ),
        second,
        expected_source_batch_sha256=batch_hash,
    )
    first_review = game_content.apply_review_decision(
        first_validation,
        approved_decision(first_validation, trusted=first),
        trusted_question=first,
    )
    second_review = game_content.apply_review_decision(
        second_validation,
        approved_decision(second_validation, trusted=second),
        trusted_question=second,
    )
    return questions, first_review, second_review


def approved_decision(validation=None, trusted=None, **overrides):
    validation = validation or validation_for_review()
    trusted = trusted or trusted_question()
    decision = {
        "schema_version": "glitch-rally-review-decision-v1",
        "candidate_id": validation["candidate_id"],
        "candidate_hash": validation["candidate_hash"],
        "validation_hash": validation["validation_hash"],
        "review_payload_hash": game_content.review_payload_fingerprint(
            validation,
            trusted,
        ),
        "decision": "approved",
        "reviewer": "owner",
        "reviewed_at_utc": "2026-07-10T20:00:00Z",
        "notes": "Each label accurately describes the shown arithmetic.",
        "trusted_question_verified": True,
        "trusted_answer_verified": True,
        "trusted_steps_verified": True,
        "holdout_origin_verified": True,
        "distractor_reviews": [
            {
                "index": 0,
                "semantic_valid": True,
                "age_appropriate": True,
                "glitch_family_id": "fraction_forger",
                "repair_prompt": "Build equal-sized fraction pieces first.",
                "repair_explanation": "Rename 1/4 as 2/8, then add the numerators.",
            },
            {
                "index": 1,
                "semantic_valid": True,
                "age_appropriate": True,
                "glitch_family_id": "operation_swapper",
                "repair_prompt": "Use the operation named in the question.",
                "repair_explanation": "This question asks for addition, not multiplication.",
            },
            {
                "index": 2,
                "semantic_valid": True,
                "age_appropriate": True,
                "glitch_family_id": "operation_swapper",
                "repair_prompt": "Track whether the amount grows or shrinks.",
                "repair_explanation": "The battery gains charge, so add rather than subtract.",
            },
        ],
    }
    decision.update(overrides)
    return decision


class ReviewQueueTests(unittest.TestCase):
    def test_queue_contains_only_candidates_that_passed_automatic_checks(self):
        self.assertTrue(hasattr(game_content, "create_review_queue"))
        valid = validation_for_review()
        question = trusted_question()
        batch_hash = game_content.stable_json_sha256([question])
        rejected = game_content.validate_generation_candidate(
            raw_candidate(raw_response="{}", source_batch_sha256=batch_hash),
            question,
            expected_source_batch_sha256=batch_hash,
        )

        queue = game_content.create_review_queue(
            [valid, rejected],
            [trusted_question()],
        )

        self.assertEqual(len(queue), 1)
        self.assertEqual(
            set(queue[0]),
            {
                "schema_version",
                "review_status",
                "review_payload",
                "review_payload_hash",
                "decision",
            },
        )
        self.assertEqual(
            queue[0]["review_payload"]["candidate_id"],
            valid["candidate_id"],
        )
        self.assertEqual(queue[0]["review_status"], "pending")
        self.assertEqual(
            queue[0]["review_payload"]["question"]["trusted_steps"],
            trusted_question()["trusted_steps"],
        )
        self.assertEqual(len(queue[0]["decision"]["distractor_reviews"]), 3)
        self.assertIn(
            "fraction_forger",
            queue[0]["review_payload"]["allowed_glitch_families"],
        )
        self.assertIn("decision", queue[0])
        self.assertEqual(queue[0]["decision"]["decision"], "pending")
        self.assertEqual(
            queue[0]["decision"]["validation_hash"],
            valid["validation_hash"],
        )
        self.assertIn("review_payload", queue[0])
        self.assertEqual(
            queue[0]["review_payload_hash"],
            "review-payload:v1:"
            + game_content.stable_json_sha256(queue[0]["review_payload"]),
        )
        self.assertEqual(
            queue[0]["decision"]["review_payload_hash"],
            queue[0]["review_payload_hash"],
        )


class HashBoundReviewTests(unittest.TestCase):
    def test_review_timestamp_cannot_predate_generation(self):
        validation = validation_for_review()
        decision = approved_decision(
            validation,
            reviewed_at_utc="2026-07-10T17:00:00Z",
        )

        with self.assertRaisesRegex(game_content.GameContentError, "predate generation"):
            game_content.apply_review_decision(
                validation,
                decision,
                trusted_question=trusted_question(),
            )

    def test_review_decision_is_bound_to_the_exact_trusted_question_payload(self):
        parameters = inspect.signature(game_content.apply_review_decision).parameters

        self.assertIn("trusted_question", parameters)

    def test_rejects_a_validation_artifact_with_changed_review_text(self):
        self.assertTrue(hasattr(game_content, "apply_review_decision"))
        validation = validation_for_review()
        decision = approved_decision(validation)
        validation["distractors"][0]["misconception"] = "OWNER SAW DIFFERENT TEXT"

        with self.assertRaisesRegex(game_content.GameContentError, "validation artifact"):
            game_content.apply_review_decision(
                validation,
                decision,
                trusted_question=trusted_question(),
            )

    def test_rejects_review_bound_to_stale_candidate_or_validation_hash(self):
        self.assertTrue(hasattr(game_content, "apply_review_decision"))
        validation = validation_for_review()

        for field in ("candidate_id", "candidate_hash", "validation_hash"):
            with self.subTest(field=field):
                decision = approved_decision(validation)
                decision[field] = "stale"
                with self.assertRaisesRegex(game_content.GameContentError, "does not match"):
                    game_content.apply_review_decision(
                        validation,
                        decision,
                        trusted_question=trusted_question(),
                    )

    def test_approval_requires_explicit_semantic_age_and_repair_checks(self):
        self.assertTrue(hasattr(game_content, "apply_review_decision"))
        validation = validation_for_review()
        bad_decisions = []
        semantic = approved_decision(validation)
        semantic["distractor_reviews"][0]["semantic_valid"] = False
        bad_decisions.append(semantic)
        age = approved_decision(validation)
        age["distractor_reviews"][1]["age_appropriate"] = None
        bad_decisions.append(age)
        family = approved_decision(validation)
        family["distractor_reviews"][2]["glitch_family_id"] = "made_up_glitch"
        bad_decisions.append(family)
        repair = approved_decision(validation)
        repair["distractor_reviews"][0]["repair_prompt"] = ""
        bad_decisions.append(repair)
        bidi = approved_decision(validation)
        bidi["distractor_reviews"][0]["repair_prompt"] = "Safe\u202eexe"
        bad_decisions.append(bidi)
        unchecked = approved_decision(validation)
        unchecked["trusted_answer_verified"] = False
        bad_decisions.append(unchecked)

        for decision in bad_decisions:
            with self.subTest(decision=decision):
                with self.assertRaises(game_content.GameContentError):
                    game_content.apply_review_decision(
                        validation,
                        decision,
                        trusted_question=trusted_question(),
                    )

    def test_explicit_rejection_requires_a_reason_and_never_becomes_approved(self):
        self.assertTrue(hasattr(game_content, "apply_review_decision"))
        validation = validation_for_review()
        decision = approved_decision(
            validation,
            decision="rejected",
            notes="Misconception labels do not actually explain the arithmetic.",
            distractor_reviews=[],
        )

        reviewed = game_content.apply_review_decision(
            validation,
            decision,
            trusted_question=trusted_question(),
        )

        self.assertEqual(reviewed["review_status"], "rejected")
        self.assertRegex(reviewed["review_hash"], r"^review:v1:[0-9a-f]{64}$")


class SanitizedExportTests(unittest.TestCase):
    def test_python_export_runs_through_browser_loader_reducer_and_renderer(self):
        question = trusted_question()
        validation = validation_for_review()
        reviewed = game_content.apply_review_decision(
            validation,
            approved_decision(validation),
            trusted_question=question,
        )
        pack = game_content.export_approved_pack(
            [question],
            holdout_questions=frozen_holdout(),
            reviewed_records=[reviewed],
            pack_id="glitch-rally-test-v1",
            released_at_utc="2026-07-10T21:00:00Z",
        )
        root = Path(__file__).resolve().parents[1]
        script = r"""
import fs from "node:fs";
const { loadApprovedPack } = await import("./game/prototype/content.js");
const { createInitialEncounterState, reduceEncounter } = await import("./game/prototype/encounter.js");
const { createEncounterViewModel } = await import("./game/prototype/view-model.js");
const { renderEncounterMarkup } = await import("./game/prototype/render.js");
const pack = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const encounters = await loadApprovedPack(pack);
const encounter = encounters[0];
let state = createInitialEncounterState();
state = reduceEncounter(encounter, state, { type: "SELECT_ANSWER", answerId: encounter.correctAnswerId });
state = reduceEncounter(encounter, state, { type: "COMMIT_ANSWER" });
if (state.revealedCounterfeitId !== encounter.featuredCounterfeitId) throw new Error("featured counterfeit ignored");
const revealed = encounter.counterfeits.find((item) => item.id === state.revealedCounterfeitId);
state = reduceEncounter(encounter, state, { type: "SELECT_REPAIR", repairId: revealed.repairId });
state = reduceEncounter(encounter, state, { type: "COMMIT_REPAIR" });
if (state.phase !== "resolved") throw new Error("approved encounter did not resolve");
const markup = renderEncounterMarkup(encounter, createEncounterViewModel(encounter, state));
if (!markup.includes("v7.1") || !markup.includes("owner-reviewed")) {
  throw new Error("approved SLM origin is not visible in the UI");
}
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pack.json"
            path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script, str(path)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_pack_timestamp_cannot_predate_approval(self):
        validation = validation_for_review()
        reviewed = game_content.apply_review_decision(
            validation,
            approved_decision(validation),
            trusted_question=trusted_question(),
        )

        with self.assertRaisesRegex(game_content.GameContentError, "predate approval"):
            game_content.export_approved_pack(
                [trusted_question()],
                holdout_questions=frozen_holdout(),
                reviewed_records=[reviewed],
                pack_id="glitch-rally-test-v1",
                released_at_utc="2026-07-10T19:00:00Z",
            )

    def test_export_order_and_hash_do_not_depend_on_review_file_order(self):
        questions, first, second = two_reviewed_candidates()
        arguments = {
            "holdout_questions": frozen_holdout(),
            "pack_id": "glitch-rally-test-v1",
            "released_at_utc": "2026-07-10T21:00:00Z",
        }

        forward = game_content.export_approved_pack(
            questions,
            reviewed_records=[first, second],
            **arguments,
        )
        reverse = game_content.export_approved_pack(
            questions,
            reviewed_records=[second, first],
            **arguments,
        )

        self.assertEqual(forward, reverse)
        self.assertEqual(forward["encounterIds"], ["GR-NUM-001", "GR-NUM-002"])

    def test_rejects_a_review_wrapper_with_a_flipped_outer_status(self):
        validation = validation_for_review()
        rejected = game_content.apply_review_decision(
            validation,
            approved_decision(
                validation,
                decision="rejected",
                notes="Not suitable.",
                distractor_reviews=[],
            ),
            trusted_question=trusted_question(),
        )
        rejected["review_status"] = "approved"

        with self.assertRaisesRegex(game_content.GameContentError, "review_status"):
            game_content.export_approved_pack(
                [trusted_question()],
                holdout_questions=frozen_holdout(),
                reviewed_records=[rejected],
                pack_id="glitch-rally-test-v1",
                released_at_utc="2026-07-10T21:00:00Z",
            )

    def test_holdout_iterable_is_materialized_once_before_exclusion_checks(self):
        holdout = frozen_holdout()
        copied = {
            "id": "GR-NUM-999",
            "question": holdout[0]["question"],
            "correct": "0.5",
            "topic": "Multiplying and Dividing with Decimals",
            "difficulty": "easy",
            "visual_tool": "decimal_equal_groups",
            "trusted_steps": ["Scale 0.2 ÷ 0.4 to 2 ÷ 4.", "2 ÷ 4 = 0.5."],
            "solver": {"kind": "arithmetic", "expression": "0.2 / 0.4"},
        }

        with self.assertRaisesRegex(game_content.GameContentError, "frozen holdout exact"):
            game_content.export_approved_pack(
                [copied],
                holdout_questions=(record for record in holdout),
                reviewed_records=[],
                pack_id="glitch-rally-test-v1",
                released_at_utc="2026-07-10T21:00:00Z",
            )

    def test_exports_only_fresh_approved_content_without_raw_model_text(self):
        self.assertTrue(hasattr(game_content, "export_approved_pack"))
        question = trusted_question()
        validation = validation_for_review()
        reviewed = game_content.apply_review_decision(
            validation,
            approved_decision(validation),
            trusted_question=trusted_question(),
        )

        pack = game_content.export_approved_pack(
            [question],
            holdout_questions=frozen_holdout(),
            reviewed_records=[reviewed],
            pack_id="glitch-rally-test-v1",
            released_at_utc="2026-07-10T21:00:00Z",
        )

        encounter = pack["encounters"][0]
        self.assertIn("schemaVersion", pack)
        self.assertEqual(pack["schemaVersion"], "glitch-rally-pack-v1")
        self.assertEqual(pack["packVersion"], "glitch-rally-test-v1")
        self.assertEqual(pack["encounterIds"], [encounter["id"]])
        self.assertRegex(pack["contentHash"], r"^pack:v1:[0-9a-f]{64}$")
        self.assertEqual(
            pack["holdoutAssertion"],
            {
                "excluded": True,
                "recordCount": 140,
                "sha256": game_content.FROZEN_HOLDOUT_SHA256,
            },
        )
        self.assertEqual(encounter["contentStatus"], "approved")
        self.assertEqual(encounter["question"]["correctAnswer"], question["correct"])
        self.assertIn("correctAnswerId", encounter)
        self.assertIn("featuredCounterfeitId", encounter)
        self.assertEqual(len(encounter["counterfeits"]), 3)
        self.assertEqual(len(encounter["repairChoices"]), 3)
        self.assertEqual(
            encounter["provenance"]["adapterId"],
            "j2ampn/qwen3-4b-distractor-lora-v7",
        )
        self.assertEqual(
            encounter["provenance"]["generatorVersion"],
            "glitch-rally-generator-v1",
        )
        self.assertTrue(encounter["provenance"]["excludedFromEvaluationHoldout"])
        self.assertIn("generationRunId", encounter["provenance"])
        self.assertIn("promptSha256", encounter["provenance"])
        self.assertNotIn("reviewer", encounter["provenance"])
        self.assertIn("misconception", encounter["counterfeits"][0])
        self.assertIn("computation", encounter["counterfeits"][0])
        self.assertNotIn("raw_response", nested_keys(pack))
        self.assertNotIn("raw_candidate", nested_keys(pack))

    def test_export_revalidates_raw_generation_and_review_hashes(self):
        self.assertTrue(hasattr(game_content, "export_approved_pack"))
        validation = validation_for_review()
        reviewed = game_content.apply_review_decision(
            validation,
            approved_decision(validation),
            trusted_question=trusted_question(),
        )
        reviewed["validation"]["raw_candidate"]["raw_response"] += " "

        with self.assertRaises(game_content.GameContentError):
            game_content.export_approved_pack(
                [trusted_question()],
                holdout_questions=frozen_holdout(),
                reviewed_records=[reviewed],
                pack_id="glitch-rally-test-v1",
                released_at_utc="2026-07-10T21:00:00Z",
            )

    def test_export_rechecks_the_frozen_holdout_and_skips_rejections(self):
        self.assertTrue(hasattr(game_content, "export_approved_pack"))
        validation = validation_for_review()
        rejected = game_content.apply_review_decision(
            validation,
            approved_decision(
                validation,
                decision="rejected",
                notes="Not semantically valid.",
                distractor_reviews=[],
            ),
            trusted_question=trusted_question(),
        )

        with self.assertRaisesRegex(game_content.GameContentError, "no approved"):
            game_content.export_approved_pack(
                [trusted_question()],
                holdout_questions=frozen_holdout(),
                reviewed_records=[rejected],
                pack_id="glitch-rally-test-v1",
                released_at_utc="2026-07-10T21:00:00Z",
            )

        with self.assertRaisesRegex(game_content.GameContentError, "receipt mismatch"):
            game_content.export_approved_pack(
                [trusted_question()],
                holdout_questions=[],
                reviewed_records=[],
                pack_id="glitch-rally-test-v1",
                released_at_utc="2026-07-10T21:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
