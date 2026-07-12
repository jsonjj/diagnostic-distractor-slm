from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

import services.wayline_forge.scripts.build_reviewed_cache as build_module
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.curriculum import CURRICULUM_V1_SHA256
from services.wayline_forge.app.procedure_registry import (
    PROCEDURE_REGISTRY_V1_SHA256,
)
from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.reviewed_cache import (
    CacheKey,
    ReviewedCache,
)
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle
from services.wayline_forge.scripts.build_reviewed_cache import (
    BUILD_APPROVAL_SCHEMA_VERSION,
    BUILD_INPUT_SCHEMA_VERSION,
    BUILD_MANIFEST_SCHEMA_VERSION,
    CacheBuildError,
    build_reviewed_cache,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class ReviewedCacheBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = DistractorVerifier.for_tests()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.input_path = self.root / "build-input.json"
        self.destination = self.root / "reviewed.sqlite3"

    @classmethod
    def bundle(cls, seed: int) -> VerifiedQuestionBundle:
        request = CompileRequest(
            "valuehold",
            "place_value",
            "place_value",
            2,
            seed,
        )
        blueprint = cls.verifier.compiler.compile(request)
        distractors = [
            {
                "answer": cls.verifier.registry.evaluate(
                    procedure_id,
                    blueprint,
                ).display,
                "computation": cls.verifier.registry.canonical_computation(
                    procedure_id,
                    blueprint,
                ),
                "misconception": cls.verifier.registry.canonical_label(
                    procedure_id
                ),
            }
            for procedure_id in blueprint.allowed_procedure_ids[:3]
        ]
        generation = replace(
            cls.verifier.fixture_generation(blueprint, "accepted.json"),
            text=_canonical_json({"distractors": distractors}),
            generated_at_utc="2026-07-11T18:00:00Z",
        )
        verification = cls.verifier.verify_generation(blueprint, generation)
        if not verification.accepted or verification.value is None:
            raise AssertionError(verification.code)
        return VerifiedQuestionBundle.from_verified(
            compiler=cls.verifier.compiler,
            request=request,
            blueprint=blueprint,
            verified=verification.value,
            generation=generation,
            manifest=cls.verifier.manifest,
        )

    @staticmethod
    def approval(
        bundle: VerifiedQuestionBundle,
        **overrides: object,
    ) -> dict[str, object]:
        unsigned: dict[str, object] = {
            "approvedCacheContentSha256": bundle.cache_content_sha256,
            "approvedSemanticContentSha256": bundle.semantic_content_sha256,
            "decision": "approved",
            "ownerAlias": "owner-01",
            "reviewedAtUtc": "2026-07-11T19:30:00Z",
            "schemaVersion": BUILD_APPROVAL_SCHEMA_VERSION,
        }
        unsigned.update(overrides)
        approval_record_sha256 = hashlib.sha256(
            _canonical_json(unsigned).encode("utf-8")
        ).hexdigest()
        return unsigned | {"approvalRecordSha256": approval_record_sha256}

    @staticmethod
    def input_item(
        bundle: VerifiedQuestionBundle,
        approval: dict[str, object],
    ) -> dict[str, object]:
        return {
            "approval": approval,
            "bundle": json.loads(bundle.to_private_json()),
        }

    def write_input(self, items: list[dict[str, object]]) -> None:
        self.input_path.write_text(
            _canonical_json(
                {
                    "items": items,
                    "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                }
            ),
            encoding="utf-8",
        )
        self.input_path.chmod(0o600)

    def write_raw(self, raw: bytes) -> None:
        self.input_path.write_bytes(raw)
        self.input_path.chmod(0o600)

    def assert_build_error(
        self,
        code: str,
        *,
        input_path: Path | None = None,
        destination: Path | None = None,
    ) -> CacheBuildError:
        with self.assertRaises(CacheBuildError) as caught:
            build_reviewed_cache(
                input_path or self.input_path,
                destination or self.destination,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertIsNone(caught.exception.__cause__)
        return caught.exception

    def test_builds_cache_and_reopens_it_in_learner_mode(self) -> None:
        bundle = self.bundle(9301)
        approval = self.approval(bundle)
        self.write_input([self.input_item(bundle, approval)])

        result = build_reviewed_cache(
            self.input_path,
            self.destination,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

        self.assertEqual(result.schema_version, BUILD_MANIFEST_SCHEMA_VERSION)
        self.assertEqual(result.item_count, 1)
        self.assertRegex(result.manifest_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(result.logical_content_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(result.database_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(result.database_size, self.destination.stat().st_size)
        self.assertTrue(self.destination.is_file())
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o400)
        for suffix in ("-journal", "-shm", "-wal"):
            self.assertFalse(Path(str(self.destination) + suffix).exists())
        key = CacheKey(
            world_id=bundle.blueprint.world_id,
            skill_id=bundle.blueprint.skill_id,
            family_id=bundle.blueprint.family_id,
            difficulty=bundle.blueprint.difficulty,
            required_procedure_ids=(),
            registry_id=self.verifier.registry.registry_id,
            curriculum_id=self.verifier.compiler.curriculum.curriculum_id,
            selection_seed=17,
        )
        with ReviewedCache.open_learner(
            self.destination,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        ) as cache:
            self.assertFalse(cache.writable)
            hit = cache.lookup_reviewed(key)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(
            hit.bundle.to_private_json(),
            bundle.to_private_json(),
        )
        serialized = result.manifest_json.encode("utf-8") + self.destination.read_bytes()
        for banned in (b"rawGeneration", b"profileId", b"learnerId", b"private-child"):
            self.assertNotIn(banned, serialized)

        manifest = json.loads(result.manifest_json)
        self.assertEqual(manifest["schemaVersion"], BUILD_MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest["database"]["sha256"], result.database_sha256)
        self.assertEqual(manifest["database"]["sizeBytes"], result.database_size)
        self.assertEqual(manifest["logicalContentSha256"], result.logical_content_sha256)
        self.assertEqual(manifest["runtime"]["registrySha256"], PROCEDURE_REGISTRY_V1_SHA256)
        self.assertEqual(manifest["runtime"]["curriculumSha256"], CURRICULUM_V1_SHA256)
        item = manifest["items"][0]
        self.assertEqual(item["approvalRecordSha256"], approval["approvalRecordSha256"])
        self.assertEqual(item["reviewerAlias"], "owner-01")
        self.assertEqual(item["decision"], "approved")
        self.assertEqual(
            item["verifierReceiptSha256"],
            bundle.provenance.verifier_receipt_sha256,
        )
        self.assertEqual(item["modelSha256"], bundle.provenance.model_sha256)
        self.assertEqual(
            item["holdoutReceipt"]["sourceSha256"],
            bundle.blueprint.holdout_receipt.source_sha256,
        )
        self.assertEqual(
            hashlib.sha256(self.destination.read_bytes()).hexdigest(),
            result.database_sha256,
        )

    def test_duplicate_keys_and_unknown_fields_leave_destination_unchanged(
        self,
    ) -> None:
        bundle = self.bundle(9302)
        approval = self.approval(bundle)
        item = self.input_item(bundle, approval)
        item_json = _canonical_json(item)
        cases = (
            (
                "duplicate-key",
                (
                    '{"items":['
                    + item_json
                    + '],"items":['
                    + item_json
                    + '],"schemaVersion":"'
                    + BUILD_INPUT_SCHEMA_VERSION
                    + '"}'
                ).encode("utf-8"),
                "duplicate_json_key",
            ),
            (
                "unknown-top-level",
                _canonical_json(
                    {
                        "items": [item],
                        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                        "unexpected": True,
                    }
                ).encode("utf-8"),
                "invalid_build_input",
            ),
            (
                "unknown-item",
                _canonical_json(
                    {
                        "items": [item | {"rawGeneration": "private-child"}],
                        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                    }
                ).encode("utf-8"),
                "invalid_build_input",
            ),
            (
                "unknown-approval",
                _canonical_json(
                    {
                        "items": [
                            self.input_item(
                                bundle,
                                approval | {"profileId": "private-child"},
                            )
                        ],
                        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                    }
                ).encode("utf-8"),
                "invalid_approval",
            ),
            (
                "unknown-bundle",
                _canonical_json(
                    {
                        "items": [
                            {
                                "approval": approval,
                                "bundle": item["bundle"]
                                | {"rawGeneration": "private-child"},
                            }
                        ],
                        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                    }
                ).encode("utf-8"),
                "invalid_verified_bundle",
            ),
            (
                "bom",
                b"\xef\xbb\xbf"
                + _canonical_json(
                    {
                        "items": [item],
                        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                    }
                ).encode("utf-8"),
                "invalid_build_input",
            ),
            ("blank", b"", "build_input_size_invalid"),
            ("malformed", b'{"items":[', "invalid_build_input"),
            (
                "nonfinite",
                (
                    '{"items":['
                    + item_json
                    + '],"schemaVersion":"'
                    + BUILD_INPUT_SCHEMA_VERSION
                    + '","unexpected":NaN}'
                ).encode("utf-8"),
                "invalid_build_input",
            ),
        )

        for name, raw, code in cases:
            with self.subTest(name=name):
                self.write_raw(raw)
                self.assert_build_error(code)
                self.assertFalse(self.destination.exists())
                self.assertEqual(list(self.root.glob(".reviewed.sqlite3.*")), [])

        valid_raw = _canonical_json(
            {
                "items": [item],
                "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
            }
        ).encode("utf-8")
        with patch.object(build_module, "_MAX_INPUT_BYTES", len(valid_raw) - 1):
            self.write_raw(valid_raw)
            self.assert_build_error("build_input_size_invalid")
        self.assertFalse(self.destination.exists())

    def test_approval_is_canonical_and_bound_to_both_bundle_hashes(self) -> None:
        bundle = self.bundle(9303)
        valid = self.approval(bundle)
        cases = (
            (valid | {"approvalRecordSha256": "f" * 64}, "invalid_approval"),
            (
                self.approval(
                    bundle,
                    approvedSemanticContentSha256="e" * 64,
                ),
                "approval_bundle_mismatch",
            ),
            (
                self.approval(
                    bundle,
                    approvedCacheContentSha256="d" * 64,
                ),
                "approval_bundle_mismatch",
            ),
            (self.approval(bundle, decision="rejected"), "invalid_approval"),
            (
                self.approval(bundle, schemaVersion="wayline.review-approval.v2"),
                "invalid_approval",
            ),
        )
        for approval, code in cases:
            with self.subTest(code=code, approval=approval):
                self.write_input([self.input_item(bundle, approval)])
                self.assert_build_error(code)
                self.assertFalse(self.destination.exists())

    def test_duplicate_bundle_is_rejected_before_temporary_database_creation(
        self,
    ) -> None:
        bundle = self.bundle(9304)
        item = self.input_item(bundle, self.approval(bundle))
        self.write_input([item, item])

        self.assert_build_error("duplicate_build_item")

        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob(".reviewed.sqlite3.*")), [])

    def test_reversed_inputs_have_identical_logical_and_physical_manifest(
        self,
    ) -> None:
        bundles = (self.bundle(9305), self.bundle(9306))
        items = [
            self.input_item(bundle, self.approval(bundle))
            for bundle in bundles
        ]
        first_input = self.root / "first.json"
        second_input = self.root / "second.json"
        first_destination = self.root / "first.sqlite3"
        second_destination = self.root / "second.sqlite3"
        for path, ordered in ((first_input, items), (second_input, list(reversed(items)))):
            path.write_text(
                _canonical_json(
                    {
                        "items": ordered,
                        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                    }
                ),
                encoding="utf-8",
            )
            path.chmod(0o600)

        first = build_reviewed_cache(
            first_input,
            first_destination,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        second = build_reviewed_cache(
            second_input,
            second_destination,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

        self.assertEqual(first.logical_content_sha256, second.logical_content_sha256)
        self.assertEqual(first.manifest_json, second.manifest_json)
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(first.database_sha256, second.database_sha256)

    def test_untrusted_paths_and_existing_destination_fail_closed(self) -> None:
        bundle = self.bundle(9307)
        self.write_input([self.input_item(bundle, self.approval(bundle))])

        self.input_path.chmod(0o644)
        self.assert_build_error("unsafe_build_input")
        self.input_path.chmod(0o600)

        real_input = self.root / "real-input.json"
        self.input_path.replace(real_input)
        self.input_path.symlink_to(real_input)
        self.assert_build_error("unsafe_build_input")
        self.input_path.unlink()
        real_input.replace(self.input_path)

        sentinel = b"existing destination"
        self.destination.write_bytes(sentinel)
        self.destination.chmod(0o600)
        self.assert_build_error("destination_exists")
        self.assertEqual(self.destination.read_bytes(), sentinel)
        self.destination.unlink()

        target = self.root / "target.sqlite3"
        target.write_bytes(sentinel)
        target.chmod(0o600)
        self.destination.symlink_to(target)
        self.assert_build_error("unsafe_destination")
        self.assertEqual(target.read_bytes(), sentinel)
        self.destination.unlink()

        output_parent = self.root / "group-writable"
        output_parent.mkdir(mode=0o700)
        output_parent.chmod(0o720)
        try:
            self.assert_build_error(
                "unsafe_output_parent",
                destination=output_parent / "cache.sqlite3",
            )
        finally:
            output_parent.chmod(0o700)

        nested = self.root / "nested"
        nested.mkdir(mode=0o700)
        unnormalized = nested / ".." / "cache.sqlite3"
        self.assert_build_error("unsafe_path", destination=unnormalized)

    def test_every_row_is_eagerly_revalidated_before_and_after_publish(self) -> None:
        bundles = (self.bundle(9308), self.bundle(9309))
        self.write_input(
            [
                self.input_item(bundle, self.approval(bundle))
                for bundle in bundles
            ]
        )
        calls: list[str] = []
        original = ReviewedCache._validate_row

        def tracked(cache: ReviewedCache, row: sqlite3.Row):
            result = original(cache, row)
            calls.append(result.bundle.cache_content_sha256)
            return result

        with patch.object(ReviewedCache, "_validate_row", new=tracked):
            build_reviewed_cache(
                self.input_path,
                self.destination,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        self.assertEqual(
            sorted(calls),
            sorted(
                [bundle.cache_content_sha256 for bundle in bundles] * 2
            ),
        )

    def test_replace_failure_leaves_no_destination_or_temporary_files(self) -> None:
        bundle = self.bundle(9310)
        self.write_input([self.input_item(bundle, self.approval(bundle))])

        with patch.object(build_module.os, "replace", side_effect=OSError("private")):
            self.assert_build_error("destination_publish_failed")

        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob(".reviewed.sqlite3.*")), [])

    def test_parent_fsync_failure_reports_the_post_replace_boundary(self) -> None:
        bundle = self.bundle(9311)
        self.write_input([self.input_item(bundle, self.approval(bundle))])

        with patch.object(
            build_module,
            "_fsync_parent_directory",
            side_effect=OSError("private"),
            create=True,
        ):
            self.assert_build_error("publish_durability_uncertain")

        self.assertTrue(self.destination.is_file())

    def test_manifest_preserves_each_cache_hash_row_hash_binding(self) -> None:
        # These three receipts sort differently by semantic and row hash; the
        # old builder deterministically associated two of the three wrongly.
        bundles = tuple(self.bundle(seed) for seed in (9403, 9404, 9405))
        self.write_input(
            [
                self.input_item(bundle, self.approval(bundle))
                for bundle in reversed(bundles)
            ]
        )

        result = build_reviewed_cache(
            self.input_path,
            self.destination,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

        connection = sqlite3.connect(
            self.destination.as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            expected = dict(
                connection.execute(
                    "SELECT cache_content_sha256, row_hash "
                    "FROM reviewed_questions"
                ).fetchall()
            )
        finally:
            connection.close()
        actual = {
            item["cacheContentSha256"]: item["rowSha256"]
            for item in json.loads(result.manifest_json)["items"]
        }
        self.assertEqual(actual, expected)

    def test_logical_digest_is_recomputable_from_manifest_fields(self) -> None:
        bundle = self.bundle(9315)
        self.write_input([self.input_item(bundle, self.approval(bundle))])

        result = build_reviewed_cache(
            self.input_path,
            self.destination,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

        manifest = json.loads(result.manifest_json)
        logical = {
            "buildInputCanonicalSha256": manifest[
                "buildInputCanonicalSha256"
            ],
            "items": manifest["items"],
            "runtime": manifest["runtime"],
            "schemaVersion": "wayline.reviewed-cache-logical-content.v1",
        }
        self.assertEqual(
            hashlib.sha256(_canonical_json(logical).encode("utf-8")).hexdigest(),
            result.logical_content_sha256,
        )

    def test_final_database_hash_is_recomputed_after_publish(self) -> None:
        bundle = self.bundle(9316)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        original = build_module._fsync_and_hash_database
        calls = 0

        def mismatched_second_hash(*args, **kwargs):
            nonlocal calls
            calls += 1
            digest, size = original(*args, **kwargs)
            if calls == 2:
                digest = "f" * 64
            return digest, size

        with patch.object(
            build_module,
            "_fsync_and_hash_database",
            new=mismatched_second_hash,
        ):
            self.assert_build_error("published_cache_invalid")

        self.assertEqual(calls, 2)
        self.assertFalse(self.destination.exists())

        retry = build_reviewed_cache(
            self.input_path,
            self.destination,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.assertEqual(
            hashlib.sha256(self.destination.read_bytes()).hexdigest(),
            retry.database_sha256,
        )

    def test_cleanup_parent_fsync_failure_is_never_silently_swallowed(self) -> None:
        bundle = self.bundle(9322)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        original_hash = build_module._fsync_and_hash_database
        original_parent_fsync = build_module._fsync_parent_directory
        hash_calls = 0
        fsync_calls = 0

        def mismatched_second_hash(*args, **kwargs):
            nonlocal hash_calls
            hash_calls += 1
            digest, size = original_hash(*args, **kwargs)
            if hash_calls == 2:
                digest = "f" * 64
            return digest, size

        def fail_cleanup_fsync(descriptor):
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 2:
                raise OSError("cleanup durability is uncertain")
            return original_parent_fsync(descriptor)

        with (
            patch.object(
                build_module,
                "_fsync_and_hash_database",
                new=mismatched_second_hash,
            ),
            patch.object(
                build_module,
                "_fsync_parent_directory",
                new=fail_cleanup_fsync,
            ),
        ):
            self.assert_build_error("cleanup_durability_uncertain")

        self.assertEqual(hash_calls, 2)
        self.assertEqual(fsync_calls, 2)
        self.assertFalse(self.destination.exists())

    def test_cleanup_quarantine_restores_foreign_inode_before_removal(self) -> None:
        bundle = self.bundle(9323)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        original_hash = build_module._fsync_and_hash_database
        original_replace = build_module.os.replace
        hash_calls = 0
        swapped = False
        foreign = b"foreign inode survives quarantine"

        def mismatched_second_hash(*args, **kwargs):
            nonlocal hash_calls
            hash_calls += 1
            digest, size = original_hash(*args, **kwargs)
            if hash_calls == 2:
                digest = "f" * 64
            return digest, size

        def swap_before_quarantine(source, target, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and source == self.destination.name
                and target == "artifact"
                and kwargs.get("src_dir_fd") is not None
                and kwargs.get("dst_dir_fd") is not None
                and kwargs["src_dir_fd"] != kwargs["dst_dir_fd"]
            ):
                replacement = self.root / "foreign-cleanup-swap.tmp"
                replacement.write_bytes(foreign)
                replacement.chmod(0o400)
                original_replace(replacement, self.destination)
                swapped = True
            return original_replace(source, target, *args, **kwargs)

        with (
            patch.object(
                build_module,
                "_fsync_and_hash_database",
                new=mismatched_second_hash,
            ),
            patch.object(build_module.os, "replace", new=swap_before_quarantine),
        ):
            self.assert_build_error("published_cache_unsafe")

        self.assertTrue(swapped)
        self.assertEqual(self.destination.read_bytes(), foreign)
        self.assertEqual(
            list(self.root.glob(f".{self.destination.name}.*.cleanup")),
            [],
        )

    def test_baseexceptions_after_replace_clean_the_exact_published_inode(self) -> None:
        bundle = self.bundle(9324)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        original_replace = build_module.os.replace

        for exceptional in (KeyboardInterrupt(), SystemExit(7)):
            destination = self.root / (
                type(exceptional).__name__.lower() + ".sqlite3"
            )
            interrupted = False

            def replace_then_interrupt(source, target, *args, **kwargs):
                nonlocal interrupted
                result = original_replace(source, target, *args, **kwargs)
                if not interrupted and target == destination.name:
                    interrupted = True
                    raise exceptional
                return result

            with self.subTest(exception=type(exceptional).__name__), patch.object(
                build_module.os,
                "replace",
                new=replace_then_interrupt,
            ):
                with self.assertRaises(type(exceptional)):
                    build_reviewed_cache(
                        self.input_path,
                        destination,
                        compiler=self.verifier.compiler,
                        manifest=self.verifier.manifest,
                    )
                self.assertTrue(interrupted)
                self.assertFalse(destination.exists())
                self.assertEqual(
                    list(self.root.glob(f".{destination.name}.*")),
                    [],
                )

    def test_keyboard_interrupt_survives_cleanup_fsync_uncertainty(self) -> None:
        bundle = self.bundle(9325)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        destination = self.root / "keyboard-cleanup.sqlite3"
        original_replace = build_module.os.replace
        interrupted = False

        def replace_then_interrupt(source, target, *args, **kwargs):
            nonlocal interrupted
            result = original_replace(source, target, *args, **kwargs)
            if not interrupted and target == destination.name:
                interrupted = True
                raise KeyboardInterrupt()
            return result

        with (
            patch.object(build_module.os, "replace", new=replace_then_interrupt),
            patch.object(
                build_module,
                "_fsync_parent_directory",
                side_effect=OSError("private cleanup fsync detail"),
            ),
            self.assertRaises(KeyboardInterrupt) as caught,
        ):
            build_reviewed_cache(
                self.input_path,
                destination,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        self.assertTrue(interrupted)
        self.assertFalse(destination.exists())
        self.assertIsInstance(caught.exception.__cause__, CacheBuildError)
        self.assertEqual(
            caught.exception.__cause__.code,
            "cleanup_durability_uncertain",
        )
        self.assertEqual(
            str(caught.exception.__cause__),
            "cleanup_durability_uncertain",
        )

    def test_system_exit_survives_foreign_cleanup_failure(self) -> None:
        bundle = self.bundle(9326)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        destination = self.root / "system-exit-cleanup.sqlite3"
        original_replace = build_module.os.replace
        interrupted = False
        swapped = False
        foreign = b"foreign inode remains after SystemExit"

        def interrupt_then_swap(source, target, *args, **kwargs):
            nonlocal interrupted, swapped
            if (
                interrupted
                and not swapped
                and source == destination.name
                and target == "artifact"
                and kwargs.get("src_dir_fd") != kwargs.get("dst_dir_fd")
            ):
                replacement = self.root / "system-exit-foreign.tmp"
                replacement.write_bytes(foreign)
                replacement.chmod(0o400)
                original_replace(replacement, destination)
                swapped = True
            result = original_replace(source, target, *args, **kwargs)
            if not interrupted and target == destination.name:
                interrupted = True
                raise SystemExit(23)
            return result

        with (
            patch.object(build_module.os, "replace", new=interrupt_then_swap),
            self.assertRaises(SystemExit) as caught,
        ):
            build_reviewed_cache(
                self.input_path,
                destination,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        self.assertEqual(caught.exception.code, 23)
        self.assertTrue(interrupted)
        self.assertTrue(swapped)
        self.assertEqual(destination.read_bytes(), foreign)
        self.assertIsInstance(caught.exception.__cause__, CacheBuildError)
        self.assertEqual(
            caught.exception.__cause__.code,
            "published_cache_unsafe",
        )
        self.assertEqual(
            str(caught.exception.__cause__),
            "published_cache_unsafe",
        )

    def test_foreign_post_publish_inode_is_never_unlinked(self) -> None:
        bundle = self.bundle(9321)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        original = build_module._fsync_and_hash_database
        calls = 0
        foreign = b"foreign inode must survive"

        def replace_on_second_hash(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original(*args, **kwargs)
            if calls == 2:
                replacement = self.root / "foreign.tmp"
                replacement.write_bytes(foreign)
                replacement.chmod(0o400)
                os.replace(replacement, self.destination)
            return result

        with patch.object(
            build_module,
            "_fsync_and_hash_database",
            new=replace_on_second_hash,
        ):
            self.assert_build_error("published_cache_unsafe")

        self.assertEqual(calls, 2)
        self.assertEqual(self.destination.read_bytes(), foreign)

    def test_temp_inode_swap_is_rejected_before_publish(self) -> None:
        bundle = self.bundle(9317)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        original = build_module._audit_database
        calls = 0

        def audit_then_swap(path: Path, **kwargs):
            nonlocal calls
            result = original(path, **kwargs)
            calls += 1
            if calls == 1:
                replacement = path.with_name(path.name + ".replacement")
                shutil.copyfile(path, replacement)
                replacement.chmod(0o600)
                os.replace(replacement, path)
            return result

        with patch.object(build_module, "_audit_database", new=audit_then_swap):
            self.assert_build_error("cache_identity_changed")

        self.assertEqual(calls, 1)
        self.assertFalse(self.destination.exists())

    def test_keyboard_interrupt_and_system_exit_are_not_normalized(self) -> None:
        bundle = self.bundle(9318)
        self.write_input([self.input_item(bundle, self.approval(bundle))])

        for exceptional in (KeyboardInterrupt(), SystemExit(7)):
            with self.subTest(exception=type(exceptional).__name__), patch.object(
                build_module,
                "_decode_build_input",
                side_effect=exceptional,
            ):
                with self.assertRaises(type(exceptional)):
                    build_reviewed_cache(
                        self.input_path,
                        self.destination,
                        compiler=self.verifier.compiler,
                        manifest=self.verifier.manifest,
                    )
                self.assertFalse(self.destination.exists())

    def test_intermediate_symlink_hardlink_and_nonregular_input_fail_closed(
        self,
    ) -> None:
        bundle = self.bundle(9319)
        item = self.input_item(bundle, self.approval(bundle))
        self.write_input([item])

        hardlink = self.root / "hardlinked-input.json"
        os.link(self.input_path, hardlink)
        self.assert_build_error("unsafe_build_input")
        hardlink.unlink()

        directory_input = self.root / "directory-input.json"
        directory_input.mkdir(mode=0o700)
        self.assert_build_error(
            "unsafe_build_input",
            input_path=directory_input,
        )

        real_parent = self.root / "real-parent"
        real_parent.mkdir(mode=0o700)
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        linked_input = real_parent / "input.json"
        linked_input.write_text(
            _canonical_json(
                {
                    "items": [item],
                    "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                }
            ),
            encoding="utf-8",
        )
        linked_input.chmod(0o600)
        self.assert_build_error(
            "unsafe_build_input",
            input_path=linked_parent / "input.json",
        )
        self.assert_build_error(
            "unsafe_output_parent",
            destination=linked_parent / "output.sqlite3",
        )

    def test_database_fsync_precedes_replace_and_parent_fsync_follows(self) -> None:
        bundle = self.bundle(9320)
        self.write_input([self.input_item(bundle, self.approval(bundle))])
        events: list[str] = []
        real_hash = build_module._fsync_and_hash_database
        real_replace = build_module.os.replace
        real_parent_fsync = build_module._fsync_parent_directory

        def tracked_hash(*args, **kwargs):
            events.append("database-hash-fsync")
            return real_hash(*args, **kwargs)

        def tracked_replace(*args, **kwargs):
            events.append("replace")
            return real_replace(*args, **kwargs)

        def tracked_parent_fsync(*args, **kwargs):
            events.append("parent-fsync")
            return real_parent_fsync(*args, **kwargs)

        with (
            patch.object(
                build_module,
                "_fsync_and_hash_database",
                new=tracked_hash,
            ),
            patch.object(build_module.os, "replace", new=tracked_replace),
            patch.object(
                build_module,
                "_fsync_parent_directory",
                new=tracked_parent_fsync,
            ),
        ):
            build_reviewed_cache(
                self.input_path,
                self.destination,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        self.assertEqual(
            events,
            [
                "database-hash-fsync",
                "replace",
                "parent-fsync",
                "database-hash-fsync",
            ],
        )

    def test_file_identity_tracks_mtime_and_ctime(self) -> None:
        path = self.root / "identity.bin"
        path.write_bytes(b"identity")
        path.chmod(0o600)
        details = path.stat()
        identity = build_module._file_identity(details)

        self.assertEqual(identity.modified_ns, details.st_mtime_ns)
        self.assertEqual(identity.changed_ns, details.st_ctime_ns)
        before = build_module._stable_file_identity(details)
        os.chmod(path, 0o400)
        os.chmod(path, 0o600)
        after = build_module._stable_file_identity(path.stat())
        self.assertNotEqual(before, after)

    def test_same_size_mutation_during_hash_is_detected(self) -> None:
        path = self.root / "mutable.sqlite3"
        path.write_bytes(b"A" * (1024 * 1024))
        path.chmod(0o600)
        expected = build_module._file_identity(path.stat())
        real_read = build_module.os.read
        mutator = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, mutator)
        mutated = False

        def mutate_after_read(descriptor: int, maximum: int) -> bytes:
            nonlocal mutated
            value = real_read(descriptor, maximum)
            if value and not mutated:
                mutated = True
                os.pwrite(mutator, b"B", 0)
                os.fsync(mutator)
            return value

        with patch.object(build_module.os, "read", new=mutate_after_read):
            with self.assertRaises(CacheBuildError) as caught:
                build_module._fsync_and_hash_database(
                    path,
                    expected_identity=expected,
                    make_read_only=True,
                )

        self.assertTrue(mutated)
        self.assertEqual(caught.exception.code, "cache_identity_changed")


if __name__ == "__main__":
    unittest.main()
