import importlib
import importlib.util
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src import game_content
from tests.test_game_candidates import raw_candidate, trusted_question
from tests.test_game_review_export import approved_decision


def write_jsonl(path, records):
    Path(path).write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class GameContentCliTests(unittest.TestCase):
    def test_rejects_duplicate_json_keys_at_the_jsonl_boundary(self):
        self.assertIsNotNone(importlib.util.find_spec("src.game_content_cli"))
        game_content_cli = importlib.import_module("src.game_content_cli")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            questions = root / "questions.jsonl"
            output = root / "prepared.jsonl"
            valid = json.dumps(trusted_question(), ensure_ascii=False)
            duplicate = valid.replace(
                '"id": "GR-NUM-001"',
                '"id": "ATTACK", "id": "GR-NUM-001"',
                1,
            )
            questions.write_text(duplicate + "\n", encoding="utf-8")
            holdout = (
                Path(__file__).resolve().parents[1]
                / "data/processed/eval_heldout.jsonl"
            )

            with self.assertRaisesRegex(game_content.GameContentError, "duplicate JSON key"):
                game_content_cli.main(
                    [
                        "prepare-batch",
                        "--questions",
                        str(questions),
                        "--holdout",
                        str(holdout),
                        "--output",
                        str(output),
                    ]
                )

    def test_prepare_batch_validates_questions_and_refuses_silent_overwrite(self):
        self.assertIsNotNone(importlib.util.find_spec("src.game_content_cli"))
        game_content_cli = importlib.import_module("src.game_content_cli")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            questions = root / "questions.jsonl"
            holdout = (
                Path(__file__).resolve().parents[1]
                / "data/processed/eval_heldout.jsonl"
            )
            output = root / "prepared.jsonl"
            write_jsonl(questions, [trusted_question()])

            result = game_content_cli.main(
                [
                    "prepare-batch",
                    "--questions",
                    str(questions),
                    "--holdout",
                    str(holdout),
                    "--output",
                    str(output),
                ]
            )

            prepared = read_jsonl(output)
            self.assertEqual(result, 0)
            self.assertEqual(len(prepared), 1)
            self.assertEqual(prepared[0]["source"], "original-game-v1")
            self.assertEqual(
                prepared[0]["question_hash"],
                game_content.question_fingerprint(prepared[0]["question"]),
            )

            with self.assertRaisesRegex(game_content.GameContentError, "already exists"):
                game_content_cli.main(
                    [
                        "validate-questions",
                        "--questions",
                        str(questions),
                        "--holdout",
                        str(holdout),
                        "--output",
                        str(output),
                    ]
                )

    def test_candidate_review_and_export_commands_form_a_fail_closed_pipeline(self):
        self.assertIsNotNone(importlib.util.find_spec("src.game_content_cli"))
        game_content_cli = importlib.import_module("src.game_content_cli")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            questions_path = root / "questions.jsonl"
            holdout_path = (
                Path(__file__).resolve().parents[1]
                / "data/processed/eval_heldout.jsonl"
            )
            candidates_path = root / "candidates.jsonl"
            run_manifest_path = root / "run_manifest.json"
            validations_path = root / "validations.jsonl"
            queue_path = root / "review_queue.jsonl"
            decisions_path = root / "decisions.jsonl"
            reviewed_path = root / "reviewed.jsonl"
            pack_path = root / "pack.json"
            question = trusted_question()
            write_jsonl(questions_path, [question])
            batch_hash = game_content.stable_json_sha256([question])
            candidate = raw_candidate(source_batch_sha256=batch_hash)
            write_jsonl(candidates_path, [candidate])
            run_manifest = {
                "schema_version": "glitch-rally-generation-run-v1",
                "run_id": candidate["run_id"],
                "bundle_id": "bundle:v1:" + "5" * 64,
                "generator_source_sha256": candidate["generator_source_sha256"],
                "backend_source_sha256": candidate["backend_source_sha256"],
                "model_id": candidate["model_id"],
                "model_revision": candidate["model_revision"],
                "adapter_id": candidate["adapter_id"],
                "adapter_revision": candidate["adapter_revision"],
                "generation_parameters": candidate["generation_parameters"],
                "source_batch_sha256": candidate["source_batch_sha256"],
                "candidate_count": 1,
                "output_sha256": hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
                "runtime_versions": {
                    "unsloth": "2026.7.1",
                    "torch": "test",
                    "transformers": "test",
                    "peft": "test",
                    "huggingface_hub": "test",
                },
            }
            run_manifest_path.write_text(
                json.dumps(run_manifest, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            tampered_manifest = dict(run_manifest)
            tampered_manifest["output_sha256"] = "0" * 64
            run_manifest_path.write_text(
                json.dumps(tampered_manifest, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(game_content.GameContentError, "output_sha256"):
                game_content_cli.main(
                    [
                        "validate-candidates",
                        "--questions",
                        str(questions_path),
                        "--holdout",
                        str(holdout_path),
                        "--candidates",
                        str(candidates_path),
                        "--run-manifest",
                        str(run_manifest_path),
                        "--output",
                        str(validations_path),
                    ]
                )
            run_manifest_path.write_text(
                json.dumps(run_manifest, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                game_content_cli.main(
                    [
                        "validate-candidates",
                        "--questions",
                        str(questions_path),
                        "--holdout",
                        str(holdout_path),
                        "--candidates",
                        str(candidates_path),
                        "--run-manifest",
                        str(run_manifest_path),
                        "--output",
                        str(validations_path),
                    ]
                ),
                0,
            )
            validation = read_jsonl(validations_path)[0]
            self.assertEqual(validation["status"], "needs_review")

            self.assertEqual(
                game_content_cli.main(
                    [
                        "create-review-queue",
                        "--questions",
                        str(questions_path),
                        "--holdout",
                        str(holdout_path),
                        "--validations",
                        str(validations_path),
                        "--output",
                        str(queue_path),
                    ]
                ),
                0,
            )
            queue = read_jsonl(queue_path)
            self.assertEqual(queue[0]["decision"]["decision"], "pending")

            tampered_queue = queue
            tampered_queue[0]["review_payload"]["question"]["prompt"] = (
                "OWNER WAS SHOWN A DIFFERENT QUESTION"
            )
            tampered_queue[0]["decision"] = approved_decision(validation)
            write_jsonl(decisions_path, tampered_queue)
            with self.assertRaisesRegex(
                game_content.GameContentError,
                "review queue payload",
            ):
                game_content_cli.main(
                    [
                        "apply-reviews",
                        "--questions",
                        str(questions_path),
                        "--holdout",
                        str(holdout_path),
                        "--validations",
                        str(validations_path),
                        "--decisions",
                        str(decisions_path),
                        "--output",
                        str(reviewed_path),
                    ]
                )

            write_jsonl(decisions_path, [approved_decision(validation)])
            self.assertEqual(
                game_content_cli.main(
                    [
                        "apply-reviews",
                        "--questions",
                        str(questions_path),
                        "--holdout",
                        str(holdout_path),
                        "--validations",
                        str(validations_path),
                        "--decisions",
                        str(decisions_path),
                        "--output",
                        str(reviewed_path),
                    ]
                ),
                0,
            )

            self.assertEqual(
                game_content_cli.main(
                    [
                        "export-pack",
                        "--questions",
                        str(questions_path),
                        "--holdout",
                        str(holdout_path),
                        "--reviewed",
                        str(reviewed_path),
                        "--pack-id",
                        "glitch-rally-test-v1",
                        "--released-at-utc",
                        "2026-07-10T21:00:00Z",
                        "--output",
                        str(pack_path),
                    ]
                ),
                0,
            )
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            self.assertEqual(pack["encounterCount"], 1)
            self.assertEqual(
                pack["encounters"][0]["contentStatus"],
                "approved",
            )


if __name__ == "__main__":
    unittest.main()
