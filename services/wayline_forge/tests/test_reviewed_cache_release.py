from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

import services.wayline_forge.app.reviewed_cache_release as release_module
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.reviewed_cache import (
    CacheModeError,
    ReviewedCache,
)
from services.wayline_forge.app.reviewed_cache_release import (
    ReviewedCacheRelease,
    ReviewedCacheReleaseError,
)
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle
from services.wayline_forge.scripts.build_reviewed_cache import (
    BUILD_APPROVAL_SCHEMA_VERSION,
    BUILD_INPUT_SCHEMA_VERSION,
    build_reviewed_cache,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _descriptor_numbers(release: ReviewedCacheRelease) -> tuple[int, ...]:
    return tuple(
        getattr(record, "descriptor", record)
        for record in release._descriptors
    )


class ReviewedCacheReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = DistractorVerifier.for_tests()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.release_root = self.root / "release"
        self.release_root.mkdir(mode=0o700)
        self.generations = self.release_root / "generations"
        self.generations.mkdir(mode=0o700)
        self._assemble_release((9501, 9502))

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
    def approval(bundle: VerifiedQuestionBundle) -> dict[str, object]:
        unsigned: dict[str, object] = {
            "approvedCacheContentSha256": bundle.cache_content_sha256,
            "approvedSemanticContentSha256": bundle.semantic_content_sha256,
            "decision": "approved",
            "ownerAlias": "owner-01",
            "reviewedAtUtc": "2026-07-11T19:30:00Z",
            "schemaVersion": BUILD_APPROVAL_SCHEMA_VERSION,
        }
        return unsigned | {
            "approvalRecordSha256": _sha256(
                _canonical_json(unsigned).encode("utf-8")
            )
        }

    def _assemble_release(self, seeds: tuple[int, ...]) -> None:
        bundles = tuple(self.bundle(seed) for seed in seeds)
        items = [
            {
                "approval": self.approval(bundle),
                "bundle": json.loads(bundle.to_private_json()),
            }
            for bundle in bundles
        ]
        input_path = self.root / "build-input.json"
        input_path.write_text(
            _canonical_json(
                {
                    "items": items,
                    "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                }
            ),
            encoding="utf-8",
        )
        input_path.chmod(0o600)
        staged_database = self.root / "staged.sqlite3"
        result = build_reviewed_cache(
            input_path,
            staged_database,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.manifest = json.loads(result.manifest_json)
        self.original_manifest_bytes = result.manifest_json.encode("utf-8")
        self.manifest_sha256 = result.manifest_sha256
        self.generation_id = "generation-" + self.manifest_sha256
        self.generation = self.generations / self.generation_id
        self.generation.mkdir(mode=0o700)
        self.database = self.generation / "reviewed_cache.sqlite3"
        os.replace(staged_database, self.database)
        self.manifest_path = self.generation / "reviewed_cache_manifest.json"
        self.manifest_path.write_bytes(self.original_manifest_bytes)
        self.manifest_path.chmod(0o400)
        self.generation.chmod(0o500)
        self.pointer = self.release_root / "current.json"
        self._write_pointer(
            {
                "generationId": self.generation_id,
                "manifestSha256": self.manifest_sha256,
                "schemaVersion": "wayline.reviewed-cache-pointer.v1",
            }
        )

    def _write_pointer(
        self,
        value: object,
        *,
        path: Path | None = None,
    ) -> None:
        self._write_pointer_raw(
            _canonical_json(value).encode("utf-8"),
            path=path,
        )

    def _write_pointer_raw(
        self,
        raw: bytes,
        *,
        path: Path | None = None,
    ) -> None:
        target = path or self.pointer
        if target.exists() and not target.is_symlink():
            target.chmod(0o600)
        target.write_bytes(raw)
        target.chmod(0o400)

    def _recompute_logical_digest(self) -> None:
        logical = {
            "buildInputCanonicalSha256": self.manifest[
                "buildInputCanonicalSha256"
            ],
            "items": self.manifest["items"],
            "runtime": self.manifest["runtime"],
            "schemaVersion": "wayline.reviewed-cache-logical-content.v1",
        }
        self.manifest["logicalContentSha256"] = _sha256(
            _canonical_json(logical).encode("utf-8")
        )

    def _replace_manifest_raw_and_repoint(self, raw: bytes) -> None:
        self.generation.chmod(0o700)
        self.manifest_path.chmod(0o600)
        self.manifest_path.write_bytes(raw)
        self.manifest_path.chmod(0o400)
        new_sha256 = _sha256(raw)
        new_generation_id = "generation-" + new_sha256
        new_generation = self.generations / new_generation_id
        os.replace(self.generation, new_generation)
        self.generation = new_generation
        self.generation_id = new_generation_id
        self.manifest_sha256 = new_sha256
        self.database = self.generation / "reviewed_cache.sqlite3"
        self.manifest_path = self.generation / "reviewed_cache_manifest.json"
        self.generation.chmod(0o500)
        self._write_pointer(
            {
                "generationId": self.generation_id,
                "manifestSha256": self.manifest_sha256,
                "schemaVersion": "wayline.reviewed-cache-pointer.v1",
            }
        )

    def _write_manifest_and_repoint(self, *, recompute_logical: bool) -> None:
        if recompute_logical:
            self._recompute_logical_digest()
        self._replace_manifest_raw_and_repoint(
            _canonical_json(self.manifest).encode("utf-8")
        )

    def assert_release_error(
        self,
        code: str,
        *,
        pointer_name: str = "current.json",
    ) -> ReviewedCacheReleaseError:
        with self.assertRaises(ReviewedCacheReleaseError) as caught:
            ReviewedCacheRelease.open_pointer(
                self.release_root,
                pointer_name,
                compiler=self.verifier.compiler,
                model_manifest=self.verifier.manifest,
            )
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(str(self.root), repr(caught.exception))
        self.assertNotIn("private-child", repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        return caught.exception

    def test_open_current_rehashes_database_and_eagerly_validates_every_row(
        self,
    ) -> None:
        calls: list[tuple[str, str]] = []
        original = ReviewedCache._validate_row

        def tracked(cache: ReviewedCache, row: sqlite3.Row):
            hit = original(cache, row)
            calls.append(
                (hit.bundle.cache_content_sha256, hit.cache_row_sha256)
            )
            return hit

        with patch.object(ReviewedCache, "_validate_row", new=tracked):
            with ReviewedCacheRelease.open_current(
                self.release_root,
                compiler=self.verifier.compiler,
                model_manifest=self.verifier.manifest,
            ) as release:
                cache = release.cache
                self.assertEqual(release.generation_id, self.generation_id)
                self.assertFalse(cache.writable)
                self.assertEqual(
                    sorted(calls),
                    sorted(
                        (
                            item["cacheContentSha256"],
                            item["rowSha256"],
                        )
                        for item in self.manifest["items"]
                    ),
                )

        with self.assertRaises(CacheModeError):
            cache._ensure_open()
        serialized = (
            self.pointer.read_bytes()
            + self.manifest_path.read_bytes()
            + self.database.read_bytes()
        )
        for banned in (
            b"rawGeneration",
            b"learnerId",
            b"profileId",
            b"sessionId",
            b"confidence",
            b"truefoundry",
        ):
            self.assertNotIn(banned, serialized)

    def test_candidate_pointer_opens_and_unsafe_pointer_names_are_rejected(
        self,
    ) -> None:
        candidate = self.release_root / "candidate-pointer.json"
        self._write_pointer_raw(self.pointer.read_bytes(), path=candidate)
        with ReviewedCacheRelease.open_pointer(
            self.release_root,
            candidate.name,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        ) as release:
            self.assertEqual(release.generation_id, self.generation_id)

        for name in (
            "",
            ".",
            "..",
            ".hidden.json",
            "../current.json",
            "nested/current.json",
            "nested\\current.json",
            "/current.json",
        ):
            with self.subTest(name=name):
                self.assert_release_error("invalid_pointer_name", pointer_name=name)

        candidate.unlink()
        target = self.root / "candidate-target.json"
        target.write_bytes(self.pointer.read_bytes())
        target.chmod(0o400)
        candidate.symlink_to(target)
        self.assert_release_error(
            "pointer_invalid",
            pointer_name=candidate.name,
        )

    def test_duplicate_unknown_malformed_or_noncanonical_pointer_fails_closed(
        self,
    ) -> None:
        sha256 = self.manifest_sha256
        generation = self.generation_id
        invalid = (
            (
                '{"generationId":"'
                + generation
                + '","generationId":"'
                + generation
                + '","manifestSha256":"'
                + sha256
                + '","schemaVersion":"wayline.reviewed-cache-pointer.v1"}'
            ).encode("utf-8"),
            _canonical_json(
                {
                    "generationId": generation,
                    "manifestSha256": sha256,
                    "schemaVersion": "wayline.reviewed-cache-pointer.v1",
                    "private-child": True,
                }
            ).encode("utf-8"),
            b"{",
            b"\xef\xbb\xbf" + self.pointer.read_bytes(),
            self.pointer.read_bytes() + b"\n",
            (
                '{"generationId":"'
                + generation
                + '","manifestSha256":NaN,'
                '"schemaVersion":"wayline.reviewed-cache-pointer.v1"}'
            ).encode("utf-8"),
            _canonical_json(
                {
                    "generationId": "generation-" + "f" * 64,
                    "manifestSha256": sha256,
                    "schemaVersion": "wayline.reviewed-cache-pointer.v1",
                }
            ).encode("utf-8"),
        )
        for raw in invalid:
            with self.subTest(raw=raw[:32]):
                self._write_pointer_raw(raw)
                self.assert_release_error("pointer_invalid")

    def test_release_directory_pointer_generation_and_file_paths_fail_closed(
        self,
    ) -> None:
        cases = (
            (self.release_root, 0o755, "unsafe_release_path"),
            (self.generations, 0o750, "generation_invalid"),
            (self.generation, 0o700, "generation_invalid"),
            (self.pointer, 0o600, "pointer_invalid"),
            (self.manifest_path, 0o600, "generation_invalid"),
            (self.database, 0o600, "generation_invalid"),
        )
        originals = {
            path: stat.S_IMODE(path.stat().st_mode)
            for path, _mode, _code in cases
        }
        for path, mode, code in cases:
            with self.subTest(path=path.name, mode=oct(mode)):
                path.chmod(mode)
                try:
                    self.assert_release_error(code)
                finally:
                    path.chmod(originals[path])

    def test_symlinked_pointer_generation_or_generation_file_fails_closed(
        self,
    ) -> None:
        pointer_bytes = self.pointer.read_bytes()
        self.pointer.unlink()
        pointer_target = self.root / "pointer-target.json"
        pointer_target.write_bytes(pointer_bytes)
        pointer_target.chmod(0o400)
        self.pointer.symlink_to(pointer_target)
        self.assert_release_error("pointer_invalid")

        self.pointer.unlink()
        self._write_pointer_raw(pointer_bytes)
        real_generation = self.generations / "real-generation"
        os.replace(self.generation, real_generation)
        self.generation.symlink_to(real_generation.name, target_is_directory=True)
        self.assert_release_error("generation_invalid")

    def test_symlinked_or_hardlinked_generation_file_fails_closed(self) -> None:
        self.generation.chmod(0o700)
        real_manifest = self.generation / "manifest-real.json"
        os.replace(self.manifest_path, real_manifest)
        self.manifest_path.symlink_to(real_manifest.name)
        self.generation.chmod(0o500)
        self.assert_release_error("generation_invalid")

        self.generation.chmod(0o700)
        self.manifest_path.unlink()
        os.replace(real_manifest, self.manifest_path)
        hardlink = self.root / "manifest-hardlink.json"
        os.link(self.manifest_path, hardlink)
        self.generation.chmod(0o500)
        self.assert_release_error("generation_invalid")

    def test_duplicate_noncanonical_bom_or_malformed_manifest_fails_closed(
        self,
    ) -> None:
        schema = self.manifest["schemaVersion"]
        invalid = (
            self.original_manifest_bytes[:-1]
            + b',"schemaVersion":"'
            + schema.encode("ascii")
            + b'"}',
            self.original_manifest_bytes + b"\n",
            b"\xef\xbb\xbf" + self.original_manifest_bytes,
            b"{",
        )
        for raw in invalid:
            with self.subTest(raw=raw[:24]):
                self._replace_manifest_raw_and_repoint(raw)
                self.assert_release_error("manifest_invalid")

    def test_unknown_nested_manifest_field_fails_closed(self) -> None:
        self.manifest["items"][0]["holdoutReceipt"]["private-child"] = True
        self._write_manifest_and_repoint(recompute_logical=True)
        self.assert_release_error("manifest_invalid")

    def test_manifest_or_database_byte_tamper_fails_closed(self) -> None:
        self.generation.chmod(0o700)
        self.manifest_path.chmod(0o600)
        with self.manifest_path.open("r+b") as stream:
            stream.seek(0)
            stream.write(b"X")
        self.manifest_path.chmod(0o400)
        self.generation.chmod(0o500)
        self.assert_release_error("manifest_invalid")

        self._replace_manifest_raw_and_repoint(self.original_manifest_bytes)
        self.database.chmod(0o600)
        with self.database.open("r+b") as stream:
            stream.seek(-1, os.SEEK_END)
            original = stream.read(1)
            stream.seek(-1, os.SEEK_END)
            stream.write(bytes([original[0] ^ 1]))
        self.database.chmod(0o400)
        self.assert_release_error("database_invalid")

    def test_wrong_runtime_receipt_fails_closed(self) -> None:
        self.manifest["runtime"]["modelSha256"] = "f" * 64
        self._write_manifest_and_repoint(recompute_logical=True)
        self.assert_release_error("runtime_receipt_mismatch")

    def test_wrong_per_item_cache_row_mapping_fails_closed(self) -> None:
        first, second = self.manifest["items"]
        first["rowSha256"], second["rowSha256"] = (
            second["rowSha256"],
            first["rowSha256"],
        )
        self._write_manifest_and_repoint(recompute_logical=True)
        self.assert_release_error("row_manifest_mismatch")

    def test_corrupt_database_row_fails_eager_validation(self) -> None:
        self.generation.chmod(0o700)
        self.database.chmod(0o600)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE reviewed_questions SET row_json = '{}' "
                "WHERE cache_content_sha256 = ("
                "SELECT cache_content_sha256 FROM reviewed_questions LIMIT 1)"
            )
            connection.commit()
        finally:
            connection.close()
        self.database.chmod(0o400)
        self.generation.chmod(0o500)
        database_bytes = self.database.read_bytes()
        self.manifest["database"]["sha256"] = _sha256(database_bytes)
        self.manifest["database"]["sizeBytes"] = len(database_bytes)
        self._write_manifest_and_repoint(recompute_logical=False)
        self.assert_release_error("database_invalid")

    def test_missing_extra_or_sqlite_sidecar_fails_closed(self) -> None:
        self.generation.chmod(0o700)
        self.manifest_path.unlink()
        self.generation.chmod(0o500)
        self.assert_release_error("generation_invalid")

        self.generation.chmod(0o700)
        self.manifest_path.write_bytes(self.original_manifest_bytes)
        self.manifest_path.chmod(0o400)
        for name in ("private-child", "reviewed_cache.sqlite3-wal"):
            with self.subTest(name=name):
                extra = self.generation / name
                extra.write_bytes(b"private-child")
                extra.chmod(0o400)
                self.generation.chmod(0o500)
                self.assert_release_error("generation_invalid")
                self.generation.chmod(0o700)
                extra.unlink()
        self.generation.chmod(0o500)

    def test_pointer_replacement_during_open_is_detected_and_cache_closed(
        self,
    ) -> None:
        real_open = ReviewedCache.open_learner_fd
        opened_cache: ReviewedCache | None = None

        def swap_pointer(*args, **kwargs):
            nonlocal opened_cache
            opened_cache = real_open(*args, **kwargs)
            replacement = self.release_root / "replacement-pointer.json"
            self._write_pointer_raw(self.pointer.read_bytes(), path=replacement)
            os.replace(replacement, self.pointer)
            return opened_cache

        with patch.object(
            release_module.ReviewedCache,
            "open_learner_fd",
            side_effect=swap_pointer,
        ):
            self.assert_release_error("pointer_changed")

        self.assertIsNotNone(opened_cache)
        assert opened_cache is not None
        with self.assertRaises(CacheModeError):
            opened_cache._ensure_open()

    def test_baseexception_during_eager_audit_closes_the_fd_backed_cache(
        self,
    ) -> None:
        real_open = ReviewedCache.open_learner_fd
        opened: list[ReviewedCache] = []

        def tracked_open(*args, **kwargs):
            cache = real_open(*args, **kwargs)
            opened.append(cache)
            return cache

        with (
            patch.object(
                release_module.ReviewedCache,
                "open_learner_fd",
                side_effect=tracked_open,
            ),
            patch.object(
                release_module.ReviewedCache,
                "_validate_row",
                side_effect=KeyboardInterrupt(),
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            ReviewedCacheRelease.open_current(
                self.release_root,
                compiler=self.verifier.compiler,
                model_manifest=self.verifier.manifest,
            )

        self.assertEqual(len(opened), 1)
        with self.assertRaises(CacheModeError):
            opened[0]._ensure_open()

    def test_context_body_error_is_not_masked_and_descriptors_still_close(
        self,
    ) -> None:
        release = ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        )
        real_cache_close = release.cache.close
        descriptors = _descriptor_numbers(release)
        close_calls = 0

        def one_shot_close_failure() -> None:
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("private close detail")
            real_cache_close()

        with patch.object(
            release.cache,
            "close",
            new=one_shot_close_failure,
        ):
            with self.assertRaisesRegex(ValueError, "body-marker"):
                with release:
                    raise ValueError("body-marker")

        self.assertEqual(close_calls, 2)
        self.assertTrue(release._closed)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_release_close_failure_retains_state_for_successful_retry(self) -> None:
        release = ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        )
        descriptors = _descriptor_numbers(release)
        real_cache_close = release.cache.close
        close_calls = 0

        def one_shot_close_failure() -> None:
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("private close detail")
            real_cache_close()

        with patch.object(release.cache, "close", new=one_shot_close_failure):
            with self.assertRaises(ReviewedCacheReleaseError) as caught:
                release.close()
            self.assertEqual(caught.exception.code, "release_close_failed")
            self.assertEqual(str(caught.exception), "release_close_failed")
            self.assertNotIn("private close detail", repr(caught.exception))
            self.assertFalse(release._closed)
            self.assertEqual(_descriptor_numbers(release), descriptors)
            for descriptor in descriptors:
                os.fstat(descriptor)

            release.close()

        self.assertEqual(close_calls, 2)
        self.assertTrue(release._closed)
        self.assertEqual(release._descriptors, ())
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_open_failure_cleanup_retries_one_shot_cache_close_failure(
        self,
    ) -> None:
        real_open = ReviewedCache.open_learner_fd
        real_close = ReviewedCache.close
        opened: list[ReviewedCache] = []
        close_calls = 0

        def open_then_swap_pointer(*args, **kwargs):
            cache = real_open(*args, **kwargs)
            opened.append(cache)
            replacement = self.release_root / "replacement-pointer.json"
            self._write_pointer_raw(self.pointer.read_bytes(), path=replacement)
            os.replace(replacement, self.pointer)
            return cache

        def one_shot_close_failure(cache: ReviewedCache) -> None:
            nonlocal close_calls
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("private close detail")
            real_close(cache)

        with (
            patch.object(
                release_module.ReviewedCache,
                "open_learner_fd",
                side_effect=open_then_swap_pointer,
            ),
            patch.object(
                release_module.ReviewedCache,
                "close",
                new=one_shot_close_failure,
            ),
        ):
            self.assert_release_error("pointer_changed")

        self.assertEqual(len(opened), 1)
        self.assertEqual(close_calls, 2)
        with self.assertRaises(CacheModeError):
            opened[0]._ensure_open()

    def test_generator_exit_from_close_is_re_raised_and_resources_remain(
        self,
    ) -> None:
        release = ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        )
        descriptors = _descriptor_numbers(release)
        with patch.object(
            release.cache,
            "close",
            side_effect=GeneratorExit(),
        ):
            with self.assertRaises(GeneratorExit):
                release.close()

        self.assertFalse(release._closed)
        self.assertEqual(_descriptor_numbers(release), descriptors)
        for descriptor in descriptors:
            os.fstat(descriptor)

        release.close()
        self.assertTrue(release._closed)

    def test_descriptor_close_interruptions_never_close_reused_foreign_fds(
        self,
    ) -> None:
        probe = ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        )
        descriptor_count = len(_descriptor_numbers(probe))
        probe.close()
        real_close = os.close

        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for interrupt_at in range(descriptor_count):
                with self.subTest(
                    exception=exception_type.__name__,
                    interrupt_at=interrupt_at,
                ):
                    release = ReviewedCacheRelease.open_current(
                        self.release_root,
                        compiler=self.verifier.compiler,
                        model_manifest=self.verifier.manifest,
                    )
                    original = _descriptor_numbers(release)
                    close_order = tuple(reversed(original))
                    calls: list[int] = []
                    exceptional = (
                        SystemExit(23)
                        if exception_type is SystemExit
                        else exception_type()
                    )

                    def interrupt_one_close(descriptor: int) -> None:
                        position = len(calls)
                        calls.append(descriptor)
                        if position == interrupt_at:
                            raise exceptional
                        real_close(descriptor)

                    unrelated: list[int] = []
                    try:
                        with patch.object(
                            release_module.os,
                            "close",
                            new=interrupt_one_close,
                        ):
                            with self.assertRaises(exception_type) as caught:
                                release.close()
                        self.assertIs(caught.exception, exceptional)
                        confirmed_closed = close_order[:interrupt_at]
                        expected_retained = tuple(
                            descriptor
                            for descriptor in original
                            if descriptor not in confirmed_closed
                        )
                        self.assertEqual(
                            _descriptor_numbers(release),
                            expected_retained,
                        )

                        unrelated = [
                            os.open(os.devnull, os.O_RDONLY)
                            for _descriptor in confirmed_closed
                        ]
                        unrelated_identities = {
                            descriptor: (
                                os.fstat(descriptor).st_dev,
                                os.fstat(descriptor).st_ino,
                            )
                            for descriptor in unrelated
                        }
                        release.close()
                        self.assertTrue(release._closed)
                        for descriptor, identity in unrelated_identities.items():
                            details = os.fstat(descriptor)
                            self.assertEqual(
                                (details.st_dev, details.st_ino),
                                identity,
                            )
                    finally:
                        try:
                            release.close()
                        except BaseException:
                            pass
                        for descriptor in unrelated:
                            try:
                                real_close(descriptor)
                            except OSError:
                                pass

    def test_open_failure_descriptor_cleanup_is_bounded_and_never_masks_primary(
        self,
    ) -> None:
        probe = ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        )
        descriptor_count = len(_descriptor_numbers(probe))
        probe.close()
        real_open = ReviewedCache.open_learner_fd
        real_close = os.close

        for exceptional in (KeyboardInterrupt(), SystemExit(29), GeneratorExit()):
            for interrupt_at in range(descriptor_count):
                with self.subTest(
                    cleanup_exception=type(exceptional).__name__,
                    interrupt_at=interrupt_at,
                ):
                    active = False
                    interrupted = False
                    calls: list[int] = []

                    def open_then_swap(*args, **kwargs):
                        nonlocal active
                        cache = real_open(*args, **kwargs)
                        replacement = (
                            self.release_root / "replacement-pointer.json"
                        )
                        self._write_pointer_raw(
                            self.pointer.read_bytes(),
                            path=replacement,
                        )
                        os.replace(replacement, self.pointer)
                        active = True
                        return cache

                    def interrupt_cleanup(descriptor: int) -> None:
                        nonlocal interrupted
                        if active:
                            position = len(calls)
                            calls.append(descriptor)
                            if position == interrupt_at and not interrupted:
                                interrupted = True
                                raise exceptional
                        real_close(descriptor)

                    with (
                        patch.object(
                            release_module.ReviewedCache,
                            "open_learner_fd",
                            side_effect=open_then_swap,
                        ),
                        patch.object(
                            release_module.os,
                            "close",
                            new=interrupt_cleanup,
                        ),
                    ):
                        try:
                            self.assert_release_error("pointer_changed")
                        except (KeyboardInterrupt, SystemExit, GeneratorExit):
                            self.fail("cleanup masked the primary validation error")

                    self.assertTrue(interrupted)
                    self.assertEqual(len(calls), descriptor_count + 1)
                    for descriptor in set(calls):
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)

        primary = GeneratorExit()
        cleanup_interrupted = False
        active = False
        calls = []

        def open_for_primary(*args, **kwargs):
            nonlocal active
            cache = real_open(*args, **kwargs)
            active = True
            return cache

        def interrupt_primary_cleanup(descriptor: int) -> None:
            nonlocal cleanup_interrupted
            if active:
                calls.append(descriptor)
                if not cleanup_interrupted:
                    cleanup_interrupted = True
                    raise KeyboardInterrupt()
            real_close(descriptor)

        try:
            with (
                patch.object(
                    release_module.ReviewedCache,
                    "open_learner_fd",
                    side_effect=open_for_primary,
                ),
                patch.object(
                    release_module.ReviewedCache,
                    "_validate_row",
                    side_effect=primary,
                ),
                patch.object(
                    release_module.os,
                    "close",
                    new=interrupt_primary_cleanup,
                ),
                self.assertRaises(GeneratorExit) as caught,
            ):
                ReviewedCacheRelease.open_current(
                    self.release_root,
                    compiler=self.verifier.compiler,
                    model_manifest=self.verifier.manifest,
                )
        except (KeyboardInterrupt, SystemExit):
            self.fail("cleanup masked the primary GeneratorExit")
        self.assertIs(caught.exception, primary)
        self.assertTrue(cleanup_interrupted)
        self.assertEqual(len(calls), descriptor_count + 1)
        for descriptor in set(calls):
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_every_open_to_fstat_interruption_cleans_exact_acquired_fds(
        self,
    ) -> None:
        real_open = os.open
        real_fstat = os.fstat
        real_close = os.close

        def acquisition_count() -> int:
            opened: list[int] = []

            def tracked_open(*args, **kwargs):
                descriptor = real_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            with patch.object(release_module.os, "open", new=tracked_open):
                with ReviewedCacheRelease.open_current(
                    self.release_root,
                    compiler=self.verifier.compiler,
                    model_manifest=self.verifier.manifest,
                ):
                    pass
            return len(opened)

        count = acquisition_count()
        self.assertGreater(count, 6)
        for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for interrupt_at in range(count):
                with self.subTest(
                    exception=exception_type.__name__,
                    interrupt_at=interrupt_at,
                ):
                    opened: list[int] = []
                    current_token: dict[int, int] = {}
                    first_fstat_tokens: set[int] = set()
                    first_fstat_order: list[int] = []
                    exceptional = (
                        SystemExit(31)
                        if exception_type is SystemExit
                        else exception_type()
                    )

                    def tracked_open(*args, **kwargs):
                        descriptor = real_open(*args, **kwargs)
                        token = len(opened)
                        opened.append(descriptor)
                        current_token[descriptor] = token
                        return descriptor

                    def interrupt_first_fstat(descriptor: int):
                        token = current_token.get(descriptor)
                        if token is not None and token not in first_fstat_tokens:
                            first_fstat_tokens.add(token)
                            position = len(first_fstat_order)
                            first_fstat_order.append(token)
                            if position == interrupt_at:
                                raise exceptional
                        return real_fstat(descriptor)

                    try:
                        with (
                            patch.object(
                                release_module.os,
                                "open",
                                new=tracked_open,
                            ),
                            patch.object(
                                release_module.os,
                                "fstat",
                                new=interrupt_first_fstat,
                            ),
                            self.assertRaises(exception_type) as caught,
                        ):
                            ReviewedCacheRelease.open_current(
                                self.release_root,
                                compiler=self.verifier.compiler,
                                model_manifest=self.verifier.manifest,
                            )
                        self.assertIs(caught.exception, exceptional)
                        self.assertEqual(len(first_fstat_order), interrupt_at + 1)
                        for descriptor in set(opened):
                            with self.assertRaises(OSError):
                                real_fstat(descriptor)
                    finally:
                        for descriptor in set(opened):
                            try:
                                real_close(descriptor)
                            except OSError:
                                pass

    def test_helper_return_interruptions_use_the_outer_ownership_ledger(
        self,
    ) -> None:
        real_fstat = os.fstat
        real_close = os.close
        cases = (
            ("_open_release_root", 0),
            ("_open_release_root", 1),
            ("_open_child_directory", 0),
            ("_open_child_directory", 1),
            ("_open_owned_file", 0),
            ("_open_owned_file", 1),
            ("_open_owned_file", 2),
        )
        for helper_name, interrupt_at in cases:
            for exception_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
                with self.subTest(
                    helper=helper_name,
                    interrupt_at=interrupt_at,
                    exception=exception_type.__name__,
                ):
                    real_helper = getattr(release_module, helper_name)
                    helper_calls = 0
                    captured: list[int] = []
                    exceptional = (
                        SystemExit(37)
                        if exception_type is SystemExit
                        else exception_type()
                    )

                    def interrupt_after_return(*args, **kwargs):
                        nonlocal helper_calls
                        result = real_helper(*args, **kwargs)
                        position = helper_calls
                        helper_calls += 1
                        if position == interrupt_at:
                            captured.append(
                                getattr(result[0], "descriptor", result[0])
                            )
                            raise exceptional
                        return result

                    try:
                        with (
                            patch.object(
                                release_module,
                                helper_name,
                                new=interrupt_after_return,
                            ),
                            self.assertRaises(exception_type) as caught,
                        ):
                            ReviewedCacheRelease.open_current(
                                self.release_root,
                                compiler=self.verifier.compiler,
                                model_manifest=self.verifier.manifest,
                            )
                        self.assertIs(caught.exception, exceptional)
                        self.assertEqual(len(captured), 1)
                        with self.assertRaises(OSError):
                            real_fstat(captured[0])
                    finally:
                        for descriptor in captured:
                            try:
                                real_close(descriptor)
                            except OSError:
                                pass

    def test_acquisition_primary_survives_cleanup_identity_interruptions(
        self,
    ) -> None:
        real_open = os.open
        real_fstat = os.fstat
        real_close = os.close
        for cleanup_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(cleanup_exception=cleanup_type.__name__):
                opened: list[int] = []
                target: int | None = None
                target_fstats = 0
                primary = GeneratorExit()
                cleanup_exception = (
                    SystemExit(41)
                    if cleanup_type is SystemExit
                    else cleanup_type()
                )

                def tracked_open(*args, **kwargs):
                    nonlocal target
                    descriptor = real_open(*args, **kwargs)
                    opened.append(descriptor)
                    if target is None:
                        target = descriptor
                    return descriptor

                def interrupt_acquire_and_cleanup(descriptor: int):
                    nonlocal target_fstats
                    if descriptor == target:
                        target_fstats += 1
                        if target_fstats == 1:
                            raise primary
                        if target_fstats == 2:
                            raise cleanup_exception
                    return real_fstat(descriptor)

                try:
                    try:
                        with (
                            patch.object(
                                release_module.os,
                                "open",
                                new=tracked_open,
                            ),
                            patch.object(
                                release_module.os,
                                "fstat",
                                new=interrupt_acquire_and_cleanup,
                            ),
                            self.assertRaises(GeneratorExit) as caught,
                        ):
                            ReviewedCacheRelease.open_current(
                                self.release_root,
                                compiler=self.verifier.compiler,
                                model_manifest=self.verifier.manifest,
                            )
                    except (KeyboardInterrupt, SystemExit):
                        self.fail("cleanup identity check masked the primary")
                    self.assertIs(caught.exception, primary)
                    self.assertGreaterEqual(target_fstats, 3)
                    for descriptor in set(opened):
                        with self.assertRaises(OSError):
                            real_fstat(descriptor)
                finally:
                    for descriptor in set(opened):
                        try:
                            real_close(descriptor)
                        except OSError:
                            pass


if __name__ == "__main__":
    unittest.main()
