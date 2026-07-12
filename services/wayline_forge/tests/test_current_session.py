from dataclasses import FrozenInstanceError, fields, replace
import inspect
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.current_session import (
    CurrentSessionError,
    CurrentSessionResolver,
    ResolvedCurrentSession,
)
from services.wayline_forge.app.profile_store import (
    IdentityStoreCorruptionError,
    LocalProfile,
    LocalSession,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
)


class CurrentSessionResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "wayline.sqlite3"
        )
        self.store = ProfileStore(self.database_path)
        self.profile = self.store.create_profile(
            request_id="profile-request-current-session-001"
        )
        self.session = self.store.create_session(
            request_id="session-request-current-session-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.resolver = CurrentSessionResolver(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def assert_public_error(
        self,
        expected_code: str,
        action,
        *,
        attacker_text: str | None = None,
    ) -> CurrentSessionError:
        with self.assertRaises(CurrentSessionError) as caught:
            action()
        error = caught.exception
        self.assertEqual(error.code, expected_code)
        self.assertEqual(error.args, (expected_code,))
        self.assertEqual(str(error), expected_code)
        self.assertEqual(
            repr(error),
            f"CurrentSessionError({expected_code!r})",
        )
        if attacker_text is not None:
            self.assertNotIn(attacker_text, str(error))
            self.assertNotIn(attacker_text, repr(error))
        return error

    def test_public_api_accepts_only_a_profile_store_and_public_session_id(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(CurrentSessionResolver).parameters),
            ("profile_store",),
        )
        self.assertEqual(
            tuple(inspect.signature(CurrentSessionResolver.resolve).parameters),
            ("self", "session_id"),
        )
        self.assertEqual(
            CurrentSessionError._CODES,
            frozenset(
                {
                    "session_not_current",
                    "storage_busy",
                    "integrity_failure",
                }
            ),
        )
        with self.assertRaises(TypeError):
            CurrentSessionResolver(object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            CurrentSessionError("attacker-selected-code")

    def test_resolves_exact_current_profile_and_session_as_frozen_identity(self) -> None:
        resolved = self.resolver.resolve(self.session.session_id)

        self.assertIs(type(resolved), ResolvedCurrentSession)
        self.assertEqual(resolved.profile_id, self.profile.profile_id)
        self.assertEqual(resolved.session_id, self.session.session_id)
        self.assertEqual(
            tuple(field.name for field in fields(resolved)),
            ("profile_id", "session_id"),
        )
        self.assertFalse(hasattr(resolved, "__dict__"))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            resolved.profile_id = "profile-replaced"  # type: ignore[misc]

    def test_resolved_identity_revalidates_strict_identifier_types(self) -> None:
        valid_profile = "profile-current-001"
        valid_session = "session-current-001"
        invalid_pairs = (
            (True, valid_session),
            (b"profile-current-001", valid_session),
            (valid_profile, False),
            (valid_profile, bytearray(b"session-current-001")),
            ("x", valid_session),
            (valid_profile, "session current 001"),
        )
        for profile_id, session_id in invalid_pairs:
            with self.subTest(
                profile_id=type(profile_id).__name__,
                session_id=type(session_id).__name__,
            ), self.assertRaises((TypeError, ValueError)):
                ResolvedCurrentSession(  # type: ignore[arg-type]
                    profile_id=profile_id,
                    session_id=session_id,
                )

    def test_invalid_public_session_values_are_coalesced_before_storage(self) -> None:
        attacker_text = "<script>session-secret</script>"
        invalid_values = (
            None,
            True,
            7,
            b"session-current-001",
            bytearray(b"session-current-001"),
            object(),
            "x",
            " session-current-001",
            "session/current/001",
            attacker_text,
        )
        with patch.object(
            self.store,
            "load_session",
            side_effect=AssertionError("storage must not receive invalid input"),
        ) as load_session:
            for value in invalid_values:
                with self.subTest(value=type(value).__name__):
                    self.assert_public_error(
                        "session_not_current",
                        lambda value=value: self.resolver.resolve(value),  # type: ignore[arg-type]
                        attacker_text=attacker_text,
                    )
        load_session.assert_not_called()

    def test_unknown_stale_and_closed_sessions_share_one_safe_failure(self) -> None:
        unknown = "session-unknown-current-001"
        self.assert_public_error(
            "session_not_current",
            lambda: self.resolver.resolve(unknown),
            attacker_text=unknown,
        )

        replacement = self.store.create_session(
            request_id="session-request-current-session-002",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.assert_public_error(
            "session_not_current",
            lambda: self.resolver.resolve(self.session.session_id),
            attacker_text=self.session.session_id,
        )
        self.assertEqual(
            self.resolver.resolve(replacement.session_id),
            ResolvedCurrentSession(
                profile_id=self.profile.profile_id,
                session_id=replacement.session_id,
            ),
        )

    def test_missing_or_cross_profile_current_authority_is_nonenumerating(self) -> None:
        second_profile = self.store.create_profile(
            request_id="profile-request-current-session-002"
        )
        second_session = self.store.create_session(
            request_id="session-request-current-session-003",
            profile_id=second_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )

        for current in (None, second_session):
            with self.subTest(current=current), patch.object(
                self.store,
                "load_open_session",
                return_value=current,
            ):
                self.assert_public_error(
                    "session_not_current",
                    lambda: self.resolver.resolve(self.session.session_id),
                    attacker_text=self.session.session_id,
                )

        with patch.object(
            self.store,
            "load_profile",
            return_value=second_profile,
        ):
            self.assert_public_error(
                "session_not_current",
                lambda: self.resolver.resolve(self.session.session_id),
                attacker_text=second_profile.profile_id,
            )

    def test_exact_typed_authority_is_required_at_every_read(self) -> None:
        class DuckSession:
            session_id = self.session.session_id
            profile_id = self.profile.profile_id
            closed_at = None

        class DuckProfile:
            profile_id = self.profile.profile_id

        cases = (
            ("load_session", DuckSession()),
            ("load_profile", DuckProfile()),
            ("load_open_session", DuckSession()),
        )
        for method_name, returned in cases:
            with self.subTest(method=method_name), patch.object(
                self.store,
                method_name,
                return_value=returned,
            ):
                self.assert_public_error(
                    "integrity_failure",
                    lambda: self.resolver.resolve(self.session.session_id),
                )

    def test_mismatched_typed_records_fail_as_auth_or_integrity_without_detail(self) -> None:
        mismatched_session = replace(
            self.session,
            session_id="session-mismatched-authority-001",
        )
        with patch.object(
            self.store,
            "load_session",
            return_value=mismatched_session,
        ):
            self.assert_public_error(
                "integrity_failure",
                lambda: self.resolver.resolve(self.session.session_id),
                attacker_text=mismatched_session.session_id,
            )

        changed_current = replace(
            self.session,
            client_build="mac-demo-corrupt-9.9.9",
        )
        with patch.object(
            self.store,
            "load_open_session",
            return_value=changed_current,
        ):
            self.assert_public_error(
                "integrity_failure",
                lambda: self.resolver.resolve(self.session.session_id),
                attacker_text=changed_current.client_build,
            )

    def test_missing_profile_behind_a_validated_session_is_integrity_failure(self) -> None:
        with patch.object(
            self.store,
            "load_profile",
            side_effect=ProfileNotFoundError("missing attacker profile"),
        ):
            self.assert_public_error(
                "integrity_failure",
                lambda: self.resolver.resolve(self.session.session_id),
                attacker_text="missing attacker profile",
            )

    def test_sqlite_busy_at_every_read_stage_is_retryable_and_redacted(self) -> None:
        secret = "database is locked secret-session-value"
        for method_name in (
            "load_session",
            "load_profile",
            "load_open_session",
        ):
            with self.subTest(method=method_name), patch.object(
                self.store,
                method_name,
                side_effect=sqlite3.OperationalError(secret),
            ):
                self.assert_public_error(
                    "storage_busy",
                    lambda: self.resolver.resolve(self.session.session_id),
                    attacker_text=secret,
                )

        cause = sqlite3.OperationalError(secret)
        wrapped = ProfileStoreError("wrapped storage secret")
        wrapped.__cause__ = cause
        with patch.object(
            self.store,
            "load_session",
            side_effect=wrapped,
        ):
            self.assert_public_error(
                "storage_busy",
                lambda: self.resolver.resolve(self.session.session_id),
                attacker_text=secret,
            )

    def test_corruption_nonbusy_sqlite_and_unexpected_failures_are_integrity(self) -> None:
        failures = (
            IdentityStoreCorruptionError("corrupt secret authority"),
            sqlite3.OperationalError("disk input/output secret"),
            sqlite3.DatabaseError("database secret"),
            ProfileStoreError("store secret"),
            RuntimeError("unexpected attacker secret"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch.object(
                self.store,
                "load_session",
                side_effect=failure,
            ):
                self.assert_public_error(
                    "integrity_failure",
                    lambda: self.resolver.resolve(self.session.session_id),
                    attacker_text=str(failure),
                )

    def test_exact_local_profile_and_session_types_are_the_only_authority(self) -> None:
        self.assertIs(type(self.profile), LocalProfile)
        self.assertIs(type(self.session), LocalSession)
        resolved = self.resolver.resolve(self.session.session_id)
        self.assertEqual(
            (resolved.profile_id, resolved.session_id),
            (self.profile.profile_id, self.session.session_id),
        )


if __name__ == "__main__":
    unittest.main()
