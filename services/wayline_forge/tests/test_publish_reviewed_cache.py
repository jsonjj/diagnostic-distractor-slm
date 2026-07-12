from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.reviewed_cache_release import (
    ReviewedCacheRelease,
    ReviewedCacheReleaseError,
)
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle
import services.wayline_forge.scripts.publish_reviewed_cache as publish_module
from services.wayline_forge.scripts.build_reviewed_cache import (
    BUILD_APPROVAL_SCHEMA_VERSION,
    BUILD_INPUT_SCHEMA_VERSION,
    build_reviewed_cache,
)
from services.wayline_forge.scripts.publish_reviewed_cache import (
    CachePublicationError,
    PublishedCacheGeneration,
    publish_reviewed_cache,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class ReviewedCachePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = DistractorVerifier.for_tests()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.input_path = self.root / "build-input.json"
        self.release_root = self.root / "release"
        self.provision(self.release_root)

    @staticmethod
    def provision(root: Path) -> None:
        root.mkdir(mode=0o700)
        (root / "generations").mkdir(mode=0o700)
        lock = root / ".publish.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)

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
        approval_hash = hashlib.sha256(
            _canonical_json(unsigned).encode("utf-8")
        ).hexdigest()
        return unsigned | {"approvalRecordSha256": approval_hash}

    @staticmethod
    def input_item(bundle: VerifiedQuestionBundle) -> dict[str, object]:
        return {
            "approval": ReviewedCachePublicationTests.approval(bundle),
            "bundle": json.loads(bundle.to_private_json()),
        }

    def write_input(self, seed: int, *, path: Path | None = None) -> None:
        target = path or self.input_path
        bundle = self.bundle(seed)
        target.write_text(
            _canonical_json(
                {
                    "items": [self.input_item(bundle)],
                    "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
                }
            ),
            encoding="utf-8",
        )
        target.chmod(0o600)

    def publish(
        self,
        *,
        input_path: Path | None = None,
        release_root: Path | None = None,
    ) -> PublishedCacheGeneration:
        return publish_reviewed_cache(
            input_path or self.input_path,
            release_root or self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        )

    @staticmethod
    def current_generation(root: Path) -> str:
        return json.loads((root / "current.json").read_text("utf-8"))[
            "generationId"
        ]

    def assert_publication_error(
        self,
        code: str,
        *,
        input_path: Path | None = None,
        release_root: Path | None = None,
    ) -> CachePublicationError:
        with self.assertRaises(CachePublicationError) as caught:
            self.publish(input_path=input_path, release_root=release_root)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertIsNone(caught.exception.__cause__)
        return caught.exception

    def tracked_boundaries(self, events: list[str]) -> ExitStack:
        stack = ExitStack()
        original_fchmod = publish_module.os.fchmod

        def tracked_fchmod(descriptor: int, mode: int) -> None:
            if mode == 0o500:
                events.append("staging-directory-chmod")
            original_fchmod(descriptor, mode)

        stack.enter_context(
            patch.object(publish_module.os, "fchmod", new=tracked_fchmod)
        )
        for attribute, label in (
            ("_fsync_database", "database-fsync"),
            ("_fsync_manifest", "manifest-fsync"),
            ("_fsync_staging_directory", "staging-directory-fsync"),
            ("_rename_staging_generation", "generation-rename"),
            ("_fsync_generations_directory", "generation-parent-fsync"),
            ("_fsync_pointer", "pointer-fsync"),
            ("_replace_pointer", "pointer-replace"),
            ("_fsync_release_root", "release-root-fsync"),
            ("_runtime_reopen", "runtime-reopen"),
        ):
            original = getattr(publish_module, attribute)

            def tracked(*args, _original=original, _label=label, **kwargs):
                events.append(_label)
                return _original(*args, **kwargs)

            stack.enter_context(
                patch.object(publish_module, attribute, new=tracked)
            )
        return stack

    def descriptor_fault_stack(
        self,
        target: str,
        failure_factory,
        *,
        reuse_numeric_descriptor: bool,
        case_name: str,
    ) -> tuple[ExitStack, dict[str, object]]:
        """Instrument publisher-owned descriptors and fault one lifecycle call."""

        stack = ExitStack()
        original_close = publish_module.os.close
        original_open = publish_module.os.open
        original_flock = publish_module.fcntl.flock
        foreign_path = self.root / f"foreign-{case_name}.txt"
        foreign_path.write_bytes(b"foreign descriptor must survive")
        state: dict[str, object] = {
            "active": {},
            "foreign_descriptor": None,
            "injected": False,
            "records": [],
            "unlock_calls": 0,
        }

        def remember(role: str, descriptor: int) -> None:
            details = os.fstat(descriptor)
            state["active"][role] = descriptor
            state["records"].append(
                (
                    role,
                    descriptor,
                    details.st_dev,
                    details.st_ino,
                    stat.S_IFMT(details.st_mode),
                    details.st_uid,
                )
            )

        for attribute, role in (
            ("_open_absolute_directory", "root"),
            ("_open_directory_at", "generations"),
            ("_open_lock", "lock"),
            ("_create_stage", "stage"),
            ("_open_stage_file", "database"),
            ("_create_pointer", "candidate"),
        ):
            original = getattr(publish_module, attribute)

            def tracked(*args, _original=original, _role=role, **kwargs):
                result = _original(*args, **kwargs)
                descriptor_index = 2 if _role == "stage" else 1 if _role == "candidate" else 0
                remember(_role, result[descriptor_index])
                return result

            stack.enter_context(
                patch.object(publish_module, attribute, new=tracked)
            )

        def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
            if path == publish_module._MANIFEST_NAME and flags & os.O_CREAT:
                remember("manifest", descriptor)
            return descriptor

        stack.enter_context(
            patch.object(publish_module.os, "open", new=tracked_open)
        )

        def inject(descriptor: int) -> None:
            state["injected"] = True
            if reuse_numeric_descriptor:
                original_close(descriptor)
                foreign_descriptor = original_open(foreign_path, os.O_RDONLY)
                if foreign_descriptor != descriptor:
                    os.dup2(foreign_descriptor, descriptor)
                    original_close(foreign_descriptor)
                    foreign_descriptor = descriptor
                state["foreign_descriptor"] = foreign_descriptor
            raise failure_factory()

        def tracked_close(descriptor: int) -> None:
            active = state["active"]
            if (
                not state["injected"]
                and target != "lock-unlock"
                and active.get(target) == descriptor
            ):
                inject(descriptor)
            original_close(descriptor)

        stack.enter_context(
            patch.object(publish_module.os, "close", new=tracked_close)
        )

        def tracked_flock(descriptor: int, operation: int) -> None:
            if operation == fcntl.LOCK_UN and state["active"].get("lock") == descriptor:
                state["unlock_calls"] += 1
                if not state["injected"] and target == "lock-unlock":
                    state["injected"] = True
                    raise failure_factory()
            original_flock(descriptor, operation)

        stack.enter_context(
            patch.object(publish_module.fcntl, "flock", new=tracked_flock)
        )
        return stack, state

    def assert_owned_descriptors_released(self, state: dict[str, object]) -> None:
        self.assertTrue(state["injected"])
        for role, descriptor, device, inode, file_type, owner in state["records"]:
            try:
                details = os.fstat(descriptor)
            except OSError as error:
                self.assertEqual(error.errno, errno.EBADF, role)
                continue
            self.assertNotEqual(
                (
                    details.st_dev,
                    details.st_ino,
                    stat.S_IFMT(details.st_mode),
                    details.st_uid,
                ),
                (device, inode, file_type, owner),
                role,
            )

    @staticmethod
    def descriptor_matches_record(record: tuple[int, int, int, int, int]) -> bool:
        descriptor, device, inode, file_type, owner = record
        try:
            details = os.fstat(descriptor)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
            return False
        return (
            details.st_dev,
            details.st_ino,
            stat.S_IFMT(details.st_mode),
            details.st_uid,
        ) == (device, inode, file_type, owner)

    @staticmethod
    def descriptor_record(descriptor: int) -> tuple[int, int, int, int, int]:
        details = os.fstat(descriptor)
        return (
            descriptor,
            details.st_dev,
            details.st_ino,
            stat.S_IFMT(details.st_mode),
            details.st_uid,
        )

    def assert_foreign_descriptor_survives(self, state: dict[str, object]) -> None:
        descriptor = state["foreign_descriptor"]
        self.assertIsInstance(descriptor, int)
        assert isinstance(descriptor, int)
        try:
            self.assertEqual(
                os.read(descriptor, 64),
                b"foreign descriptor must survive",
            )
        finally:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise

    def isolated_publication_case(
        self,
        label: str,
        seed: int,
    ) -> tuple[Path, Path]:
        case = self.root / label
        case.mkdir(mode=0o700)
        release_root = case / "release"
        self.provision(release_root)
        input_path = case / "input.json"
        self.write_input(seed, path=input_path)
        return input_path, release_root

    def test_publish_orders_durable_generation_before_pointer_switch(self) -> None:
        self.write_input(9501)
        events: list[str] = []

        with self.tracked_boundaries(events):
            result = self.publish()

        self.assertEqual(
            events,
            [
                "database-fsync",
                "manifest-fsync",
                "staging-directory-chmod",
                "staging-directory-fsync",
                "generation-rename",
                "generation-parent-fsync",
                "pointer-fsync",
                "pointer-replace",
                "release-root-fsync",
                "runtime-reopen",
            ],
        )
        self.assertEqual(result.generation_id, "generation-" + result.manifest_sha256)
        pointer = (self.release_root / "current.json").read_bytes()
        self.assertEqual(hashlib.sha256(pointer).hexdigest(), result.pointer_sha256)
        generation = self.release_root / "generations" / result.generation_id
        self.assertEqual(stat.S_IMODE(generation.stat().st_mode), 0o500)
        self.assertEqual(
            {child.name for child in generation.iterdir()},
            {"reviewed_cache.sqlite3", "reviewed_cache_manifest.json"},
        )
        for child in generation.iterdir():
            self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o400)
        with ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        ) as opened:
            self.assertEqual(opened.generation_id, result.generation_id)

    def test_pre_switch_close_fault_matrix_preserves_failure_and_cleans_up(self) -> None:
        failure_cases = (
            ("oserror", lambda: OSError("private close failure")),
            ("keyboard", lambda: KeyboardInterrupt("private close interrupt")),
            ("system-exit", lambda: SystemExit(73)),
            ("generator-exit", lambda: GeneratorExit("private close exit")),
        )
        scenarios = [
            (name, factory, False)
            for name, factory in failure_cases
        ] + [("oserror-reused", failure_cases[0][1], True)]
        index = 0
        for target in ("database", "manifest", "candidate"):
            for failure_name, failure_factory, reuse in scenarios:
                with self.subTest(target=target, failure=failure_name):
                    case_index = index
                    index += 1
                    input_path, release_root = self.isolated_publication_case(
                        f"pre-close-{case_index}",
                        9700 + case_index,
                    )
                    stack, state = self.descriptor_fault_stack(
                        target,
                        failure_factory,
                        reuse_numeric_descriptor=reuse,
                        case_name=f"pre-{case_index}",
                    )
                    failure: BaseException | None = None
                    with stack:
                        try:
                            self.publish(
                                input_path=input_path,
                                release_root=release_root,
                            )
                        except BaseException as error:
                            failure = error
                    self.assertIsNotNone(failure)
                    assert failure is not None
                    if failure_name.startswith("oserror"):
                        self.assertIsInstance(failure, CachePublicationError)
                        self.assertEqual(failure.code, "publication_failed")
                    else:
                        expected_type = type(failure_factory())
                        self.assertIs(type(failure), expected_type)
                    self.assertIsNone(failure.__cause__)
                    self.assert_owned_descriptors_released(state)
                    if reuse:
                        self.assert_foreign_descriptor_survives(state)
                    self.assertFalse((release_root / "current.json").exists())
                    self.assertEqual(list(release_root.glob("candidate-*.json")), [])
                    self.assertEqual(
                        [
                            path
                            for path in (release_root / "generations").iterdir()
                            if path.name.startswith(".stage-")
                        ],
                        [],
                    )

    def test_post_switch_cleanup_fault_matrix_is_stable_and_exhaustive(self) -> None:
        failure_cases = (
            ("oserror", lambda: OSError("private cleanup failure")),
            ("keyboard", lambda: KeyboardInterrupt("private cleanup interrupt")),
            ("system-exit", lambda: SystemExit(79)),
            ("generator-exit", lambda: GeneratorExit("private cleanup exit")),
        )
        close_targets = ("stage", "lock", "generations", "root")
        scenarios = [
            (name, factory, False)
            for name, factory in failure_cases
        ] + [("oserror-reused", failure_cases[0][1], True)]
        index = 0
        for target in close_targets:
            for failure_name, failure_factory, reuse in scenarios:
                with self.subTest(target=target, failure=failure_name):
                    case_index = index
                    index += 1
                    input_path, release_root = self.isolated_publication_case(
                        f"post-close-{case_index}",
                        9750 + case_index,
                    )
                    stack, state = self.descriptor_fault_stack(
                        target,
                        failure_factory,
                        reuse_numeric_descriptor=reuse,
                        case_name=f"post-{case_index}",
                    )
                    failure: BaseException | None = None
                    with stack:
                        try:
                            self.publish(
                                input_path=input_path,
                                release_root=release_root,
                            )
                        except BaseException as error:
                            failure = error
                    self.assertIsNotNone(failure)
                    assert failure is not None
                    if failure_name.startswith("oserror"):
                        self.assertIsInstance(failure, CachePublicationError)
                        self.assertEqual(failure.code, "publication_cleanup_failed")
                        self.assertIsNone(failure.__cause__)
                    else:
                        expected_type = type(failure_factory())
                        self.assertIs(type(failure), expected_type)
                        self.assertIsInstance(
                            failure.__cause__,
                            CachePublicationError,
                        )
                        self.assertEqual(
                            failure.__cause__.code,
                            "publication_cleanup_failed",
                        )
                    self.assert_owned_descriptors_released(state)
                    if reuse:
                        self.assert_foreign_descriptor_survives(state)
                    with ReviewedCacheRelease.open_current(
                        release_root,
                        compiler=self.verifier.compiler,
                        model_manifest=self.verifier.manifest,
                    ) as opened:
                        self.assertEqual(
                            opened.generation_id,
                            self.current_generation(release_root),
                        )

        for failure_name, failure_factory in failure_cases:
            with self.subTest(target="lock-unlock", failure=failure_name):
                case_index = index
                index += 1
                input_path, release_root = self.isolated_publication_case(
                    f"post-unlock-{case_index}",
                    9750 + case_index,
                )
                stack, state = self.descriptor_fault_stack(
                    "lock-unlock",
                    failure_factory,
                    reuse_numeric_descriptor=False,
                    case_name=f"unlock-{case_index}",
                )
                failure: BaseException | None = None
                with stack:
                    try:
                        self.publish(
                            input_path=input_path,
                            release_root=release_root,
                        )
                    except BaseException as error:
                        failure = error
                self.assertIsNotNone(failure)
                assert failure is not None
                if failure_name == "oserror":
                    self.assertIsInstance(failure, CachePublicationError)
                    self.assertEqual(failure.code, "publication_cleanup_failed")
                else:
                    self.assertIs(type(failure), type(failure_factory()))
                    self.assertIsInstance(failure.__cause__, CachePublicationError)
                    self.assertEqual(
                        failure.__cause__.code,
                        "publication_cleanup_failed",
                    )
                self.assertGreaterEqual(state["unlock_calls"], 1)
                self.assert_owned_descriptors_released(state)
                with ReviewedCacheRelease.open_current(
                    release_root,
                    compiler=self.verifier.compiler,
                    model_manifest=self.verifier.manifest,
                ) as opened:
                    self.assertEqual(
                        opened.generation_id,
                        self.current_generation(release_root),
                    )

    def test_cleanup_fault_never_masks_post_switch_primary_baseexception(self) -> None:
        index = 0
        for target in ("stage", "lock-unlock", "lock", "generations", "root"):
            with self.subTest(target=target):
                case_index = index
                index += 1
                input_path, release_root = self.isolated_publication_case(
                    f"primary-cleanup-{case_index}",
                    9790 + case_index,
                )
                primary = KeyboardInterrupt(f"primary-{target}")
                stack, state = self.descriptor_fault_stack(
                    target,
                    lambda: OSError("secondary cleanup failure"),
                    reuse_numeric_descriptor=False,
                    case_name=f"primary-{case_index}",
                )
                failure: BaseException | None = None
                with stack, patch.object(
                    publish_module,
                    "_runtime_reopen",
                    side_effect=primary,
                ):
                    try:
                        self.publish(
                            input_path=input_path,
                            release_root=release_root,
                        )
                    except BaseException as error:
                        failure = error
                self.assertIs(failure, primary)
                self.assertIsInstance(primary.__cause__, CachePublicationError)
                self.assertEqual(
                    primary.__cause__.code,
                    "published_release_unavailable",
                )
                self.assert_owned_descriptors_released(state)
                with ReviewedCacheRelease.open_current(
                    release_root,
                    compiler=self.verifier.compiler,
                    model_manifest=self.verifier.manifest,
                ) as opened:
                    self.assertEqual(
                        opened.generation_id,
                        self.current_generation(release_root),
                    )

    def test_every_pre_switch_boundary_preserves_current_pointer(self) -> None:
        boundaries = (
            "_fsync_database",
            "_fsync_manifest",
            "_fsync_staging_directory",
            "_rename_staging_generation",
            "_fsync_generations_directory",
            "_validate_candidate_pointer",
            "_fsync_pointer",
            "_replace_pointer",
        )
        for index, boundary in enumerate(boundaries):
            with self.subTest(boundary=boundary):
                case = self.root / f"case-{index}"
                case.mkdir(mode=0o700)
                release_root = case / "release"
                self.provision(release_root)
                input_path = case / "input.json"
                self.write_input(9510 + index * 2, path=input_path)
                first = self.publish(
                    input_path=input_path,
                    release_root=release_root,
                )
                pointer_before = (release_root / "current.json").read_bytes()
                self.write_input(9511 + index * 2, path=input_path)
                with patch.object(
                    publish_module,
                    boundary,
                    side_effect=OSError("private boundary failure"),
                ):
                    self.assert_publication_error(
                        "publication_failed",
                        input_path=input_path,
                        release_root=release_root,
                    )
                self.assertEqual(
                    (release_root / "current.json").read_bytes(),
                    pointer_before,
                )
                with ReviewedCacheRelease.open_current(
                    release_root,
                    compiler=self.verifier.compiler,
                    model_manifest=self.verifier.manifest,
                ) as opened:
                    self.assertEqual(opened.generation_id, first.generation_id)
                self.assertEqual(
                    [p for p in (release_root / "generations").iterdir()
                     if p.name.startswith(".stage-")],
                    [],
                )
                self.assertEqual(list(release_root.glob("candidate-*.json")), [])

    def test_pointer_fsync_failure_after_switch_is_durability_uncertain(self) -> None:
        self.write_input(9540)
        with patch.object(
            publish_module,
            "_fsync_release_root",
            side_effect=OSError("private"),
        ):
            self.assert_publication_error("pointer_durability_uncertain")

        generation_id = self.current_generation(self.release_root)
        with ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        ) as opened:
            self.assertEqual(opened.generation_id, generation_id)

    def test_post_switch_reopen_failure_keeps_new_pointer_installed(self) -> None:
        self.write_input(9541)
        with patch.object(
            publish_module,
            "_runtime_reopen",
            side_effect=ReviewedCacheReleaseError("private"),
        ):
            self.assert_publication_error("published_release_unavailable")

        generation_id = self.current_generation(self.release_root)
        with ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        ) as opened:
            self.assertEqual(opened.generation_id, generation_id)

    def test_existing_generation_is_idempotent_only_after_exact_validation(self) -> None:
        self.write_input(9542)
        first = self.publish()
        generation = self.release_root / "generations" / first.generation_id
        identities = {
            child.name: (child.stat().st_ino, child.stat().st_mtime_ns)
            for child in generation.iterdir()
        }

        second = self.publish()

        self.assertEqual(first, second)
        self.assertEqual(
            [path.name for path in (self.release_root / "generations").iterdir()],
            [first.generation_id],
        )
        self.assertEqual(
            {
                child.name: (child.stat().st_ino, child.stat().st_mtime_ns)
                for child in generation.iterdir()
            },
            identities,
        )

        self.write_input(9549)
        collision_database = self.root / "collision-build.sqlite3"
        collision_result = build_reviewed_cache(
            self.input_path,
            collision_database,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        collision_generation = (
            self.release_root
            / "generations"
            / ("generation-" + collision_result.manifest_sha256)
        )
        collision_generation.mkdir(mode=0o700)
        collision_files = {
            "reviewed_cache.sqlite3": b"foreign database collision",
            "reviewed_cache_manifest.json": b"foreign manifest collision",
        }
        for name, raw in collision_files.items():
            target = collision_generation / name
            target.write_bytes(raw)
            target.chmod(0o400)
        collision_generation.chmod(0o500)
        pointer_before = (self.release_root / "current.json").read_bytes()

        self.assert_publication_error("publication_failed")

        for name, raw in collision_files.items():
            self.assertEqual((collision_generation / name).read_bytes(), raw)
        self.assertEqual((self.release_root / "current.json").read_bytes(), pointer_before)

    def test_process_contention_is_nonblocking_and_uses_lock_name(self) -> None:
        self.write_input(9543)
        lock_descriptor = os.open(self.release_root / ".publish.lock", os.O_RDWR)
        self.addCleanup(os.close, lock_descriptor)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = (
            "from services.wayline_forge.app.distractor_verifier import "
            "DistractorVerifier; "
            "from services.wayline_forge.scripts.publish_reviewed_cache import "
            "publish_reviewed_cache, CachePublicationError; "
            "import sys; v=DistractorVerifier.for_tests(); "
            "\ntry: publish_reviewed_cache(sys.argv[1], sys.argv[2], "
            "compiler=v.compiler, model_manifest=v.manifest)\n"
            "except CachePublicationError as e: print(e.code)\n"
        )

        completed = subprocess.run(
            [sys.executable, "-c", code, str(self.input_path), str(self.release_root)],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )

        self.assertEqual(completed.stdout.strip(), "publication_busy")
        self.assertFalse((self.release_root / "current.json").exists())

    def test_lock_acquisition_baseexceptions_release_lock_and_descriptor(self) -> None:
        failure_cases = (
            lambda: KeyboardInterrupt("lock attestation interrupt"),
            lambda: SystemExit(83),
            lambda: GeneratorExit("lock attestation exit"),
        )
        original_open = os.open
        original_close = os.close
        original_flock = fcntl.flock
        for failure_factory in failure_cases:
            primary = failure_factory()
            with self.subTest(failure=type(primary).__name__):
                root_descriptor = original_open(
                    self.release_root,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                root_device = os.fstat(root_descriptor).st_dev
                captured: list[tuple[int, int, int, int, int]] = []
                ownership_records = []

                def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
                    descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
                    if path == publish_module._LOCK_NAME:
                        captured.append(self.descriptor_record(descriptor))
                    return descriptor

                failure: BaseException | None = None
                try:
                    with patch.object(
                        publish_module.os,
                        "open",
                        new=tracked_open,
                    ), patch.object(
                        publish_module,
                        "_require_name_identity",
                        side_effect=primary,
                    ):
                        try:
                            publish_module._open_lock(
                                root_descriptor,
                                device=root_device,
                                records=ownership_records,
                            )
                        except BaseException as error:
                            failure = error
                    self.assertEqual(len(captured), 1)
                    released = not self.descriptor_matches_record(captured[0])
                    contender = original_open(
                        self.release_root / ".publish.lock",
                        os.O_RDWR,
                    )
                    contender_acquired = False
                    try:
                        try:
                            original_flock(
                                contender,
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                        except OSError:
                            pass
                        else:
                            contender_acquired = True
                            original_flock(contender, fcntl.LOCK_UN)
                    finally:
                        original_close(contender)
                finally:
                    for record in captured:
                        if self.descriptor_matches_record(record):
                            original_close(record[0])
                    original_close(root_descriptor)

                self.assertIs(failure, primary)
                self.assertIsNone(primary.__cause__)
                self.assertTrue(released)
                self.assertTrue(contender_acquired)

    def test_preledger_acquisition_helpers_preserve_baseexceptions_without_leaks(self) -> None:
        failure_cases = (
            lambda: KeyboardInterrupt("acquisition interrupt"),
            lambda: SystemExit(89),
            lambda: GeneratorExit("acquisition exit"),
        )
        helper_names = (
            "root",
            "generations",
            "stage-directory",
            "stage-file",
            "candidate-pointer",
        )
        original_open = os.open
        original_close = os.close
        index = 0
        for helper_name in helper_names:
            for failure_factory in failure_cases:
                case_index = index
                index += 1
                primary = failure_factory()
                with self.subTest(
                    helper=helper_name,
                    failure=type(primary).__name__,
                ):
                    case = self.root / f"acquisition-{case_index}"
                    case.mkdir(mode=0o700)
                    release_root = case / "release"
                    self.provision(release_root)
                    captured: list[tuple[int, int, int, int, int]] = []
                    parent_descriptors: list[int] = []
                    ownership_records = []

                    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
                        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
                        captured.append(self.descriptor_record(descriptor))
                        return descriptor

                    failure: BaseException | None = None
                    with ExitStack() as stack:
                        stack.enter_context(
                            patch.object(
                                publish_module.os,
                                "open",
                                new=tracked_open,
                            )
                        )
                        if helper_name == "candidate-pointer":
                            stack.enter_context(
                                patch.object(
                                    publish_module,
                                    "_fsync_pointer",
                                    side_effect=primary,
                                )
                            )
                        else:
                            stack.enter_context(
                                patch.object(
                                    publish_module.os,
                                    "getuid",
                                    side_effect=primary,
                                )
                            )
                        try:
                            if helper_name == "root":
                                publish_module._open_absolute_directory(
                                    release_root,
                                    records=ownership_records,
                                )
                            elif helper_name == "generations":
                                parent = original_open(
                                    release_root,
                                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                                )
                                parent_descriptors.append(parent)
                                publish_module._open_directory_at(
                                    parent,
                                    publish_module._GENERATIONS_NAME,
                                    device=os.fstat(parent).st_dev,
                                    records=ownership_records,
                                )
                            elif helper_name == "stage-directory":
                                parent = original_open(
                                    release_root / "generations",
                                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                                )
                                parent_descriptors.append(parent)
                                publish_module._create_stage(
                                    parent,
                                    device=os.fstat(parent).st_dev,
                                    generations_path=release_root / "generations",
                                    records=ownership_records,
                                )
                            elif helper_name == "stage-file":
                                stage = release_root / "generations" / "stage-file"
                                stage.mkdir(mode=0o700)
                                database = stage / publish_module._DATABASE_NAME
                                database.write_bytes(b"database")
                                database.chmod(0o400)
                                parent = original_open(
                                    stage,
                                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                                )
                                parent_descriptors.append(parent)
                                publish_module._open_stage_file(
                                    parent,
                                    publish_module._DATABASE_NAME,
                                    device=os.fstat(parent).st_dev,
                                    expected_mode=0o400,
                                    records=ownership_records,
                                )
                            else:
                                parent = original_open(
                                    release_root,
                                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                                )
                                parent_descriptors.append(parent)
                                publish_module._create_pointer(
                                    parent,
                                    b"{}",
                                    device=os.fstat(parent).st_dev,
                                    records=ownership_records,
                                )
                        except BaseException as error:
                            failure = error

                    all_released = all(
                        not self.descriptor_matches_record(record)
                        for record in captured
                    )
                    stage_leaves = list(
                        (release_root / "generations").glob(".stage-*")
                    )
                    candidate_leaves = list(
                        release_root.glob("candidate-*.json")
                    )
                    for record in reversed(captured):
                        if self.descriptor_matches_record(record):
                            original_close(record[0])
                    for descriptor in parent_descriptors:
                        original_close(descriptor)
                    for leaf in stage_leaves:
                        leaf.rmdir()
                    for leaf in candidate_leaves:
                        leaf.unlink()

                    self.assertIs(failure, primary)
                    self.assertIsNone(primary.__cause__)
                    self.assertTrue(all_released)
                    self.assertEqual(stage_leaves, [])
                    self.assertEqual(candidate_leaves, [])

    def test_lock_acquisition_cleanup_fault_preserves_primary_with_stable_cause(self) -> None:
        original_open = os.open
        original_close = os.close
        original_flock = fcntl.flock
        for cleanup_target in ("unlock", "close", "close-reused"):
            primary = KeyboardInterrupt(f"primary-{cleanup_target}")
            with self.subTest(cleanup=cleanup_target):
                root_descriptor = original_open(
                    self.release_root,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                root_device = os.fstat(root_descriptor).st_dev
                captured: list[tuple[int, int, int, int, int]] = []
                ownership_records = []
                injected = False
                foreign_descriptor: int | None = None
                foreign_path = self.root / f"foreign-lock-{cleanup_target}"
                foreign_path.write_bytes(b"foreign lock fd")

                def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
                    descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
                    if path == publish_module._LOCK_NAME:
                        captured.append(self.descriptor_record(descriptor))
                    return descriptor

                def tracked_flock(descriptor: int, operation: int) -> None:
                    nonlocal injected
                    if (
                        cleanup_target == "unlock"
                        and operation == fcntl.LOCK_UN
                        and captured
                        and descriptor == captured[0][0]
                        and not injected
                    ):
                        injected = True
                        raise OSError("private unlock cleanup failure")
                    original_flock(descriptor, operation)

                def tracked_close(descriptor: int) -> None:
                    nonlocal injected, foreign_descriptor
                    if (
                        cleanup_target.startswith("close")
                        and captured
                        and descriptor == captured[0][0]
                        and not injected
                    ):
                        injected = True
                        if cleanup_target == "close-reused":
                            original_close(descriptor)
                            opened = original_open(foreign_path, os.O_RDONLY)
                            if opened != descriptor:
                                os.dup2(opened, descriptor)
                                original_close(opened)
                                opened = descriptor
                            foreign_descriptor = opened
                        raise OSError("private close cleanup failure")
                    original_close(descriptor)

                failure: BaseException | None = None
                try:
                    with patch.object(
                        publish_module.os,
                        "open",
                        new=tracked_open,
                    ), patch.object(
                        publish_module.os,
                        "close",
                        new=tracked_close,
                    ), patch.object(
                        publish_module.fcntl,
                        "flock",
                        new=tracked_flock,
                    ), patch.object(
                        publish_module,
                        "_require_name_identity",
                        side_effect=primary,
                    ):
                        try:
                            publish_module._open_lock(
                                root_descriptor,
                                device=root_device,
                                records=ownership_records,
                            )
                        except BaseException as error:
                            failure = error
                    released = bool(captured) and not self.descriptor_matches_record(
                        captured[0]
                    )
                    foreign_survived = True
                    if foreign_descriptor is not None:
                        try:
                            foreign_survived = (
                                os.read(foreign_descriptor, 64)
                                == b"foreign lock fd"
                            )
                        except OSError:
                            foreign_survived = False
                    contender = original_open(
                        self.release_root / ".publish.lock",
                        os.O_RDWR,
                    )
                    contender_acquired = False
                    try:
                        try:
                            original_flock(
                                contender,
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                        except OSError:
                            pass
                        else:
                            contender_acquired = True
                            original_flock(contender, fcntl.LOCK_UN)
                    finally:
                        original_close(contender)
                finally:
                    for record in captured:
                        if self.descriptor_matches_record(record):
                            original_close(record[0])
                    if foreign_descriptor is not None:
                        try:
                            original_close(foreign_descriptor)
                        except OSError as error:
                            if error.errno != errno.EBADF:
                                raise
                    original_close(root_descriptor)

                self.assertTrue(injected)
                self.assertIs(failure, primary)
                self.assertIsInstance(primary.__cause__, CachePublicationError)
                self.assertEqual(
                    primary.__cause__.code,
                    "publication_cleanup_failed",
                )
                self.assertTrue(released)
                self.assertTrue(foreign_survived)
                self.assertTrue(contender_acquired)

    def test_every_helper_owns_open_fd_before_first_fstat(self) -> None:
        helper_names = (
            "root",
            "generations",
            "lock",
            "stage-directory",
            "stage-file",
            "candidate-pointer",
        )
        failure_factories = (
            lambda: KeyboardInterrupt("open-fstat interrupt"),
            lambda: SystemExit(97),
            lambda: GeneratorExit("open-fstat exit"),
        )
        original_open = os.open
        original_close = os.close
        original_fstat = os.fstat
        index = 0
        for helper_name in helper_names:
            for failure_factory in failure_factories:
                case_index = index
                index += 1
                primary = failure_factory()
                with self.subTest(
                    helper=helper_name,
                    failure=type(primary).__name__,
                ):
                    case = self.root / f"open-fstat-{case_index}"
                    case.mkdir(mode=0o700)
                    release_root = case / "release"
                    self.provision(release_root)
                    ownership_records = []
                    parent_descriptors: list[int] = []
                    captured: list[tuple[int, int, int, int, int]] = []
                    target_descriptor: int | None = None
                    injected = False

                    def matches_target(path) -> bool:
                        if helper_name == "root":
                            return target_descriptor is None
                        if helper_name == "generations":
                            return path == publish_module._GENERATIONS_NAME
                        if helper_name == "lock":
                            return path == publish_module._LOCK_NAME
                        if helper_name == "stage-directory":
                            return isinstance(path, str) and path.startswith(".stage-")
                        if helper_name == "stage-file":
                            return path == publish_module._DATABASE_NAME
                        return isinstance(path, str) and path.startswith("candidate-")

                    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
                        nonlocal target_descriptor
                        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
                        if matches_target(path):
                            details = original_fstat(descriptor)
                            captured.append(
                                (
                                    descriptor,
                                    details.st_dev,
                                    details.st_ino,
                                    stat.S_IFMT(details.st_mode),
                                    details.st_uid,
                                )
                            )
                            target_descriptor = descriptor
                        return descriptor

                    def tracked_fstat(descriptor: int):
                        nonlocal injected
                        if descriptor == target_descriptor and not injected:
                            injected = True
                            raise primary
                        return original_fstat(descriptor)

                    failure: BaseException | None = None
                    try:
                        with patch.object(
                            publish_module.os,
                            "open",
                            new=tracked_open,
                        ), patch.object(
                            publish_module.os,
                            "fstat",
                            new=tracked_fstat,
                        ):
                            try:
                                if helper_name == "root":
                                    publish_module._open_absolute_directory(
                                        release_root,
                                        records=ownership_records,
                                    )
                                else:
                                    parent_path = (
                                        release_root / "generations"
                                        if helper_name in ("stage-directory", "stage-file")
                                        else release_root
                                    )
                                    if helper_name == "stage-file":
                                        parent_path = parent_path / "stage-file"
                                        parent_path.mkdir(mode=0o700)
                                        database = parent_path / publish_module._DATABASE_NAME
                                        database.write_bytes(b"database")
                                        database.chmod(0o400)
                                    parent = original_open(
                                        parent_path,
                                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                                    )
                                    parent_descriptors.append(parent)
                                    device = original_fstat(parent).st_dev
                                    if helper_name == "generations":
                                        publish_module._open_directory_at(
                                            parent,
                                            publish_module._GENERATIONS_NAME,
                                            device=device,
                                            records=ownership_records,
                                        )
                                    elif helper_name == "lock":
                                        publish_module._open_lock(
                                            parent,
                                            device=device,
                                            records=ownership_records,
                                        )
                                    elif helper_name == "stage-directory":
                                        publish_module._create_stage(
                                            parent,
                                            device=device,
                                            generations_path=release_root / "generations",
                                            records=ownership_records,
                                        )
                                    elif helper_name == "stage-file":
                                        publish_module._open_stage_file(
                                            parent,
                                            publish_module._DATABASE_NAME,
                                            device=device,
                                            expected_mode=0o400,
                                            records=ownership_records,
                                        )
                                    else:
                                        publish_module._create_pointer(
                                            parent,
                                            b"{}",
                                            device=device,
                                            records=ownership_records,
                                        )
                            except BaseException as error:
                                failure = error
                        all_released = all(
                            not self.descriptor_matches_record(record)
                            for record in captured
                        )
                        stage_leaves = list(
                            (release_root / "generations").glob(".stage-*")
                        )
                        candidate_leaves = list(
                            release_root.glob("candidate-*.json")
                        )
                    finally:
                        for record in captured:
                            if self.descriptor_matches_record(record):
                                original_close(record[0])
                        for descriptor in parent_descriptors:
                            original_close(descriptor)
                        for leaf in (release_root / "generations").glob(".stage-*"):
                            leaf.rmdir()
                        for leaf in release_root.glob("candidate-*.json"):
                            leaf.unlink()

                    self.assertTrue(injected)
                    self.assertIs(failure, primary)
                    self.assertIsNone(primary.__cause__)
                    self.assertTrue(all_released)
                    self.assertEqual(ownership_records, [])
                    self.assertEqual(stage_leaves, [])
                    self.assertEqual(candidate_leaves, [])

    def test_helper_return_interruptions_leave_ownership_in_outer_ledger(self) -> None:
        boundaries = (
            ("_open_absolute_directory", lambda: SystemExit(101)),
            ("_open_directory_at", lambda: KeyboardInterrupt("generations return")),
            ("_open_lock", lambda: GeneratorExit("lock return")),
            ("_create_stage", lambda: SystemExit(103)),
            ("_open_stage_file", lambda: KeyboardInterrupt("database return")),
            ("_create_pointer", lambda: GeneratorExit("pointer return")),
        )
        original_open = os.open
        original_close = os.close
        original_flock = fcntl.flock
        for index, (boundary, failure_factory) in enumerate(boundaries):
            with self.subTest(boundary=boundary):
                input_path, release_root = self.isolated_publication_case(
                    f"return-boundary-{index}",
                    9900 + index,
                )
                primary = failure_factory()
                original = getattr(publish_module, boundary)
                captured_ledger: list | None = None

                def return_then_interrupt(*args, **kwargs):
                    nonlocal captured_ledger
                    original(*args, **kwargs)
                    captured_ledger = kwargs["records"]
                    raise primary

                failure: BaseException | None = None
                with patch.object(
                    publish_module,
                    boundary,
                    new=return_then_interrupt,
                ):
                    try:
                        self.publish(
                            input_path=input_path,
                            release_root=release_root,
                        )
                    except BaseException as error:
                        failure = error

                contender = original_open(
                    release_root / ".publish.lock",
                    os.O_RDWR,
                )
                contender_acquired = False
                try:
                    try:
                        original_flock(
                            contender,
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                    except OSError:
                        pass
                    else:
                        contender_acquired = True
                        original_flock(contender, fcntl.LOCK_UN)
                finally:
                    original_close(contender)

                self.assertIs(failure, primary)
                self.assertIsNone(primary.__cause__)
                self.assertIsNotNone(captured_ledger)
                self.assertEqual(captured_ledger, [])
                self.assertTrue(contender_acquired)
                self.assertFalse((release_root / "current.json").exists())
                self.assertEqual(list(release_root.glob("candidate-*.json")), [])
                self.assertEqual(
                    list((release_root / "generations").glob(".stage-*")),
                    [],
                )

    def test_manifest_open_is_owned_before_first_fstat(self) -> None:
        self.write_input(9910)
        original_open = os.open
        original_close = os.close
        original_fstat = os.fstat
        primary = GeneratorExit("manifest fstat return")
        manifest_record: tuple[int, int, int, int, int] | None = None
        manifest_descriptor: int | None = None
        injected = False

        def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal manifest_record, manifest_descriptor
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
            if path == publish_module._MANIFEST_NAME and flags & os.O_CREAT:
                details = original_fstat(descriptor)
                manifest_descriptor = descriptor
                manifest_record = (
                    descriptor,
                    details.st_dev,
                    details.st_ino,
                    stat.S_IFMT(details.st_mode),
                    details.st_uid,
                )
            return descriptor

        def tracked_fstat(descriptor: int):
            nonlocal injected
            if descriptor == manifest_descriptor and not injected:
                injected = True
                raise primary
            return original_fstat(descriptor)

        failure: BaseException | None = None
        try:
            with patch.object(
                publish_module.os,
                "open",
                new=tracked_open,
            ), patch.object(
                publish_module.os,
                "fstat",
                new=tracked_fstat,
            ):
                try:
                    self.publish()
                except BaseException as error:
                    failure = error
            assert manifest_record is not None
            released = not self.descriptor_matches_record(manifest_record)
        finally:
            if (
                manifest_record is not None
                and self.descriptor_matches_record(manifest_record)
            ):
                original_close(manifest_record[0])

        self.assertTrue(injected)
        self.assertIs(failure, primary)
        self.assertIsNone(primary.__cause__)
        self.assertTrue(released)
        self.assertFalse((self.release_root / "current.json").exists())
        self.assertEqual(list(self.release_root.glob("candidate-*.json")), [])
        self.assertEqual(
            list((self.release_root / "generations").glob(".stage-*")),
            [],
        )

    def test_lock_inode_swap_is_rejected_before_pointer_switch(self) -> None:
        self.write_input(9544)
        original = publish_module._fsync_pointer
        swapped = False
        foreign = b"foreign lock inode"

        def fsync_then_swap(descriptor):
            nonlocal swapped
            original(descriptor)
            replacement = self.release_root / "replacement.lock"
            replacement.write_bytes(foreign)
            replacement.chmod(0o600)
            os.replace(replacement, self.release_root / ".publish.lock")
            swapped = True

        with patch.object(publish_module, "_fsync_pointer", new=fsync_then_swap):
            self.assert_publication_error("publication_failed")

        self.assertTrue(swapped)
        self.assertEqual((self.release_root / ".publish.lock").read_bytes(), foreign)
        self.assertFalse((self.release_root / "current.json").exists())

    def test_baseexception_windows_preserve_the_correct_side_of_switch(self) -> None:
        self.write_input(9545)
        with patch.object(
            publish_module,
            "_fsync_manifest",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.publish()
        self.assertFalse((self.release_root / "current.json").exists())

        original_replace = publish_module._replace_pointer
        switched = False

        def replace_then_exit(*args, **kwargs):
            nonlocal switched
            result = original_replace(*args, **kwargs)
            switched = True
            raise SystemExit(19)

        with patch.object(
            publish_module,
            "_replace_pointer",
            new=replace_then_exit,
        ):
            with self.assertRaises(SystemExit) as caught:
                self.publish()

        self.assertEqual(caught.exception.code, 19)
        self.assertTrue(switched)
        self.assertIsInstance(caught.exception.__cause__, CachePublicationError)
        self.assertEqual(
            caught.exception.__cause__.code,
            "pointer_durability_uncertain",
        )
        with ReviewedCacheRelease.open_current(
            self.release_root,
            compiler=self.verifier.compiler,
            model_manifest=self.verifier.manifest,
        ) as opened:
            self.assertEqual(opened.generation_id, self.current_generation(self.release_root))

    def test_foreign_stage_cleanup_is_never_deleted(self) -> None:
        self.write_input(9546)
        original = publish_module._fsync_staging_directory
        foreign_marker = b"foreign stage survives"
        foreign_stage: Path | None = None

        def swap_stage_then_fail(descriptor):
            nonlocal foreign_stage
            original(descriptor)
            stages = [
                path
                for path in (self.release_root / "generations").iterdir()
                if path.name.startswith(".stage-")
            ]
            self.assertEqual(len(stages), 1)
            stage = stages[0]
            os.rename(stage, stage.with_name(stage.name + ".owned-moved"))
            stage.mkdir(mode=0o700)
            (stage / "foreign.txt").write_bytes(foreign_marker)
            foreign_stage = stage
            raise OSError("private")

        with patch.object(
            publish_module,
            "_fsync_staging_directory",
            new=swap_stage_then_fail,
        ):
            self.assert_publication_error("publication_failed")

        assert foreign_stage is not None
        self.assertEqual((foreign_stage / "foreign.txt").read_bytes(), foreign_marker)
        self.assertFalse((self.release_root / "current.json").exists())

    def test_foreign_candidate_pointer_cleanup_is_never_deleted(self) -> None:
        self.write_input(9550)
        foreign = b"foreign candidate pointer survives"
        foreign_pointer: Path | None = None

        def swap_candidate_then_fail(*_args, **_kwargs):
            nonlocal foreign_pointer
            candidates = list(self.release_root.glob("candidate-*.json"))
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            replacement = self.release_root / "foreign-candidate.tmp"
            replacement.write_bytes(foreign)
            replacement.chmod(0o400)
            os.replace(replacement, candidate)
            foreign_pointer = candidate
            raise OSError("private")

        with patch.object(
            publish_module,
            "_validate_candidate_pointer",
            new=swap_candidate_then_fail,
        ):
            self.assert_publication_error("publication_failed")

        assert foreign_pointer is not None
        self.assertEqual(foreign_pointer.read_bytes(), foreign)
        self.assertFalse((self.release_root / "current.json").exists())

    def test_release_artifacts_exclude_private_and_learner_material(self) -> None:
        self.write_input(9547)
        result = self.publish()
        generation = self.release_root / "generations" / result.generation_id
        released = (self.release_root / "current.json").read_bytes()
        for child in generation.iterdir():
            released += child.read_bytes()

        for forbidden in (
            b"rawGeneration",
            b"private-child",
            b"learnerId",
            b"profileId",
            b"sessionId",
            b"answerSelections",
            b"confidence",
            b"secret-token",
        ):
            self.assertNotIn(forbidden, released)

    def test_unprovisioned_or_unsafe_release_layout_fails_before_build(self) -> None:
        self.write_input(9548)
        cases: list[Path] = []
        missing = self.root / "missing-release"
        cases.append(missing)
        no_generations = self.root / "no-generations"
        no_generations.mkdir(mode=0o700)
        (no_generations / ".publish.lock").write_bytes(b"")
        (no_generations / ".publish.lock").chmod(0o600)
        cases.append(no_generations)
        bad_lock = self.root / "bad-lock"
        self.provision(bad_lock)
        (bad_lock / ".publish.lock").chmod(0o644)
        cases.append(bad_lock)

        for root in cases:
            with self.subTest(root=root):
                self.assert_publication_error(
                    "publication_failed",
                    release_root=root,
                )
                self.assertFalse((root / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
