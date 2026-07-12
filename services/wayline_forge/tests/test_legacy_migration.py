from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from services.wayline_forge.app.curriculum import CURRICULUM_V1_SHA256
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.procedure_registry import (
    PROCEDURE_REGISTRY_V1_SHA256,
    ProcedureRegistry,
)
from services.wayline_forge.app.question_kernel import CompileRequest, QuestionCompiler
from services.wayline_forge.app.reviewed_cache import ReviewedCache
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle
from services.wayline_forge.scripts.migrate_legacy_approved import (
    MigrationError,
    audit_legacy_approved,
    canonical_sha256,
    execute_legacy_migration,
)
from services.wayline_forge.scripts.legacy_review_audit import (
    LEGACY_ADAPTER,
    LEGACY_BASE_MODEL,
    LEGACY_GENERATION_PARAMETERS,
    LEGACY_GENERATOR_VERSION,
    canonical_json_sha256,
    canonicalize_legacy_question,
    legacy_question_fingerprint,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
WORK_ROOT = REPO_ROOT / "data/game/work"


class LegacyMigrationAuditTests(unittest.TestCase):
    def paths(self) -> dict[str, Path]:
        return {
            "decisions_path": WORK_ROOT / "review_decisions_owner_v1.jsonl",
            "approved_pack_path": WORK_ROOT / "reviewed_v1.jsonl",
            "questions_path": WORK_ROOT / "questions_prepared_v1.jsonl",
            "model_manifest_path": (
                REPO_ROOT
                / "services/wayline_forge/resources/model_manifest_v1.json"
            ),
            "bundle_index_path": (
                REPO_ROOT
                / "data/wayline/runtime/legacy_bundle_index_v1.json"
            ),
        }

    def test_real_owner_approvals_produce_deterministic_fail_closed_report(self):
        first = audit_legacy_approved(**self.paths())
        second = audit_legacy_approved(**self.paths())

        self.assertEqual(first, second)
        self.assertEqual(first["schemaVersion"], "wayline.legacy-migration-report.v1")
        self.assertEqual(first["mode"], "dry-run")
        self.assertEqual(first["reviewerAlias"], "owner-01")
        self.assertEqual(
            first["summary"],
            {"accepted": 0, "approvedLegacyRecords": 6, "rejected": 6},
        )
        self.assertEqual(
            [item["legacyQuestionId"] for item in first["items"]],
            [
                "GR-NUM-010",
                "GR-NUM-018",
                "GR-NUM-024",
                "GR-NUM-036",
                "GR-NUM-037",
                "GR-NUM-055",
            ],
        )
        self.assertEqual(
            first["runtimeReceipts"]["curriculumSha256"],
            CURRICULUM_V1_SHA256,
        )
        self.assertEqual(
            first["runtimeReceipts"]["registrySha256"],
            PROCEDURE_REGISTRY_V1_SHA256,
        )
        self.assertIsNone(first["runtimeReceipts"]["modelManifestSha256"])
        self.assertIn("model_manifest_missing", first["globalReasons"])
        self.assertIn("bundle_index_missing", first["globalReasons"])

        for item in first["items"]:
            self.assertEqual(item["status"], "rejected")
            self.assertTrue(item["checks"]["legacyApprovalVerified"])
            self.assertTrue(item["checks"]["trustedAnswerRecomputed"])
            self.assertFalse(item["checks"]["holdoutExcluded"])
            self.assertEqual(item["checks"]["currentTemplateMatches"], [])
            self.assertIn(
                "current_compiler_prompt_unrepresentable", item["reasons"]
            )
            self.assertIn("model_manifest_missing", item["reasons"])
            self.assertIn("wayline_bundle_mapping_missing", item["reasons"])

        unsigned = dict(first)
        report_receipt = unsigned.pop("reportSha256")
        self.assertEqual(report_receipt, canonical_sha256(unsigned))

    def test_dry_run_has_no_cache_path_and_cannot_mutate_a_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "reviewed.sqlite3"
            cache_path.write_bytes(b"owner sentinel")

            report = audit_legacy_approved(**self.paths())

            self.assertEqual(cache_path.read_bytes(), b"owner sentinel")
            self.assertNotIn("cachePath", json.dumps(report, sort_keys=True))
            self.assertEqual(
                report["cacheMutation"],
                {"performed": False, "requested": False},
            )

    def test_duplicate_json_keys_are_rejected_before_any_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            decisions = Path(temporary) / "decisions.jsonl"
            decisions.write_text(
                '{"decision":{},"decision":{}}\n', encoding="utf-8"
            )
            paths = self.paths()
            paths["decisions_path"] = decisions

            with self.assertRaises(MigrationError) as caught:
                audit_legacy_approved(**paths)

            self.assertEqual(caught.exception.code, "duplicate_json_key")

    def test_apply_requires_separate_approval_artifact_before_cache_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "reviewed.sqlite3"
            cache_path.write_bytes(b"owner sentinel")

            with self.assertRaises(MigrationError) as caught:
                execute_legacy_migration(
                    **self.paths(),
                    apply=True,
                    cache_path=cache_path,
                    approval_artifact_path=None,
                )

            self.assertEqual(caught.exception.code, "approval_artifact_required")
            self.assertEqual(cache_path.read_bytes(), b"owner sentinel")

    def test_injected_compiler_must_equal_the_complete_packaged_runtime(self):
        trusted = QuestionCompiler.for_tests()
        truncated_registry = ProcedureRegistry(
            trusted.registry.registry_id,
            trusted.registry.entries[:-1],
        )
        untrusted = QuestionCompiler(trusted.curriculum, truncated_registry)

        with self.assertRaises(MigrationError) as caught:
            audit_legacy_approved(**self.paths(), compiler=untrusted)

        self.assertEqual(caught.exception.code, "current_runtime_receipt_invalid")

    def test_import_does_not_load_legacy_buggy_procedures(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "import services.wayline_forge.scripts.migrate_legacy_approved; "
                    "raise SystemExit("
                    "'src.buggy_procedures' in sys.modules)"
                ),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class CompatibleLegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.verifier = DistractorVerifier.for_tests()
        self.paths, self.bundles = self._build_compatible_inputs()

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _write_jsonl(self, path: Path, values: list[dict[str, object]]) -> None:
        path.write_text(
            "".join(self._json(value) + "\n" for value in values),
            encoding="utf-8",
        )

    def _bundle(self, seed: int) -> tuple[VerifiedQuestionBundle, str]:
        request = CompileRequest(
            "decimara", "decimal_add_sub", "decimal_add", 2, seed
        )
        blueprint = self.verifier.compiler.compile(request)
        distractors = [
            {
                "answer": self.verifier.registry.evaluate(
                    procedure_id, blueprint
                ).display,
                "computation": self.verifier.registry.canonical_computation(
                    procedure_id, blueprint
                ),
                "misconception": self.verifier.registry.canonical_label(
                    procedure_id
                ),
            }
            for procedure_id in blueprint.allowed_procedure_ids[:3]
        ]
        raw_response = self._json({"distractors": distractors})
        generation = replace(
            self.verifier.fixture_generation(blueprint, "accepted.json"),
            text=raw_response,
            generated_at_utc="2026-07-11T18:00:00Z",
        )
        verified = self.verifier.verify_generation(blueprint, generation)
        self.assertTrue(verified.accepted, verified.code)
        assert verified.value is not None
        return (
            VerifiedQuestionBundle.from_verified(
                compiler=self.verifier.compiler,
                request=request,
                blueprint=blueprint,
                verified=verified.value,
                generation=generation,
                manifest=self.verifier.manifest,
            ),
            raw_response,
        )

    def _build_compatible_inputs(
        self,
    ) -> tuple[dict[str, Path], dict[str, VerifiedQuestionBundle]]:
        bundles: dict[str, VerifiedQuestionBundle] = {}
        raw_responses: dict[str, str] = {}
        untrusted_questions: list[dict[str, object]] = []
        for offset, seed in enumerate(range(8101, 8107), start=1):
            question_id = f"GR-NUM-9{offset:02d}"
            bundle, raw_response = self._bundle(seed)
            bundles[question_id] = bundle
            raw_responses[question_id] = raw_response
            operands = bundle.blueprint.operand_map
            untrusted_questions.append({
                "correct": bundle.blueprint.canonical_answer.display,
                "difficulty": "medium",
                "id": question_id,
                "question": bundle.blueprint.prompt,
                "solver": {
                    "expression": f"0.{operands['a']} + 0.0{operands['b']}",
                    "kind": "arithmetic",
                },
                "topic": bundle.blueprint.topic,
                "trusted_steps": list(bundle.blueprint.trusted_steps),
                "visual_tool": "decimal_grid",
            })

        questions: list[dict[str, object]] = []
        for raw_question in untrusted_questions:
            question = dict(raw_question)
            question["canonical_question"] = canonicalize_legacy_question(
                question["question"]
            )
            question["question_hash"] = legacy_question_fingerprint(
                question["question"]
            )
            question["source"] = "original-game-v1"
            questions.append(question)
        decisions: list[dict[str, object]] = []
        approved: list[dict[str, object]] = []
        generator_source_sha256 = hashlib.sha256(
            (REPO_ROOT / "src/game_candidate_generation.py").read_bytes()
        ).hexdigest()
        backend_source_sha256 = hashlib.sha256(
            (REPO_ROOT / "src/game_colab_backend.py").read_bytes()
        ).hexdigest()
        allowed_glitch_families = {
            family_id: {"name": family_id, "personality": family_id}
            for family_id in (
                "decimal_drifter",
                "factor_faker",
                "fraction_forger",
                "operation_swapper",
                "order_hacker",
                "place_value_phantom",
                "reciprocal_rogue",
                "rounding_rascal",
                "sign_flipper",
            )
        }
        for question in questions:
            question_id = question["id"]
            raw_response = raw_responses[question_id]
            candidate: dict[str, object] = {
                "adapter_id": LEGACY_ADAPTER,
                "adapter_revision": "d" * 40,
                "backend_source_sha256": backend_source_sha256,
                "candidate_id": "",
                "correct": question["correct"],
                "generated_at_utc": "2026-07-11T18:00:00Z",
                "generation_parameters": dict(LEGACY_GENERATION_PARAMETERS),
                "generator_source_sha256": generator_source_sha256,
                "generator_version": LEGACY_GENERATOR_VERSION,
                "model_id": LEGACY_BASE_MODEL,
                "model_revision": "c" * 40,
                "prompt_sha256": "1" * 64,
                "question": question["question"],
                "question_hash": question["question_hash"],
                "question_id": question_id,
                "question_record_sha256": canonical_json_sha256(question),
                "raw_response": raw_response,
                "raw_response_sha256": hashlib.sha256(
                    raw_response.encode("utf-8")
                ).hexdigest(),
                "run_id": "wayline-migration-test-v1",
                "schema_version": "glitch-rally-candidate-v1",
                "source_batch_sha256": "a" * 64,
                "system_prompt_sha256": "2" * 64,
                "topic": question["topic"],
                "user_prompt_sha256": "3" * 64,
            }
            candidate["candidate_id"] = "candidate:v1:" + canonical_json_sha256({
                key: value
                for key, value in candidate.items()
                if key not in {"candidate_id", "generated_at_utc"}
            })
            distractors = json.loads(raw_response)["distractors"]
            validation: dict[str, object] = {
                "candidate_hash": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "distractors": distractors,
                "issues": [],
                "question_hash": question["question_hash"],
                "question_id": question_id,
                "raw_candidate": candidate,
                "schema_version": "glitch-rally-validation-v1",
                "status": "needs_review",
                "validation_hash": "",
                "validator_version": "glitch-rally-validator-v1",
            }
            validation["validation_hash"] = (
                "validation:v1:"
                + canonical_json_sha256({
                    "candidate_hash": candidate["candidate_id"],
                    "distractors": distractors,
                    "issues": [],
                    "question_hash": question["question_hash"],
                    "status": "needs_review",
                    "validator_version": "glitch-rally-validator-v1",
                })
            )
            payload = {
                "allowed_glitch_families": allowed_glitch_families,
                "candidate_hash": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "distractors": distractors,
                "generation": {
                    key: candidate[key]
                    for key in (
                        "adapter_id",
                        "adapter_revision",
                        "backend_source_sha256",
                        "generator_source_sha256",
                        "generator_version",
                        "model_id",
                        "model_revision",
                        "prompt_sha256",
                    )
                },
                "question": {
                    "correct": question["correct"],
                    "id": question_id,
                    "prompt": question["question"],
                    "question_hash": question["question_hash"],
                    "topic": question["topic"],
                    "trusted_steps": question["trusted_steps"],
                },
                "validation_hash": validation["validation_hash"],
            }
            payload_hash = "review-payload:v1:" + canonical_json_sha256(payload)
            decision = {
                "candidate_hash": validation["candidate_hash"],
                "candidate_id": validation["candidate_id"],
                "decision": "approved",
                "distractor_reviews": [
                    {
                        "age_appropriate": True,
                        "glitch_family_id": "decimal_drifter",
                        "index": index,
                        "repair_explanation": (
                            f"Reviewed explanation for route {index + 1}."
                        ),
                        "repair_prompt": f"Reviewed route {index + 1}.",
                        "semantic_valid": True,
                    }
                    for index in range(3)
                ],
                "holdout_origin_verified": True,
                "notes": "Owner approved compiler-native migration fixture.",
                "review_payload_hash": payload_hash,
                "reviewed_at_utc": "2026-07-11T19:00:00Z",
                "reviewer": "owner-01",
                "schema_version": "glitch-rally-review-decision-v1",
                "trusted_answer_verified": True,
                "trusted_question_verified": True,
                "trusted_steps_verified": True,
                "validation_hash": validation["validation_hash"],
            }
            review_hash = "review:v1:" + canonical_json_sha256({
                "candidate_hash": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "decision": decision,
                "validation_hash": validation["validation_hash"],
            })
            reviewed = {
                "candidate_hash": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "decision": decision,
                "question_id": question_id,
                "review_hash": review_hash,
                "review_payload_hash": payload_hash,
                "review_status": "approved",
                "schema_version": "glitch-rally-reviewed-candidate-v1",
                "validation": validation,
                "validation_hash": validation["validation_hash"],
            }
            approved.append(reviewed)
            decisions.append({
                "decision": decision,
                "review_payload": payload,
                "review_payload_hash": payload_hash,
                "review_status": "pending",
                "schema_version": "glitch-rally-review-queue-v1",
            })

        decisions_path = self.root / "decisions.jsonl"
        approved_path = self.root / "approved.jsonl"
        questions_path = self.root / "questions.jsonl"
        self._write_jsonl(decisions_path, decisions)
        self._write_jsonl(approved_path, approved)
        self._write_jsonl(questions_path, questions)

        bundle_root = self.root / "bundles"
        bundle_root.mkdir()
        index_items = []
        for question_id, bundle in sorted(bundles.items()):
            bundle_file = bundle_root / f"{question_id}.json"
            raw = bundle.to_private_json().encode("utf-8")
            bundle_file.write_bytes(raw)
            index_items.append({
                "bundleFileSha256": hashlib.sha256(raw).hexdigest(),
                "bundlePath": f"bundles/{question_id}.json",
                "legacyQuestionId": question_id,
            })
        bundle_index = self.root / "bundle-index.json"
        bundle_index.write_text(
            self._json({
                "items": index_items,
                "schemaVersion": "wayline.legacy-bundle-index.v1",
            }),
            encoding="utf-8",
        )
        return ({
            "approved_pack_path": approved_path,
            "bundle_index_path": bundle_index,
            "decisions_path": decisions_path,
            "model_manifest_path": self.root / "unused-model-manifest.json",
            "questions_path": questions_path,
        }, bundles)

    def _audit(self) -> dict[str, object]:
        return audit_legacy_approved(
            **self.paths,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def _approval_artifact(self, report: dict[str, object]) -> Path:
        artifact: dict[str, object] = {
            "approvedItems": [
                {
                    "legacyQuestionId": item["legacyQuestionId"],
                    "legacyReviewHash": item["legacyReviewHash"],
                    "semanticContentSha256": item["checks"][
                        "currentBundleSemanticContentSha256"
                    ],
                }
                for item in report["items"]
            ],
            "auditSha256": report["auditSha256"],
            "reviewedAtUtc": "2026-07-11T20:00:00Z",
            "reviewerAlias": "owner-01",
            "schemaVersion": "wayline.legacy-migration-approval.v1",
        }
        artifact["approvalArtifactSha256"] = canonical_sha256(artifact)
        path = self.root / "approval.json"
        path.write_text(self._json(artifact), encoding="utf-8")
        return path

    def test_current_bundles_are_recompiled_and_registry_reverified(self):
        report = self._audit()

        self.assertEqual(
            report["summary"],
            {"accepted": 6, "approvedLegacyRecords": 6, "rejected": 0},
        )
        self.assertEqual(report["globalReasons"], [])
        self.assertRegex(
            report["runtimeReceipts"]["modelManifestSha256"],
            r"^[0-9a-f]{64}$",
        )
        for item in report["items"]:
            self.assertEqual(item["status"], "accepted")
            self.assertEqual(item["reasons"], [])
            self.assertEqual(len(item["checks"]["currentTemplateMatches"]), 1)
            self.assertEqual(
                item["checks"]["currentBundleSemanticContentSha256"],
                self.bundles[item["legacyQuestionId"]].semantic_content_sha256,
            )
            self.assertTrue(item["checks"]["currentBundleReverified"])

    def test_apply_requires_exact_semantic_approval_and_writes_valid_cache(self):
        audit = self._audit()
        approval = self._approval_artifact(audit)
        cache_path = self.root / "reviewed.sqlite3"

        report = execute_legacy_migration(
            **self.paths,
            apply=True,
            cache_path=cache_path,
            approval_artifact_path=approval,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

        self.assertEqual(report["mode"], "apply")
        self.assertEqual(
            report["cacheMutation"], {"performed": True, "requested": True}
        )
        with ReviewedCache.open_learner(
            cache_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        ):
            connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
            rows = connection.execute(
                "SELECT semantic_content_sha256, row_json FROM reviewed_questions"
            ).fetchall()
            connection.close()
        self.assertEqual(len(rows), 6)
        self.assertEqual(
            {semantic for semantic, _row in rows},
            {bundle.semantic_content_sha256 for bundle in self.bundles.values()},
        )
        artifact_hash = json.loads(approval.read_text(encoding="utf-8"))[
            "approvalArtifactSha256"
        ]
        for _semantic, row_json in rows:
            review = json.loads(row_json)["review"]
            self.assertEqual(review["ownerAlias"], "owner-01")
            self.assertEqual(review["approvalRecordSha256"], artifact_hash)

    def test_semantic_approval_mismatch_cannot_create_cache(self):
        audit = self._audit()
        approval_path = self._approval_artifact(audit)
        artifact = json.loads(approval_path.read_text(encoding="utf-8"))
        artifact["approvedItems"][0]["semanticContentSha256"] = "f" * 64
        artifact.pop("approvalArtifactSha256")
        artifact["approvalArtifactSha256"] = canonical_sha256(artifact)
        approval_path.write_text(self._json(artifact), encoding="utf-8")
        cache_path = self.root / "must-not-exist.sqlite3"

        with self.assertRaises(MigrationError) as caught:
            execute_legacy_migration(
                **self.paths,
                apply=True,
                cache_path=cache_path,
                approval_artifact_path=approval_path,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        self.assertEqual(caught.exception.code, "approval_items_mismatch")
        self.assertFalse(cache_path.exists())


if __name__ == "__main__":
    unittest.main()
