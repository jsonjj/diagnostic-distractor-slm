"""Authenticated application-boundary tests for local profile export."""

from __future__ import annotations

from dataclasses import replace
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from services.wayline_forge.app.contracts import ProfileExportV1
from services.wayline_forge.app.profile_export import (
    ProfileExportError,
    ProfileExportService,
)
from services.wayline_forge.app.profile_store import ProfileStore, ProfileStoreError
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.tests.fixtures import EventFactory


MODULE_NAME = "services.wayline_forge.app.profile_export"


class _StoreLookalike:
    def export_profile(self, _profile_id: str) -> object:
        return object()


class ProfileExportServiceSurfaceTests(unittest.TestCase):
    def test_public_api_is_narrow_and_uses_only_redacted_errors(self) -> None:
        spec = importlib.util.find_spec(MODULE_NAME)
        self.assertIsNotNone(spec, "missing authenticated profile-export boundary")
        if spec is None:  # pragma: no cover - narrows the assertion for type checkers
            return
        module = importlib.import_module(MODULE_NAME)
        service_type = module.ProfileExportService
        error_type = module.ProfileExportError

        self.assertEqual(
            tuple(inspect.signature(service_type).parameters),
            ("profile_store",),
        )
        self.assertEqual(
            tuple(inspect.signature(service_type.export).parameters),
            ("self", "profile_id", "current_session_id"),
        )
        self.assertEqual(
            error_type._CODES,
            frozenset(
                {
                    "session_not_current",
                    "storage_busy",
                    "integrity_failure",
                }
            ),
        )
        with self.assertRaises(ValueError):
            error_type("attacker-selected-code")

    def test_constructor_rejects_a_duck_typed_store(self) -> None:
        with self.assertRaisesRegex(TypeError, "ProfileStore"):
            ProfileExportService(_StoreLookalike())  # type: ignore[arg-type]

    def test_constructor_rejects_a_profile_store_subclass(self) -> None:
        class OverridableProfileStore(ProfileStore):
            pass

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "subclass.sqlite3"
            with OverridableProfileStore(path) as store:
                with self.assertRaisesRegex(TypeError, "exact ProfileStore"):
                    ProfileExportService(store)


class ProfileExportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "profiles.sqlite3"
        self.store = ProfileStore(self.database_path)
        self.profile = self.store.create_profile(
            request_id="profile-request-export-service-001",
        )
        self.session = self.store.create_session(
            request_id="session-request-export-service-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.service = ProfileExportService(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def assert_public_error(
        self,
        expected_code: str,
        action,
        *,
        attacker_text: str | None = None,
    ) -> ProfileExportError:
        with self.assertRaises(ProfileExportError) as caught:
            action()
        error = caught.exception
        self.assertEqual(error.code, expected_code)
        self.assertEqual(error.args, (expected_code,))
        self.assertEqual(str(error), expected_code)
        self.assertEqual(repr(error), f"ProfileExportError({expected_code!r})")
        if attacker_text is not None:
            self.assertNotIn(attacker_text, str(error))
            self.assertNotIn(attacker_text, repr(error))
        return error

    def test_current_session_can_export_only_its_strict_profile_contract(self) -> None:
        exported = self.service.export(
            self.profile.profile_id,
            self.session.session_id,
        )

        self.assertIs(type(exported), ProfileExportV1)
        self.assertEqual(exported.profile_id, self.profile.profile_id)
        self.assertEqual(
            tuple(session.session_id for session in exported.sessions),
            (self.session.session_id,),
        )

    def test_unknown_session_is_a_redacted_nonenumerating_failure(self) -> None:
        unknown = "session-unknown-export-service-001"

        self.assert_public_error(
            "session_not_current",
            lambda: self.service.export(self.profile.profile_id, unknown),
            attacker_text=unknown,
        )

    def test_session_rotation_during_export_never_returns_stale_authority(self) -> None:
        real_export = self.store.export_current_profile

        def rotate_before_atomic_authorization(
            profile_id: str,
            current_session_id: str,
        ) -> ProfileExportV1:
            self.store.create_session(
                request_id="session-request-export-service-rotation-002",
                profile_id=profile_id,
                client_build="mac-demo-0.1.1",
            )
            return real_export(profile_id, current_session_id)

        with patch.object(
            self.store,
            "export_current_profile",
            side_effect=rotate_before_atomic_authorization,
        ):
            self.assert_public_error(
                "session_not_current",
                lambda: self.service.export(
                    self.profile.profile_id,
                    self.session.session_id,
                ),
            )

    def test_rotation_cannot_commit_during_export_model_revalidation(self) -> None:
        """The authorization point must cover the final strict model snapshot."""

        worker_ready = threading.Event()
        begin_rotation = threading.Event()
        rotation_committed = threading.Event()
        worker_errors: list[BaseException] = []

        def rotate_from_independent_connection() -> None:
            connection: sqlite3.Connection | None = None
            try:
                # A direct independent connection deliberately bypasses the
                # process RLock, exercising SQLite's cross-process reservation.
                connection = sqlite3.connect(self.database_path)
                worker_ready.set()
                if not begin_rotation.wait(timeout=5):
                    raise AssertionError("rotation was never released")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE local_sessions SET closed_at = ? "
                    "WHERE session_id = ?",
                    (
                        self.session.opened_at,
                        self.session.session_id,
                    ),
                )
                connection.commit()
                rotation_committed.set()
            except BaseException as error:  # test-thread handoff
                worker_errors.append(error)
                worker_ready.set()
                rotation_committed.set()
            finally:
                if connection is not None:
                    connection.close()

        worker = threading.Thread(
            target=rotate_from_independent_connection,
            name="profile-export-rotation",
        )
        worker.start()
        self.assertTrue(worker_ready.wait(timeout=5))

        real_model_dump = ProfileExportV1.model_dump
        rotation_committed_during_revalidation: list[bool] = []

        def dump_while_rotation_attempts(
            exported: ProfileExportV1,
            *args,
            **kwargs,
        ):
            if not rotation_committed_during_revalidation:
                begin_rotation.set()
                rotation_committed_during_revalidation.append(
                    rotation_committed.wait(timeout=1)
                )
            return real_model_dump(exported, *args, **kwargs)

        try:
            with patch.object(
                ProfileExportV1,
                "model_dump",
                dump_while_rotation_attempts,
            ):
                exported = self.service.export(
                    self.profile.profile_id,
                    self.session.session_id,
                )
        finally:
            begin_rotation.set()
            worker.join(timeout=7)

        self.assertFalse(worker.is_alive(), "rotation worker did not terminate")
        if worker_errors:
            raise worker_errors[0]
        self.assertIs(type(exported), ProfileExportV1)
        self.assertEqual(rotation_committed_during_revalidation, [False])
        self.assertTrue(rotation_committed.wait(timeout=0))

    def test_exact_export_cannot_smuggle_another_profiles_closed_session(self) -> None:
        other_profile = self.store.create_profile(
            request_id="profile-request-export-service-smuggle-003",
        )
        foreign_closed = self.store.create_session(
            request_id="session-request-export-service-smuggle-003",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-foreign-private-0.1.0",
        )
        self.store.create_session(
            request_id="session-request-export-service-smuggle-004",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-foreign-private-0.1.1",
        )
        owner_export = self.store.export_profile(self.profile.profile_id)
        foreign_export = self.store.export_profile(other_profile.profile_id)
        foreign_session = next(
            session
            for session in foreign_export.sessions
            if session.session_id == foreign_closed.session_id
        )
        foreign_poisoned = ProfileExportV1.model_validate(
            owner_export.model_dump(mode="json", by_alias=True)
            | {
                "sessions": [
                    foreign_session.model_dump(mode="json", by_alias=True),
                    *[
                        session.model_dump(mode="json", by_alias=True)
                        for session in owner_export.sessions
                    ],
                ]
            }
        )
        altered_current = owner_export.sessions[0].model_copy(
            update={"client_build": "mac-demo-foreign-private-9.9.9"}
        )
        altered_field_poisoned = owner_export.model_copy(
            update={"sessions": (altered_current,)}
        )

        for poisoned, secret in (
            (foreign_poisoned, foreign_session.session_id),
            (altered_field_poisoned, altered_current.client_build),
        ):
            with self.subTest(secret=secret), patch.object(
                ProfileStore,
                "_build_profile_export",
                side_effect=(poisoned, owner_export),
            ):
                self.assert_public_error(
                    "integrity_failure",
                    lambda: self.service.export(
                        self.profile.profile_id,
                        self.session.session_id,
                    ),
                    attacker_text=secret,
                )
                self.assertFalse(self.store._connection.in_transaction)

    def test_atomic_export_rejects_lost_transaction_ownership(self) -> None:
        real_model_dump = ProfileExportV1.model_dump
        committed_early = False

        def dump_after_early_commit(
            exported: ProfileExportV1,
            *args,
            **kwargs,
        ):
            nonlocal committed_early
            if not committed_early:
                committed_early = True
                self.store._connection.commit()
            return real_model_dump(exported, *args, **kwargs)

        with patch.object(ProfileExportV1, "model_dump", dump_after_early_commit):
            self.assert_public_error(
                "integrity_failure",
                lambda: self.service.export(
                    self.profile.profile_id,
                    self.session.session_id,
                ),
            )
        self.assertTrue(committed_early)
        self.assertFalse(self.store._connection.in_transaction)

    def test_atomic_export_rolls_back_an_unexpected_connection_write(self) -> None:
        self.store._connection.execute(
            "CREATE TABLE export_transaction_probe (value TEXT NOT NULL)"
        )
        self.store._connection.commit()
        real_model_dump = ProfileExportV1.model_dump
        wrote_once = False

        def dump_after_unexpected_write(
            exported: ProfileExportV1,
            *args,
            **kwargs,
        ):
            nonlocal wrote_once
            if not wrote_once:
                wrote_once = True
                self.store._connection.execute(
                    "INSERT INTO export_transaction_probe VALUES ('private')"
                )
            return real_model_dump(exported, *args, **kwargs)

        with patch.object(ProfileExportV1, "model_dump", dump_after_unexpected_write):
            self.assert_public_error(
                "integrity_failure",
                lambda: self.service.export(
                    self.profile.profile_id,
                    self.session.session_id,
                ),
            )
        self.assertTrue(wrote_once)
        self.assertFalse(self.store._connection.in_transaction)
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM export_transaction_probe"
            ).fetchone()[0],
            0,
        )

    def test_duck_typed_corrupt_and_mismatched_store_outputs_are_rejected(self) -> None:
        class DuckExport:
            profile_id = "profile-private-duck-secret"

        valid = self.store.export_profile(self.profile.profile_id)
        corrupt = valid.model_copy(
            update={"terminal_event_chain_sha256": "f" * 64},
        )
        omits_current_session = ProfileExportV1.model_validate(
            valid.model_dump(mode="json", by_alias=True)
            | {
                "activeWorldId": None,
                "campaignOrdinal": None,
                "sessions": [],
                "events": [],
                "terminalEventChainSha256": "0" * 64,
            }
        )
        other_profile = self.store.create_profile(
            request_id="profile-request-export-service-other-002",
        )
        self.store.create_session(
            request_id="session-request-export-service-other-002",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        mismatched = self.store.export_profile(other_profile.profile_id)

        for returned, secret in (
            (DuckExport(), DuckExport.profile_id),
            (corrupt, "f" * 64),
            (omits_current_session, self.session.session_id),
            (mismatched, other_profile.profile_id),
        ):
            with self.subTest(returned=type(returned).__name__), patch.object(
                ProfileStore,
                "_build_profile_export",
                side_effect=(returned, valid),
            ):
                self.assert_public_error(
                    "integrity_failure",
                    lambda: self.service.export(
                        self.profile.profile_id,
                        self.session.session_id,
                    ),
                    attacker_text=secret,
                )

    def test_invalid_profile_values_are_rejected_before_any_session_read(self) -> None:
        attacker_text = "<script>private-profile-id</script>"
        invalid_values = (
            None,
            True,
            7,
            b"profile-export-service-001",
            bytearray(b"profile-export-service-001"),
            object(),
            "x",
            " profile-export-service-001",
            "profile/export/service/001",
            attacker_text,
        )

        with patch.object(
            self.store,
            "load_session",
            side_effect=AssertionError("invalid path identity reached storage"),
        ) as load_session:
            for value in invalid_values:
                with self.subTest(value=type(value).__name__):
                    self.assert_public_error(
                        "session_not_current",
                        lambda value=value: self.service.export(  # type: ignore[arg-type]
                            value,
                            self.session.session_id,
                        ),
                        attacker_text=attacker_text,
                    )
        load_session.assert_not_called()

    def test_direct_and_wrapped_sqlite_busy_are_redacted_at_every_stage(self) -> None:
        secret = "database is locked private-profile-export-value"

        def failures():
            direct = sqlite3.OperationalError(secret)
            wrapped = ProfileStoreError("wrapped private storage detail")
            wrapped.__cause__ = sqlite3.OperationalError(secret)
            return (direct, wrapped)

        for stage in (
            "resolve_before",
            "internal_build",
            "atomic_authorization",
        ):
            for failure in failures():
                with self.subTest(stage=stage, failure=type(failure).__name__):
                    if stage == "resolve_before":
                        context = patch.object(
                            self.service._current_sessions,
                            "resolve",
                            side_effect=failure,
                        )
                    elif stage == "internal_build":
                        context = patch.object(
                            ProfileStore,
                            "_build_profile_export",
                            side_effect=failure,
                        )
                    else:
                        context = patch.object(
                            self.store,
                            "export_current_profile",
                            side_effect=failure,
                        )
                    with context:
                        self.assert_public_error(
                            "storage_busy",
                            lambda: self.service.export(
                                self.profile.profile_id,
                                self.session.session_id,
                            ),
                            attacker_text=secret,
                        )

    def test_unknown_stale_cross_profile_and_path_mismatch_share_one_failure(self) -> None:
        other_profile = self.store.create_profile(
            request_id="profile-request-export-service-cross-002",
        )
        other_session = self.store.create_session(
            request_id="session-request-export-service-cross-002",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        unknown_profile = "profile-unknown-export-service-001"

        for profile_id, session_id, secret in (
            (unknown_profile, self.session.session_id, unknown_profile),
            (other_profile.profile_id, self.session.session_id, other_profile.profile_id),
            (self.profile.profile_id, other_session.session_id, other_session.session_id),
        ):
            with self.subTest(profile_id=profile_id, session_id=session_id):
                self.assert_public_error(
                    "session_not_current",
                    lambda profile_id=profile_id, session_id=session_id: (
                        self.service.export(profile_id, session_id)
                    ),
                    attacker_text=secret,
                )

        self.store.create_session(
            request_id="session-request-export-service-stale-003",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.1",
        )
        self.assert_public_error(
            "session_not_current",
            lambda: self.service.export(
                self.profile.profile_id,
                self.session.session_id,
            ),
            attacker_text=self.session.session_id,
        )

    def test_invalid_session_values_are_exact_and_never_reach_storage(self) -> None:
        class StringLookalike(str):
            pass

        attacker_text = "<script>private-session-id</script>"
        invalid_values = (
            None,
            True,
            7,
            b"session-export-service-001",
            bytearray(b"session-export-service-001"),
            object(),
            StringLookalike("session-export-service-001"),
            "x",
            " session-export-service-001",
            "session/export/service/001",
            "s" * 97,
            attacker_text,
        )

        with patch.object(
            self.store,
            "load_session",
            side_effect=AssertionError("invalid session identity reached storage"),
        ) as load_session:
            for value in invalid_values:
                with self.subTest(value=type(value).__name__):
                    self.assert_public_error(
                        "session_not_current",
                        lambda value=value: self.service.export(  # type: ignore[arg-type]
                            self.profile.profile_id,
                            value,
                        ),
                        attacker_text=attacker_text,
                    )
        load_session.assert_not_called()

    def test_all_nonbusy_unexpected_failures_are_redacted_integrity_failures(self) -> None:
        secret = "private export implementation detail"
        poisoned_boundary_error = ProfileExportError("integrity_failure")
        poisoned_boundary_error.args = (secret,)
        failures = (
            sqlite3.OperationalError("disk input/output " + secret),
            sqlite3.DatabaseError(secret),
            ProfileStoreError(secret),
            RuntimeError(secret),
            poisoned_boundary_error,
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch.object(
                ProfileStore,
                "_build_profile_export",
                side_effect=failure,
            ):
                self.assert_public_error(
                    "integrity_failure",
                    lambda: self.service.export(
                        self.profile.profile_id,
                        self.session.session_id,
                    ),
                    attacker_text=secret,
                )

    def test_export_keeps_revealed_evidence_but_never_quiz_answer_material(self) -> None:
        observation = replace(
            EventFactory.wrong(
                "align_by_ends",
                ordinal=2,
                profile=self.profile.profile_id,
                session=self.session.session_id,
                battle="valuehold_route_1",
                batch="batch-export-service-001",
            ),
            occurred_at=self.session.opened_at,
        )
        self.store.append(observation)

        with QuizStore(
            self.database_path,
            allow_unverified_test_material=True,
        ):
            pass
        digest = "a" * 64
        private_sentinels = (
            "SEALED-ANSWER-KEY-PRIVATE",
            "RAW-SLM-OUTPUT-PRIVATE",
            "LAUNCH-TOKEN-PRIVATE",
        )
        private_json = json.dumps(
            {
                "correctAnswer": private_sentinels[0],
                "rawSlmText": private_sentinels[1],
                "launchToken": private_sentinels[2],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.store._connection.execute(
            """
            INSERT INTO quiz_machines (
                batch_id, profile_id, state, version, machine_json,
                machine_sha256, batch_material_sha256
            ) VALUES (?, ?, 'ready', 1, '{}', ?, ?)
            """,
            ("batch-export-service-001", self.profile.profile_id, digest, digest),
        )
        self.store._connection.execute(
            """
            INSERT INTO quiz_batch_material (
                batch_id, profile_id, batch_material_sha256,
                sealed_quiz_sha256, context_json, context_sha256,
                item_count, private_json, private_json_sha256
            ) VALUES (?, ?, ?, ?, '{}', ?, 3, ?, ?)
            """,
            (
                "batch-export-service-001",
                self.profile.profile_id,
                digest,
                digest,
                digest,
                private_json,
                digest,
            ),
        )
        self.store._connection.commit()

        exported = self.service.export(
            self.profile.profile_id,
            self.session.session_id,
        )

        revealed = json.loads(exported.events[-1].canonical_event_json)
        self.assertEqual(revealed["first_procedure_id"], "align_by_ends")
        self.assertEqual(
            tuple(revealed["canonical_feedback"]),
            observation.canonical_feedback,
        )
        serialized = exported.model_dump_json(by_alias=True)
        for sentinel in private_sentinels:
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, serialized)
        for forbidden_key in (
            "correctAnswer",
            "rawSlmText",
            "launchToken",
            "sealedAnswerMaterial",
        ):
            with self.subTest(forbidden_key=forbidden_key):
                self.assertNotIn(forbidden_key, serialized)


if __name__ == "__main__":
    unittest.main()
