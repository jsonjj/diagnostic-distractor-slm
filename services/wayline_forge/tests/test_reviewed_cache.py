from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.wayline_forge.app.curriculum import CURRICULUM_V1_SHA256
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.procedure_registry import PROCEDURE_REGISTRY_V1_SHA256
from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.reviewed_cache import (
    CacheCorruptionError,
    CacheKey,
    CacheModeError,
    CacheSchemaError,
    CacheWriteError,
    ReviewReceipt,
    ReviewedCache,
    ReviewedCacheHit,
    question_semantic_sha256,
)
from services.wayline_forge.app import slot_materializer as slot_materializer_module
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ReviewedCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = DistractorVerifier.for_tests()
        cls.registry_id = cls.verifier.registry.registry_id
        cls.curriculum_id = cls.verifier.compiler.curriculum.curriculum_id

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db_path = Path(self.temporary.name) / "reviewed.sqlite3"

    @classmethod
    def bundle(
        cls,
        *,
        seed: int,
        world_id: str = "valuehold",
        skill_id: str = "place_value",
        family_id: str = "place_value",
        difficulty: int = 2,
        procedure_ids: tuple[str, ...] | None = None,
        generated_at_utc: str = "2026-07-11T18:00:00Z",
    ) -> VerifiedQuestionBundle:
        request = CompileRequest(
            world_id,
            skill_id,
            family_id,
            difficulty,
            seed,
        )
        blueprint = cls.verifier.compiler.compile(request)
        selected = procedure_ids or blueprint.allowed_procedure_ids[:3]
        if len(selected) != 3 or not set(selected).issubset(
            blueprint.allowed_procedure_ids
        ):
            raise AssertionError("test route selection is not valid for blueprint")
        distractors = []
        for procedure_id in selected:
            distractors.append({
                "misconception": cls.verifier.registry.canonical_label(procedure_id),
                "computation": cls.verifier.registry.canonical_computation(
                    procedure_id,
                    blueprint,
                ),
                "answer": cls.verifier.registry.evaluate(
                    procedure_id,
                    blueprint,
                ).display,
            })
        generation = replace(
            cls.verifier.fixture_generation(blueprint, "accepted.json"),
            text=_canonical_json({"distractors": distractors}),
            generated_at_utc=generated_at_utc,
        )
        result = cls.verifier.verify_generation(blueprint, generation)
        if not result.accepted or result.value is None:
            raise AssertionError(f"test bundle was rejected: {result.code}")
        return VerifiedQuestionBundle.from_verified(
            compiler=cls.verifier.compiler,
            request=request,
            blueprint=blueprint,
            verified=result.value,
            generation=generation,
            manifest=cls.verifier.manifest,
        )

    def open_build(self, path: Path | None = None) -> ReviewedCache:
        return ReviewedCache.open_build(
            path or self.db_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def open_learner(self, path: Path | None = None) -> ReviewedCache:
        return ReviewedCache.open_learner(
            path or self.db_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def key(self, **overrides: object) -> CacheKey:
        values = {
            "world_id": "valuehold",
            "skill_id": "place_value",
            "family_id": "place_value",
            "difficulty": 2,
            "required_procedure_ids": (),
            "registry_id": self.registry_id,
            "curriculum_id": self.curriculum_id,
            "selection_seed": 947,
            "excluded_question_ids": (),
            "excluded_template_ids": (),
            "excluded_operand_signatures": (),
            "excluded_content_ids": (),
            "excluded_question_semantic_sha256s": (),
            "excluded_context_ids": (),
        }
        values.update(overrides)
        return CacheKey(**values)

    @staticmethod
    def approval_record_for(bundle: VerifiedQuestionBundle) -> str:
        return _sha256_text(
            "external-owner-approval-v1|" + bundle.semantic_content_sha256
        )

    def review_for(
        self,
        bundle: VerifiedQuestionBundle,
        *,
        reviewed_at_utc: str = "2026-07-11T19:30:00Z",
    ) -> ReviewReceipt:
        return ReviewReceipt.approved(
            owner_alias="owner-01",
            reviewed_at_utc=reviewed_at_utc,
            approved_semantic_content_sha256=bundle.semantic_content_sha256,
            approval_record_sha256=self.approval_record_for(bundle),
        )

    def test_review_receipt_cryptographically_binds_external_approval_to_semantics(self):
        bundle = self.bundle(seed=11)
        receipt = self.review_for(bundle)
        self.assertEqual(receipt.decision, "approved")
        self.assertEqual(
            receipt.approved_semantic_content_sha256,
            bundle.semantic_content_sha256,
        )
        self.assertEqual(
            receipt.approval_record_sha256,
            self.approval_record_for(bundle),
        )
        self.assertRegex(receipt.decision_receipt_sha256, r"^[0-9a-f]{64}$")
        with self.assertRaises(FrozenInstanceError):
            receipt.approval_record_sha256 = "f" * 64
        with self.assertRaises(ValueError):
            replace(receipt, decision_receipt_sha256="f" * 64)
        with self.assertRaises(ValueError):
            replace(receipt, approval_record_sha256="e" * 64)

    def test_review_receipt_cannot_be_reused_for_a_different_bundle(self):
        approved = self.bundle(seed=13)
        different = self.bundle(seed=14)
        receipt = self.review_for(approved)
        with self.open_build() as cache:
            with self.assertRaises(CacheWriteError) as caught:
                cache.insert(different, receipt)
        self.assertEqual(
            caught.exception.code,
            "review_semantic_content_mismatch",
        )

    def test_semantically_identical_provenance_variants_are_unique(self):
        first = self.bundle(
            seed=15,
            generated_at_utc="2026-07-11T18:00:00Z",
        )
        later = self.bundle(
            seed=15,
            generated_at_utc="2026-07-11T20:00:00Z",
        )
        self.assertEqual(
            first.semantic_content_sha256,
            later.semantic_content_sha256,
        )
        self.assertNotEqual(
            first.cache_content_sha256,
            later.cache_content_sha256,
        )
        with self.open_build() as cache:
            cache.insert(first, self.review_for(first))
            with self.assertRaises(CacheWriteError) as caught:
                cache.insert(later, self.review_for(later))
        self.assertEqual(caught.exception.code, "duplicate_semantic_content")

    def test_serialized_review_receipt_is_closed_bound_and_revalidated(self):
        bundle = self.bundle(seed=16)
        review = self.review_for(bundle)
        with self.open_build() as cache:
            cache.insert(bundle, review)
        connection = sqlite3.connect(self.db_path)
        semantic_column, row_json = connection.execute(
            """
            SELECT semantic_content_sha256, row_json
            FROM reviewed_questions
            """
        ).fetchone()
        connection.close()
        row = json.loads(row_json)
        stored = row["review"]
        self.assertEqual(semantic_column, bundle.semantic_content_sha256)
        self.assertEqual(
            row["derived"]["semanticContentSha256"],
            bundle.semantic_content_sha256,
        )
        self.assertEqual(
            set(stored),
            {
                "approvalRecordSha256",
                "approvedSemanticContentSha256",
                "decision",
                "decisionReceiptSha256",
                "ownerAlias",
                "reviewedAtUtc",
            },
        )
        self.assertEqual(
            stored["approvedSemanticContentSha256"],
            bundle.semantic_content_sha256,
        )
        self.assertEqual(
            stored["decisionReceiptSha256"],
            review.decision_receipt_sha256,
        )
        with self.open_learner() as cache:
            restored = cache.get(self.key())
        self.assertEqual(restored.to_private_json(), bundle.to_private_json())

    def test_explicit_lookup_returns_an_immutable_validated_reviewed_hit(self):
        bundle = self.bundle(seed=17)
        review = self.review_for(bundle)
        with self.open_build() as cache:
            cache.insert(bundle, review)

        connection = sqlite3.connect(self.db_path)
        stored_row_hash = connection.execute(
            "SELECT row_hash FROM reviewed_questions"
        ).fetchone()[0]
        connection.close()

        with self.open_learner() as cache:
            hit = cache.lookup_reviewed(self.key())
            compatibility_bundle = cache.get(self.key())

        self.assertIsInstance(hit, ReviewedCacheHit)
        self.assertEqual(hit.bundle, bundle)
        self.assertEqual(hit.cache_row_sha256, stored_row_hash)
        self.assertEqual(
            hit.cache_content_sha256,
            bundle.cache_content_sha256,
        )
        self.assertEqual(
            hit.approved_semantic_content_sha256,
            bundle.semantic_content_sha256,
        )
        self.assertEqual(
            hit.review_decision_receipt_sha256,
            review.decision_receipt_sha256,
        )
        self.assertEqual(
            hit.approval_record_sha256,
            review.approval_record_sha256,
        )
        self.assertEqual(hit.reviewer_alias, review.owner_alias)
        self.assertEqual(hit.reviewed_at_utc, review.reviewed_at_utc)
        self.assertRegex(hit.hit_receipt_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(compatibility_bundle, bundle)

        with self.assertRaises(FrozenInstanceError):
            hit.cache_row_sha256 = "f" * 64
        with self.assertRaises(ValueError):
            replace(hit, cache_row_sha256="f" * 64)
        with self.assertRaises(ValueError):
            replace(hit, bundle=self.bundle(seed=18))

    def test_corrupt_unrelated_world_does_not_block_indexed_matching_lookup(self):
        matching = self.bundle(seed=18)
        unrelated = self.bundle(
            seed=18,
            world_id="decimara",
            skill_id="decimal_add_sub",
            family_id="decimal_add",
        )
        with self.open_build() as cache:
            cache.insert(matching, self.review_for(matching))
            cache.insert(unrelated, self.review_for(unrelated))
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            UPDATE reviewed_questions SET row_hash = ?
            WHERE world_id = 'decimara'
            """,
            ("f" * 64,),
        )
        connection.commit()
        connection.close()
        with self.open_learner() as cache:
            selected = cache.get(self.key())
        self.assertEqual(
            selected.semantic_content_sha256,
            matching.semantic_content_sha256,
        )

    def test_lookup_has_an_exact_validated_composite_index_and_query_plan(self):
        bundle = self.bundle(seed=19)
        with self.open_build() as cache:
            cache.insert(bundle, self.review_for(bundle))
        connection = sqlite3.connect(self.db_path)
        index_names = {
            row[1] for row in connection.execute(
                "PRAGMA index_list(reviewed_questions)"
            ).fetchall()
        }
        self.assertIn("reviewed_questions_lookup_v2", index_names)
        columns = tuple(
            row[2] for row in connection.execute(
                "PRAGMA index_info(reviewed_questions_lookup_v2)"
            ).fetchall()
        )
        self.assertEqual(columns, (
            "world_id",
            "skill_id",
            "family_id",
            "difficulty",
            "registry_id",
            "curriculum_id",
        ))
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT cache_content_sha256, semantic_content_sha256,
                   world_id, skill_id, family_id, difficulty,
                   question_id, template_id, operand_signature, content_id,
                   procedure_ids_json, registry_id, curriculum_id,
                   row_json, row_hash
            FROM reviewed_questions
            WHERE world_id = ? AND skill_id = ? AND family_id = ?
              AND difficulty = ? AND registry_id = ? AND curriculum_id = ?
            """,
            (
                "valuehold",
                "place_value",
                "place_value",
                2,
                self.registry_id,
                self.curriculum_id,
            ),
        ).fetchall()
        connection.close()
        detail = " ".join(str(row[3]) for row in plan)
        self.assertIn("USING INDEX reviewed_questions_lookup_v2", detail)
        statements = []
        with self.open_learner() as cache:
            cache._connection.set_trace_callback(statements.append)
            self.assertIsNotNone(cache.get(self.key()))
        lookup = next(
            statement for statement in statements
            if "FROM reviewed_questions" in statement
        )
        normalized = " ".join(lookup.split())
        for predicate in (
            "world_id =",
            "skill_id =",
            "family_id =",
            "difficulty =",
            "registry_id =",
            "curriculum_id =",
        ):
            self.assertIn(predicate, normalized)

    def test_unexpected_schema_index_is_rejected(self):
        bundle = self.bundle(seed=20)
        with self.open_build() as cache:
            cache.insert(bundle, self.review_for(bundle))
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            "CREATE INDEX attacker_index ON cache_metadata(schema_version)"
        )
        connection.close()
        with self.assertRaises(CacheSchemaError):
            self.open_learner()

    def test_key_and_review_receipt_are_strict_immutable_contracts(self):
        key = self.key()
        bundle = self.bundle(seed=21)
        review = self.review_for(bundle)
        self.assertEqual(key.difficulty_band, 2)
        with self.assertRaises(FrozenInstanceError):
            key.selection_seed = 1
        with self.assertRaises(FrozenInstanceError):
            review.decision = "rejected"

        invalid_keys = (
            {"difficulty": 0},
            {"difficulty": 4},
            {"difficulty": True},
            {"selection_seed": -1},
            {"required_procedure_ids": ("pv_face_value", "pv_face_value")},
            {"excluded_question_ids": ["not-a-tuple"]},
            {"excluded_question_ids": (["unhashable"],)},
            {"excluded_context_ids": ["not-a-tuple"]},
            {"excluded_question_semantic_sha256s": ("short",)},
            {
                "excluded_question_semantic_sha256s": (
                    "a" * 64,
                    "a" * 64,
                )
            },
        )
        for changes in invalid_keys:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.key(**changes)
        for changes in (
            {"owner_alias": ""},
            {"decision": "rejected"},
            {"reviewed_at_utc": "2026-07-11T14:30:00-05:00"},
            {"reviewed_at_utc": "2026-07-11 19:30:00Z"},
            {"approved_semantic_content_sha256": "short"},
            {"approval_record_sha256": "short"},
            {"approval_record_sha256": None},
            {"decision_receipt_sha256": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(review, **changes)

    def test_selection_is_deterministic_and_independent_of_insertion_order(self):
        bundles = tuple(self.bundle(seed=seed) for seed in (17, 29, 43, 59))
        paths = (
            self.db_path,
            Path(self.temporary.name) / "reverse.sqlite3",
        )
        for path, order in zip(paths, (bundles, tuple(reversed(bundles))), strict=True):
            with self.open_build(path) as cache:
                for bundle in order:
                    cache.insert(bundle, self.review_for(bundle))

        selections = []
        for path in paths:
            with self.open_learner(path) as cache:
                first = cache.get(self.key(selection_seed=81))
                replay = cache.get(self.key(selection_seed=81))
                self.assertIsNotNone(first)
                self.assertEqual(first, replay)
                selections.append(first.cache_content_sha256)
        self.assertEqual(selections[0], selections[1])

    def test_all_adjacent_question_template_operand_and_content_exclusions_apply(self):
        bundles = tuple(self.bundle(seed=seed) for seed in range(100, 124))
        with self.open_build() as cache:
            for bundle in bundles:
                cache.insert(bundle, self.review_for(bundle))
        with self.open_learner() as cache:
            adjacent = cache.get(self.key(selection_seed=7))
            self.assertIsNotNone(adjacent)
            selected = cache.get(self.key(
                selection_seed=7,
                excluded_question_ids=(adjacent.blueprint.question_id,),
                excluded_template_ids=(adjacent.template_id,),
                excluded_operand_signatures=(adjacent.operand_signature,),
                excluded_content_ids=(
                    adjacent.blueprint.content_sha256,
                    adjacent.cache_content_sha256,
                    adjacent.semantic_content_sha256,
                ),
            ))
        self.assertIsNotNone(selected)
        self.assertNotEqual(selected.blueprint.question_id, adjacent.blueprint.question_id)
        self.assertNotEqual(selected.template_id, adjacent.template_id)
        self.assertNotEqual(selected.operand_signature, adjacent.operand_signature)
        self.assertNotEqual(selected.blueprint.content_sha256, adjacent.blueprint.content_sha256)
        self.assertNotEqual(selected.cache_content_sha256, adjacent.cache_content_sha256)
        self.assertNotEqual(selected.semantic_content_sha256, adjacent.semantic_content_sha256)

    def test_context_and_question_semantic_exclusions_filter_reviewed_fallbacks(self):
        bundles = tuple(self.bundle(seed=seed) for seed in range(500, 540))
        with self.open_build() as cache:
            for bundle in bundles:
                cache.insert(bundle, self.review_for(bundle))
        with self.open_learner() as cache:
            baseline = cache.get(self.key(selection_seed=101))
            self.assertIsNotNone(baseline)
            semantic = question_semantic_sha256(baseline.blueprint)
            changed_question = cache.get(self.key(
                selection_seed=101,
                excluded_question_semantic_sha256s=(semantic,),
            ))
            changed_context = cache.get(self.key(
                selection_seed=101,
                excluded_context_ids=(baseline.context_id,),
            ))

        self.assertIsNotNone(changed_question)
        self.assertNotEqual(
            question_semantic_sha256(changed_question.blueprint),
            semantic,
        )
        self.assertIsNotNone(changed_context)
        self.assertNotEqual(changed_context.context_id, baseline.context_id)
        self.assertIs(
            slot_materializer_module.question_semantic_sha256,
            question_semantic_sha256,
        )

    def test_never_falls_back_across_skill_family_registry_or_curriculum(self):
        bundle = self.bundle(seed=211)
        with self.open_build() as cache:
            cache.insert(bundle, self.review_for(bundle))
        keys = (
            self.key(skill_id="mental_add_sub"),
            self.key(family_id="mental_add"),
            self.key(registry_id="wayline-procedures-v2"),
            self.key(curriculum_id="wayline-launch-core-v2"),
            self.key(world_id="decimara"),
            self.key(difficulty=1),
        )
        with self.open_learner() as cache:
            for key in keys:
                with self.subTest(key=key):
                    self.assertIsNone(cache.get(key))

    def test_required_routes_must_all_be_present(self):
        all_routes = self.verifier.compiler.compile(CompileRequest(
            "valuehold", "place_value", "place_value", 2, 301
        )).allowed_procedure_ids
        self.assertEqual(len(all_routes), 4)
        first = self.bundle(seed=301, procedure_ids=all_routes[:3])
        second = self.bundle(seed=301, procedure_ids=all_routes[1:])
        self.assertNotEqual(
            first.semantic_content_sha256,
            second.semantic_content_sha256,
        )
        with self.open_build() as cache:
            cache.insert(first, self.review_for(first))
            cache.insert(second, self.review_for(second))
        with self.open_learner() as cache:
            selected = cache.get(self.key(
                required_procedure_ids=(all_routes[0],),
            ))
            self.assertEqual(selected.cache_content_sha256, first.cache_content_sha256)
            self.assertIsNone(cache.get(self.key(
                required_procedure_ids=all_routes,
            )))

    def test_duplicate_content_insert_rolls_back_without_replacing_review(self):
        bundle = self.bundle(seed=401)
        review = self.review_for(bundle)
        with self.open_build() as cache:
            cache.insert(bundle, review)
            with self.assertRaises(CacheWriteError) as caught:
                cache.insert(
                    bundle,
                    self.review_for(
                        bundle,
                        reviewed_at_utc="2026-07-11T20:00:00Z",
                    ),
                )
            self.assertEqual(caught.exception.code, "duplicate_cache_content")
        connection = sqlite3.connect(self.db_path)
        self.addCleanup(connection.close)
        count, row_json = connection.execute(
            "SELECT COUNT(*), MIN(row_json) FROM reviewed_questions"
        ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(
            json.loads(row_json)["review"]["reviewedAtUtc"],
            review.reviewed_at_utc,
        )

    def test_corrupt_or_rehashed_tampered_rows_fail_closed(self):
        for case in (
            "bad_hash",
            "rehashed_derived_tamper",
            "unknown_field",
            "indexed_metadata_tamper",
            "forged_review_receipt",
            "reused_review_semantics",
            "semantic_column_tamper",
            "deep_json",
        ):
            with self.subTest(case=case):
                path = Path(self.temporary.name) / f"{case}.sqlite3"
                bundle = self.bundle(seed=500 + len(case))
                with self.open_build(path) as cache:
                    cache.insert(bundle, self.review_for(bundle))
                connection = sqlite3.connect(path)
                row_json = connection.execute(
                    "SELECT row_json FROM reviewed_questions"
                ).fetchone()[0]
                if case == "bad_hash":
                    connection.execute(
                        "UPDATE reviewed_questions SET row_hash = ?",
                        ("f" * 64,),
                    )
                elif case == "indexed_metadata_tamper":
                    connection.execute(
                        "UPDATE reviewed_questions SET skill_id = ?",
                        ("forged-skill",),
                    )
                elif case == "semantic_column_tamper":
                    connection.execute(
                        "UPDATE reviewed_questions SET semantic_content_sha256 = ?",
                        ("f" * 64,),
                    )
                elif case == "deep_json":
                    tampered = '{"x":' + "[" * 2_000 + "0" + "]" * 2_000 + "}"
                    connection.execute(
                        "UPDATE reviewed_questions SET row_json = ?, row_hash = ?",
                        (tampered, _sha256_text(tampered)),
                    )
                else:
                    row = json.loads(row_json)
                    if case == "rehashed_derived_tamper":
                        row["derived"]["questionId"] = "forged-question"
                    elif case == "forged_review_receipt":
                        row["review"]["decisionReceiptSha256"] = "f" * 64
                    elif case == "reused_review_semantics":
                        other = self.bundle(seed=900 + len(case))
                        other_review = self.review_for(other)
                        row["review"] = {
                            "approvalRecordSha256": (
                                other_review.approval_record_sha256
                            ),
                            "approvedSemanticContentSha256": (
                                other_review.approved_semantic_content_sha256
                            ),
                            "decision": other_review.decision,
                            "decisionReceiptSha256": (
                                other_review.decision_receipt_sha256
                            ),
                            "ownerAlias": other_review.owner_alias,
                            "reviewedAtUtc": other_review.reviewed_at_utc,
                        }
                    else:
                        row["unexpected"] = "field"
                    tampered = _canonical_json(row)
                    connection.execute(
                        "UPDATE reviewed_questions SET row_json = ?, row_hash = ?",
                        (tampered, _sha256_text(tampered)),
                    )
                connection.commit()
                connection.close()
                with self.open_learner(path) as cache:
                    with self.assertRaises(CacheCorruptionError):
                        cache.get(
                            self.key(skill_id="forged-skill")
                            if case == "indexed_metadata_tamper"
                            else self.key()
                        )

    def test_older_cache_user_version_is_rejected_without_implicit_migration(self):
        bundle = self.bundle(seed=590)
        with self.open_build() as cache:
            cache.insert(bundle, self.review_for(bundle))
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA user_version = 1")
        connection.close()
        with self.assertRaises(CacheSchemaError) as caught:
            self.open_learner()
        self.assertEqual(caught.exception.code, "unsupported_cache_schema")

    def test_learner_mode_denies_writes_and_survives_process_restart(self):
        bundle = self.bundle(seed=607)
        with self.open_build() as cache:
            self.assertTrue(cache.writable)
            cache.insert(bundle, self.review_for(bundle))
        with self.open_learner() as cache:
            self.assertFalse(cache.writable)
            other = self.bundle(seed=608)
            with self.assertRaises(CacheModeError):
                cache.insert(other, self.review_for(other))
            restored = cache.get(self.key())
            self.assertEqual(restored.to_private_json(), bundle.to_private_json())

    def test_learner_fd_opens_the_held_inode_and_does_not_own_caller_fd(self):
        held_bundle = self.bundle(seed=609)
        replacement_bundle = self.bundle(seed=610)
        replacement_path = Path(self.temporary.name) / "replacement.sqlite3"
        with self.open_build() as cache:
            cache.insert(held_bundle, self.review_for(held_bundle))
        with self.open_build(replacement_path) as cache:
            cache.insert(
                replacement_bundle,
                self.review_for(replacement_bundle),
            )

        descriptor = os.open(self.db_path, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        held_identity = os.fstat(descriptor)
        os.replace(replacement_path, self.db_path)

        with ReviewedCache.open_learner_fd(
            descriptor,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        ) as cache:
            selected = cache.get(self.key())

        self.assertEqual(selected, held_bundle)
        self.assertEqual(os.fstat(descriptor).st_ino, held_identity.st_ino)

    def test_schema_user_version_and_exact_runtime_receipts_are_enforced(self):
        bundle = self.bundle(seed=701)
        with self.open_build() as cache:
            cache.insert(bundle, self.review_for(bundle))

        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA user_version = 99")
        connection.close()
        with self.assertRaises(CacheSchemaError):
            self.open_learner()

        path = Path(self.temporary.name) / "manifest.sqlite3"
        with self.open_build(path) as cache:
            cache.insert(bundle, self.review_for(bundle))
        forged_manifest = replace(self.verifier.manifest, gguf_sha256="f" * 64)
        with self.assertRaises(CacheCorruptionError):
            ReviewedCache.open_learner(
                path,
                compiler=self.verifier.compiler,
                manifest=forged_manifest,
            )

        connection = sqlite3.connect(path)
        row_json = json.loads(connection.execute(
            "SELECT row_json FROM reviewed_questions"
        ).fetchone()[0])
        self.assertEqual(
            row_json["receipts"]["registrySha256"],
            PROCEDURE_REGISTRY_V1_SHA256,
        )
        self.assertEqual(
            row_json["receipts"]["curriculumSha256"],
            CURRICULUM_V1_SHA256,
        )
        connection.close()

    def test_cache_stores_no_raw_generation_or_learner_data(self):
        bundle = self.bundle(seed=809)
        raw_marker = "Does not align place values and adds both as tenths"
        with self.open_build() as cache:
            cache.insert(bundle, self.review_for(bundle))
        connection = sqlite3.connect(self.db_path)
        row_json = connection.execute(
            "SELECT row_json FROM reviewed_questions"
        ).fetchone()[0]
        connection.close()
        lowered = row_json.casefold()
        self.assertNotIn(raw_marker.casefold(), lowered)
        for forbidden in (
            "profileid",
            "learnerid",
            "sessionid",
            "confidence",
            "selectedoption",
            "rawslm",
            "rawoutput",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_public_placement_leaks_no_cache_key_or_route_fields(self):
        bundle = self.bundle(seed=907)
        with self.open_build() as cache:
            cache.insert(bundle, self.review_for(bundle))
        with self.open_learner() as cache:
            selected = cache.get(self.key(required_procedure_ids=(
                bundle.verified_distractors[0].procedure_id,
            )))
        public = selected.public_payload("item_" + "a" * 32)
        serialized = _canonical_json(public).casefold()
        self.assertEqual(set(public), {"itemId", "prompt", "options"})
        for forbidden in (
            "cache",
            "route",
            "procedure",
            "registry",
            "curriculum",
            "familyid",
            "skillid",
            "questionid",
            "templateid",
            "operand",
            "contenthash",
            "required",
            "excluded",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
