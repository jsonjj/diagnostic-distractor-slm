"""Authenticated application boundary for secure local-profile deletion."""

from __future__ import annotations

import inspect
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.assisted_route_store import (
    AssistedRouteStore,
    AssistedRouteStoreError,
)
from services.wayline_forge.app.profile_deletion import (
    ProfileDeletionError,
    ProfileDeletionService,
)
from services.wayline_forge.app.profile_store import (
    CampaignStateConflictError,
    IdentityStoreCorruptionError,
    LocalProfile,
    ProfileNotFoundError,
    ProfileStore,
)
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.tests.test_assisted_route_machine import (
    AssistedRouteMachineTests,
)


class _StoreLookalike:
    """Deliberately not a ProfileStore despite exposing similarly named methods."""

    def load_profile(self, _profile_id: str) -> object:
        return object()

    def load_session(self, _session_id: str) -> object:
        return object()

    def load_open_session(self, _profile_id: str) -> object:
        return object()

    def delete_profile(self, _profile_id: str) -> None:
        raise AssertionError("a lookalike store must never receive deletion authority")


class ProfileDeletionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "profiles.sqlite"
        self.store = ProfileStore(self.database_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _identity(self, suffix: str):
        profile = self.store.create_profile(
            request_id=f"profile-request-{suffix}",
        )
        session = self.store.create_session(
            request_id=f"session-request-{suffix}",
            profile_id=profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        return profile, session

    def _initialize_quiz_tables(self) -> None:
        with QuizStore(
            self.database_path,
            allow_unverified_test_material=True,
        ):
            pass

    def _seed_quiz_rows(self, profile_id: str, *, suffix: str) -> str:
        self._initialize_quiz_tables()
        batch_id = f"batch-delete-{suffix}"
        digest = "a" * 64
        connection = self.store._connection
        connection.execute(
            """
            INSERT INTO quiz_machines (
                batch_id, profile_id, state, version, machine_json,
                machine_sha256, batch_material_sha256
            ) VALUES (?, ?, 'ready', 1, ?, ?, ?)
            """,
            (batch_id, profile_id, "{}", digest, digest),
        )
        connection.execute(
            """
            INSERT INTO quiz_transition_receipts (
                batch_id, profile_id, action, request_id, payload_sha256,
                from_version, to_version, output_sha256, receipt_json,
                receipt_sha256, outbox_sha256
            ) VALUES (?, ?, 'initial', ?, ?, 1, 2, ?, '{}', ?, ?)
            """,
            (
                batch_id,
                profile_id,
                f"initial-delete-{suffix}",
                digest,
                digest,
                digest,
                digest,
            ),
        )
        connection.execute(
            """
            INSERT INTO quiz_observation_outbox (
                profile_id, batch_id, item_id, ordinal, event_id,
                idempotency_id, canonical_json, event_sha256, delivered
            ) VALUES (?, ?, ?, 2, ?, ?, '{}', ?, 0)
            """,
            (
                profile_id,
                batch_id,
                f"item-delete-{suffix}",
                f"event-delete-{suffix}",
                f"idempotency-delete-{suffix}",
                digest,
            ),
        )
        connection.execute(
            """
            INSERT INTO quiz_batch_material (
                batch_id, profile_id, batch_material_sha256,
                sealed_quiz_sha256, context_json, context_sha256,
                item_count, private_json, private_json_sha256
            ) VALUES (?, ?, ?, ?, '{}', ?, 3, '{}', ?)
            """,
            (batch_id, profile_id, digest, digest, digest, digest),
        )
        connection.execute(
            """
            INSERT INTO quiz_preparation_receipts (
                profile_id, request_id, batch_id, payload_sha256,
                output_sha256, receipt_json, receipt_sha256
            ) VALUES (?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                profile_id,
                f"prepare-delete-{suffix}",
                batch_id,
                digest,
                digest,
                digest,
            ),
        )
        connection.commit()
        return batch_id

    def _profile_row_counts(self, profile_id: str) -> dict[str, int]:
        tables = (
            "local_profiles",
            "local_sessions",
            "identity_command_receipts",
            "event_log",
            "learner_projection",
            "quiz_machines",
            "quiz_transition_receipts",
            "quiz_observation_outbox",
            "quiz_batch_material",
            "quiz_preparation_receipts",
        )
        return {
            table: int(
                self.store._connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()[0]
            )
            for table in tables
        }

    def test_public_api_and_error_codes_are_narrow(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(ProfileDeletionService.delete).parameters),
            ("self", "profile_id", "current_session_id"),
        )
        self.assertEqual(
            ProfileDeletionError._CODES,
            frozenset(
                {
                    "session_not_current",
                    "storage_busy",
                    "integrity_failure",
                }
            ),
        )
        with self.assertRaises(ValueError):
            ProfileDeletionError("unknown_failure")

    def test_constructor_rejects_a_duck_typed_store(self) -> None:
        with self.assertRaisesRegex(TypeError, "ProfileStore"):
            ProfileDeletionService(_StoreLookalike())  # type: ignore[arg-type]

    def test_current_session_is_checked_immediately_before_deletion(self) -> None:
        profile, session = self._identity("order-001")
        calls: list[str] = []
        load_profile = self.store.load_profile
        load_session = self.store.load_session
        load_open_session = self.store.load_open_session
        delete_profile = self.store.delete_profile

        def record_delete(
            value: str,
            *,
            expected_session_id: str | None = None,
        ) -> None:
            calls.append("delete_profile")
            self.assertEqual(expected_session_id, session.session_id)
            delete_profile(value, expected_session_id=expected_session_id)

        with (
            patch.object(
                self.store,
                "load_profile",
                side_effect=lambda value: (
                    calls.append("load_profile"), load_profile(value)
                )[1],
            ),
            patch.object(
                self.store,
                "load_session",
                side_effect=lambda value: (
                    calls.append("load_session"), load_session(value)
                )[1],
            ),
            patch.object(
                self.store,
                "load_open_session",
                side_effect=lambda value: (
                    calls.append("load_open_session"), load_open_session(value)
                )[1],
            ),
            patch.object(
                self.store,
                "delete_profile",
                side_effect=record_delete,
            ),
        ):
            result = ProfileDeletionService(self.store).delete(
                profile.profile_id,
                session.session_id,
            )

        self.assertIsNone(result)
        self.assertEqual(
            calls,
            ["load_profile", "load_session", "load_open_session", "delete_profile"],
        )

    def test_session_rotation_at_mutation_fails_without_deleting_the_profile(self) -> None:
        profile, session = self._identity("race-001")
        expected_session_ids: list[str | None] = []

        def rotate_before_delete(
            profile_id: str,
            *,
            expected_session_id: str | None = None,
        ) -> None:
            expected_session_ids.append(expected_session_id)
            replacement = self.store.create_session(
                request_id="session-request-race-002",
                profile_id=profile_id,
                client_build="mac-demo-0.1.0",
            )
            if replacement.session_id != expected_session_id:
                raise CampaignStateConflictError("private current-session conflict")
            raise AssertionError("the simulated race must rotate the session")

        with patch.object(
            self.store,
            "delete_profile",
            side_effect=rotate_before_delete,
        ):
            with self.assertRaises(ProfileDeletionError) as caught:
                ProfileDeletionService(self.store).delete(
                    profile.profile_id,
                    session.session_id,
                )

        self.assertEqual(expected_session_ids, [session.session_id])
        self.assertEqual(caught.exception.code, "session_not_current")
        self.assertEqual(str(caught.exception), "session_not_current")
        self.assertEqual(self.store.load_profile(profile.profile_id), profile)
        self.assertNotEqual(
            self.store.load_open_session(profile.profile_id).session_id,
            session.session_id,
        )

    def test_success_removes_identity_learning_quiz_rows_and_migration_backups(self) -> None:
        profile, session = self._identity("success-001")
        self._seed_quiz_rows(profile.profile_id, suffix="success-001")
        before = self._profile_row_counts(profile.profile_id)
        self.assertEqual(
            before,
            {
                "local_profiles": 1,
                "local_sessions": 1,
                "identity_command_receipts": 2,
                "event_log": 1,
                "learner_projection": 0,
                "quiz_machines": 1,
                "quiz_transition_receipts": 1,
                "quiz_observation_outbox": 1,
                "quiz_batch_material": 1,
                "quiz_preparation_receipts": 1,
            },
        )
        backup = self.database_path.with_suffix(
            self.database_path.suffix + ".backup-v3"
        )
        backup.write_text(profile.profile_id, encoding="utf-8")

        result = ProfileDeletionService(self.store).delete(
            profile.profile_id,
            session.session_id,
        )

        self.assertIsNone(result)
        self.assertEqual(
            self._profile_row_counts(profile.profile_id),
            {table: 0 for table in before},
        )
        self.assertFalse(backup.exists())
        with self.assertRaises(ProfileNotFoundError):
            self.store.load_profile(profile.profile_id)

    def test_stale_cross_profile_and_unknown_identities_share_one_safe_failure(self) -> None:
        first_profile, stale_session = self._identity("first-001")
        current_session = self.store.create_session(
            request_id="session-request-first-002",
            profile_id=first_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        second_profile, second_session = self._identity("second-001")
        cases = (
            (first_profile.profile_id, stale_session.session_id),
            (first_profile.profile_id, second_session.session_id),
            ("unknown-profile-private-001", current_session.session_id),
            (first_profile.profile_id, "unknown-session-private-001"),
        )

        for profile_id, session_id in cases:
            with self.subTest(profile_id=profile_id, session_id=session_id):
                with self.assertRaises(ProfileDeletionError) as caught:
                    ProfileDeletionService(self.store).delete(profile_id, session_id)
                error = caught.exception
                self.assertEqual(error.code, "session_not_current")
                self.assertEqual(str(error), "session_not_current")
                self.assertNotIn(profile_id, repr(error))
                self.assertNotIn(session_id, repr(error))

        self.assertEqual(
            self.store.load_profile(first_profile.profile_id),
            first_profile,
        )
        self.assertEqual(
            self.store.load_profile(second_profile.profile_id),
            second_profile,
        )

    def test_sqlite_write_lock_is_retryable_and_preserves_the_profile(self) -> None:
        profile, session = self._identity("busy-001")
        self.store._connection.execute("PRAGMA busy_timeout = 0")
        locker = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            locker.execute("BEGIN IMMEDIATE")
            with self.assertRaises(ProfileDeletionError) as caught:
                ProfileDeletionService(self.store).delete(
                    profile.profile_id,
                    session.session_id,
                )
        finally:
            locker.rollback()
            locker.close()

        self.assertEqual(caught.exception.code, "storage_busy")
        self.assertEqual(str(caught.exception), "storage_busy")
        self.assertNotIn(profile.profile_id, repr(caught.exception))
        self.assertEqual(self.store.load_profile(profile.profile_id), profile)

    def test_transaction_failure_rolls_back_every_profile_owned_table(self) -> None:
        profile, session = self._identity("rollback-001")
        self._seed_quiz_rows(profile.profile_id, suffix="rollback-001")
        before = self._profile_row_counts(profile.profile_id)
        self.store._connection.execute(
            """
            CREATE TRIGGER fail_profile_delete
            BEFORE DELETE ON local_profiles
            BEGIN
                SELECT RAISE(ABORT, 'private injected deletion failure');
            END
            """
        )
        self.store._connection.commit()

        with self.assertRaises(ProfileDeletionError) as caught:
            ProfileDeletionService(self.store).delete(
                profile.profile_id,
                session.session_id,
            )

        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(
            self._profile_row_counts(profile.profile_id),
            before,
        )
        self.assertEqual(self.store.load_profile(profile.profile_id), profile)
        self.assertEqual(self.store.load_session(session.session_id), session)

    def test_corrupt_typed_state_is_not_trusted_by_attribute_shape(self) -> None:
        profile, session = self._identity("corrupt-001")
        forged = LocalProfile(
            schema_version=profile.schema_version,
            profile_id="different-profile-001",
            created_at=profile.created_at,
        )

        with patch.object(self.store, "load_profile", return_value=forged):
            with self.assertRaises(ProfileDeletionError) as caught:
                ProfileDeletionService(self.store).delete(
                    profile.profile_id,
                    session.session_id,
                )

        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(self.store.load_profile(profile.profile_id), profile)

    def test_corruption_and_unexpected_failures_are_redacted(self) -> None:
        profile, session = self._identity("redaction-001")
        sensitive = f"private-{profile.profile_id}-{session.session_id}"
        failures = (
            IdentityStoreCorruptionError(sensitive),
            RuntimeError(sensitive),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(
                    self.store,
                    "delete_profile",
                    side_effect=failure,
                ):
                    with self.assertRaises(ProfileDeletionError) as caught:
                        ProfileDeletionService(self.store).delete(
                            profile.profile_id,
                            session.session_id,
                        )
                error = caught.exception
                self.assertEqual(error.code, "integrity_failure")
                self.assertEqual(str(error), "integrity_failure")
                self.assertNotIn(sensitive, repr(error))

        self.assertEqual(self.store.load_profile(profile.profile_id), profile)

    def test_profile_deletion_fails_closed_if_assisted_rows_do_not_cascade(self) -> None:
        profile, session = self._identity("assisted-cascade-corruption")
        self.store._connection.execute(
            "CREATE TABLE assisted_route_material (profile_id TEXT NOT NULL)"
        )
        self.store._connection.execute(
            "CREATE TABLE assisted_route_preparation_receipts "
            "(profile_id TEXT NOT NULL)"
        )
        self.store._connection.execute(
            "INSERT INTO assisted_route_material VALUES (?)",
            (profile.profile_id,),
        )
        self.store._connection.execute(
            "INSERT INTO assisted_route_preparation_receipts VALUES (?)",
            (profile.profile_id,),
        )
        self.store._connection.commit()

        with self.assertRaises(ProfileDeletionError) as raised:
            ProfileDeletionService(self.store).delete(
                profile.profile_id,
                session.session_id,
            )

        self.assertEqual(raised.exception.code, "integrity_failure")
        self.assertEqual(self.store.load_profile(profile.profile_id), profile)


class AssistedRouteDeletionCascadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        AssistedRouteMachineTests.setUpClass()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "profiles.sqlite3"
        self.profiles = ProfileStore(self.path)
        self.profile = self.profiles.create_profile(
            request_id="profile-assisted-deletion"
        )
        self.session = self.profiles.create_session(
            request_id="session-assisted-deletion",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        fixture = AssistedRouteMachineTests()
        self.fixture = fixture
        self.material = fixture._material(
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
        )
        self.routes = AssistedRouteStore(
            self.path,
            compiler=fixture.verifier.compiler,
            manifest=fixture.verifier.manifest,
        )
        ordinal, digest = self.profiles.event_head(self.profile.profile_id)
        self.stored = self.routes.create_prepared(
            route_id="assisted-deletion-route",
            profile_id=self.profile.profile_id,
            source_session_id=self.session.session_id,
            world_id="valuehold",
            preparation_request_id="prepare-assisted-deletion",
            preparation_payload_sha256="1" * 64,
            event_head_ordinal=ordinal,
            event_head_hash=digest,
            route_plan_sha256="9" * 64,
            material=self.material,
        )

    def tearDown(self) -> None:
        self.routes.close()
        self.profiles.close()
        self.temporary.cleanup()

    def test_profile_deletion_cascades_private_material_and_receipts(self) -> None:
        ProfileDeletionService(self.profiles).delete(
            self.profile.profile_id,
            self.session.session_id,
        )

        with sqlite3.connect(self.path) as connection:
            material_count = connection.execute(
                "SELECT COUNT(*) FROM assisted_route_material"
            ).fetchone()[0]
            receipt_count = connection.execute(
                "SELECT COUNT(*) FROM assisted_route_preparation_receipts"
            ).fetchone()[0]
        self.assertEqual(material_count, 0)
        self.assertEqual(receipt_count, 0)
        self.routes.close()
        self.routes = AssistedRouteStore(
            self.path,
            compiler=self.fixture.verifier.compiler,
            manifest=self.fixture.verifier.manifest,
        )
        with self.assertRaises(AssistedRouteStoreError) as missing:
            self.routes.load(
                self.stored.route_id,
                profile_id=self.profile.profile_id,
            )
        self.assertEqual(missing.exception.code, "profile_not_found")


if __name__ == "__main__":
    unittest.main()
