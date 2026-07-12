from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.campaign_catalog import (
    CAMPAIGN_CATALOG_V1_SHA256,
    CampaignCatalog,
)
from services.wayline_forge.app.contracts import (
    ProfileCreate,
    ProfileCreated,
    SessionCreate,
    SessionCreated,
)
from services.wayline_forge.app.events import (
    BattleOutcomeEvent,
    EVENT_SCHEMA_VERSION,
    OUTCOME_EVENT_SCHEMA_VERSION,
    WorldActivatedEvent,
)
from services.wayline_forge.app.identity_lifecycle import (
    IdentityLifecycleError,
    IdentityLifecycleService,
)
from services.wayline_forge.app.profile_store import ProfileStore


PROFILE_CREATED_AT = "2026-07-11T12:00:00.000000Z"
FIRST_SESSION_AT = "2026-07-11T12:01:00.000000Z"
DECIMARA_ACTIVATED_AT = "2026-07-11T12:02:00Z"
SECOND_SESSION_AT = "2026-07-11T12:03:00.000000Z"
FRACTURE_ACTIVATED_AT = "2026-07-11T12:04:00.000000Z"
THIRD_SESSION_AT = "2026-07-11T12:05:00.000000Z"


class IdentityLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = ProfileStore(
            Path(self.temporary_directory.name) / "profiles.sqlite"
        )
        self.service = IdentityLifecycleService(self.store)
        self.catalog = CampaignCatalog.packaged_v1()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _create_profile(
        self,
        *,
        request_id: str = "profile-request-001",
        timestamp: str = PROFILE_CREATED_AT,
    ) -> ProfileCreated:
        request = ProfileCreate(
            schemaVersion="wayline.v1",
            requestId=request_id,
        )
        with patch(
            "services.wayline_forge.app.profile_store._server_timestamp",
            return_value=timestamp,
        ):
            return self.service.create_profile(request)

    def _create_session(
        self,
        profile_id: str,
        *,
        request_id: str,
        timestamp: str,
        client_build: str = "mac-demo-0.1.0",
    ) -> SessionCreated:
        request = SessionCreate(
            schemaVersion="wayline.v1",
            requestId=request_id,
            profileId=profile_id,
            clientBuild=client_build,
        )
        with patch(
            "services.wayline_forge.app.profile_store._server_timestamp",
            return_value=timestamp,
        ):
            return self.service.create_session(request)

    def _activate_world(
        self,
        *,
        profile_id: str,
        session_id: str,
        world_index: int,
        occurred_at: str,
        curriculum_receipt: str | None = None,
    ) -> None:
        events = self.store.load_events(profile_id)
        world = self.catalog.worlds[world_index]
        ordinal = len(events) + 1
        self.store.append(
            WorldActivatedEvent(
                schema_version=EVENT_SCHEMA_VERSION,
                event_id=f"world-activation-{ordinal:03d}",
                idempotency_id=f"activate-world-{ordinal:03d}",
                ordinal=ordinal,
                profile_id=profile_id,
                session_id=session_id,
                world_id=world.world_id,
                battle_id="campaign-map",
                occurred_at=occurred_at,
                core_subskill_ids=world.core_subskill_ids,
                curriculum_receipt=(
                    self.catalog.curriculum_receipt
                    if curriculum_receipt is None
                    else curriculum_receipt
                ),
            )
        )

    def test_profile_creation_returns_only_the_strict_public_identity(self) -> None:
        first = self._create_profile()
        replay = self._create_profile()

        self.assertIs(type(first), ProfileCreated)
        self.assertEqual(replay, first)
        self.assertEqual(first.schema_version, "wayline.v1")
        self.assertTrue(first.profile_id.startswith("profile-"))
        self.assertEqual(first.created_at_utc, PROFILE_CREATED_AT)
        self.assertEqual(
            set(first.model_dump(by_alias=True)),
            {"schemaVersion", "profileId", "createdAtUtc"},
        )

    def test_first_session_returns_atomic_initial_world_and_catalog_hash(self) -> None:
        profile = self._create_profile()

        created = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )

        self.assertIs(type(created), SessionCreated)
        self.assertEqual(created.profile_id, profile.profile_id)
        self.assertTrue(created.session_id.startswith("session-"))
        self.assertEqual(created.created_at_utc, FIRST_SESSION_AT)
        self.assertEqual(created.active_world_id, "valuehold")
        self.assertEqual(
            created.campaign_catalog_sha256,
            CAMPAIGN_CATALOG_V1_SHA256,
        )
        self.assertEqual(
            set(created.model_dump(by_alias=True)),
            {
                "schemaVersion",
                "profileId",
                "sessionId",
                "createdAtUtc",
                "activeWorldId",
                "campaignCatalogSha256",
            },
        )

    def test_session_replay_uses_world_at_original_opening_not_current_world(
        self,
    ) -> None:
        profile = self._create_profile()
        first = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )
        self._activate_world(
            profile_id=profile.profile_id,
            session_id=first.session_id,
            world_index=1,
            occurred_at=DECIMARA_ACTIVATED_AT,
        )
        second = self._create_session(
            profile.profile_id,
            request_id="session-request-002",
            timestamp=SECOND_SESSION_AT,
        )
        self.assertEqual(second.active_world_id, "decimara")

        self._activate_world(
            profile_id=profile.profile_id,
            session_id=second.session_id,
            world_index=2,
            occurred_at=FRACTURE_ACTIVATED_AT,
        )
        replay = self._create_session(
            profile.profile_id,
            request_id="session-request-002",
            timestamp=THIRD_SESSION_AT,
        )
        current = self._create_session(
            profile.profile_id,
            request_id="session-request-003",
            timestamp=THIRD_SESSION_AT,
        )

        self.assertEqual(replay, second)
        self.assertEqual(replay.created_at_utc, SECOND_SESSION_AT)
        self.assertEqual(replay.active_world_id, "decimara")
        self.assertEqual(current.active_world_id, "fracture_isles")

    def test_session_replay_is_immutable_after_a_valid_backdated_activation(
        self,
    ) -> None:
        profile = self._create_profile()
        first = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )
        second = self._create_session(
            profile.profile_id,
            request_id="session-request-002",
            timestamp=SECOND_SESSION_AT,
        )
        self.assertEqual(second.active_world_id, "valuehold")

        self._activate_world(
            profile_id=profile.profile_id,
            session_id=first.session_id,
            world_index=1,
            occurred_at=DECIMARA_ACTIVATED_AT,
        )
        replay = self._create_session(
            profile.profile_id,
            request_id="session-request-002",
            timestamp=THIRD_SESSION_AT,
        )

        self.assertEqual(replay, second)
        self.assertEqual(replay.active_world_id, "valuehold")

    def test_activation_at_noninitial_session_opening_does_not_rewrite_snapshot(
        self,
    ) -> None:
        profile = self._create_profile()
        first = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )
        self._activate_world(
            profile_id=profile.profile_id,
            session_id=first.session_id,
            world_index=1,
            occurred_at=DECIMARA_ACTIVATED_AT,
        )
        second = self._create_session(
            profile.profile_id,
            request_id="session-request-002",
            timestamp=SECOND_SESSION_AT,
        )
        self._activate_world(
            profile_id=profile.profile_id,
            session_id=second.session_id,
            world_index=2,
            occurred_at=SECOND_SESSION_AT,
        )

        replay = self._create_session(
            profile.profile_id,
            request_id="session-request-002",
            timestamp=THIRD_SESSION_AT,
        )
        current = self._create_session(
            profile.profile_id,
            request_id="session-request-003",
            timestamp=THIRD_SESSION_AT,
        )

        self.assertEqual(replay, second)
        self.assertEqual(replay.active_world_id, "decimara")
        self.assertEqual(current.active_world_id, "fracture_isles")

    def test_modified_campaign_activation_fails_closed(self) -> None:
        profile = self._create_profile()
        session = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )
        self._activate_world(
            profile_id=profile.profile_id,
            session_id=session.session_id,
            world_index=1,
            occurred_at=DECIMARA_ACTIVATED_AT,
            curriculum_receipt="wayline-campaign-v1:modified",
        )

        with self.assertRaises(IdentityLifecycleError) as caught:
            self._create_session(
                profile.profile_id,
                request_id="session-request-002",
                timestamp=SECOND_SESSION_AT,
            )

        self.assertEqual(caught.exception.code, "catalog_conflict")

    def test_catalog_failure_before_new_session_has_no_durable_side_effects(
        self,
    ) -> None:
        profile = self._create_profile()
        first = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )
        self._activate_world(
            profile_id=profile.profile_id,
            session_id=first.session_id,
            world_index=1,
            occurred_at=DECIMARA_ACTIVATED_AT,
            curriculum_receipt="wayline-campaign-v1:modified",
        )
        sessions_before = tuple(
            self.store._connection.execute(
                "SELECT * FROM local_sessions ORDER BY session_id"
            ).fetchall()
        )
        receipts_before = tuple(
            self.store._connection.execute(
                "SELECT * FROM identity_command_receipts ORDER BY request_id"
            ).fetchall()
        )
        events_before = self.store.load_events(profile.profile_id)

        with self.assertRaises(IdentityLifecycleError) as caught:
            self._create_session(
                profile.profile_id,
                request_id="session-request-002",
                timestamp=SECOND_SESSION_AT,
            )

        self.assertEqual(caught.exception.code, "catalog_conflict")
        self.assertEqual(
            tuple(
                self.store._connection.execute(
                    "SELECT * FROM local_sessions ORDER BY session_id"
                ).fetchall()
            ),
            sessions_before,
        )
        self.assertEqual(
            tuple(
                self.store._connection.execute(
                    "SELECT * FROM identity_command_receipts ORDER BY request_id"
                ).fetchall()
            ),
            receipts_before,
        )
        self.assertEqual(self.store.load_events(profile.profile_id), events_before)
        self.assertEqual(
            self.store.load_open_session(profile.profile_id).session_id,
            first.session_id,
        )

    def test_historical_replay_validates_later_activation_session_ownership(
        self,
    ) -> None:
        profile = self._create_profile()
        session = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )
        self._activate_world(
            profile_id=profile.profile_id,
            session_id=session.session_id,
            world_index=1,
            occurred_at=DECIMARA_ACTIVATED_AT,
        )

        other_profile = self._create_profile(
            request_id="profile-request-002",
            timestamp="2026-07-11T12:00:30.000000Z",
        )
        other_session = self._create_session(
            other_profile.profile_id,
            request_id="session-request-other",
            timestamp="2026-07-11T12:01:30.000000Z",
        )
        self._activate_world(
            profile_id=profile.profile_id,
            session_id=other_session.session_id,
            world_index=2,
            occurred_at=FRACTURE_ACTIVATED_AT,
        )

        with self.assertRaises(IdentityLifecycleError) as caught:
            self._create_session(
                profile.profile_id,
                request_id="session-request-new",
                timestamp=THIRD_SESSION_AT,
            )

        self.assertEqual(caught.exception.code, "catalog_conflict")

    def test_historical_replay_rejects_nonactivation_from_another_profile_session(
        self,
    ) -> None:
        profile = self._create_profile()
        session = self._create_session(
            profile.profile_id,
            request_id="session-request-001",
            timestamp=FIRST_SESSION_AT,
        )
        other_profile = self._create_profile(
            request_id="profile-request-002",
            timestamp="2026-07-11T12:00:30.000000Z",
        )
        other_session = self._create_session(
            other_profile.profile_id,
            request_id="session-request-other",
            timestamp="2026-07-11T12:01:30.000000Z",
        )
        self.store.append(
            BattleOutcomeEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="battle-outcome-cross-profile-002",
                idempotency_id="battle-outcome-cross-profile-request-002",
                ordinal=2,
                profile_id=profile.profile_id,
                session_id=other_session.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                occurred_at="2026-07-11T12:02:00.000000Z",
                won=True,
                is_lead_in=True,
            )
        )

        with self.assertRaises(IdentityLifecycleError) as caught:
            self._create_session(
                profile.profile_id,
                request_id="session-request-new",
                timestamp=SECOND_SESSION_AT,
            )

        self.assertEqual(caught.exception.code, "catalog_conflict")
        self.assertEqual(session.active_world_id, "valuehold")

    def test_unexpected_store_failure_is_redacted_as_integrity_failure(self) -> None:
        class ExplodingStore:
            def create_profile(self, *, request_id: str) -> object:
                raise RuntimeError(f"private storage detail for {request_id}")

        service = IdentityLifecycleService(ExplodingStore())  # type: ignore[arg-type]
        request = ProfileCreate(
            schemaVersion="wayline.v1",
            requestId="profile-request-unsafe",
        )

        with self.assertRaises(IdentityLifecycleError) as caught:
            service.create_profile(request)

        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(str(caught.exception), "integrity_failure")
        self.assertNotIn("private", str(caught.exception))

    def test_profile_creation_rejects_duck_typed_request_before_storage(self) -> None:
        class ProfileCreateLookalike:
            schema_version = "wayline.v1"
            request_id = "profile-request-duck-secret"

        with self.assertRaises(IdentityLifecycleError) as caught:
            self.service.create_profile(ProfileCreateLookalike())  # type: ignore[arg-type]

        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(str(caught.exception), "integrity_failure")
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM local_profiles"
            ).fetchone()[0],
            0,
        )

    def test_profile_creation_revalidates_exact_model_construct_instances(self) -> None:
        extra = ProfileCreate.model_construct(
            schema_version="wayline.v1",
            request_id="profile-request-extra-field",
        )
        extra.__dict__["unexpected"] = "private-extra"
        requests = (
            ProfileCreate.model_construct(
                schema_version="wayline.invalid",
                request_id="profile-request-invalid-schema",
            ),
            ProfileCreate.model_construct(schema_version="wayline.v1"),
            extra,
        )

        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(IdentityLifecycleError) as caught:
                    self.service.create_profile(request)
                self.assertEqual(caught.exception.code, "integrity_failure")

        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM local_profiles"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM identity_command_receipts"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM identity_command_receipts"
            ).fetchone()[0],
            0,
        )

    def test_session_creation_rejects_duck_typed_request_before_storage(self) -> None:
        profile = self._create_profile()

        class SessionCreateLookalike:
            schema_version = "wayline.v1"
            request_id = "session-request-duck-secret"
            profile_id = profile.profile_id
            client_build = "mac-demo-0.1.0"

        receipts_before = self.store._connection.execute(
            "SELECT COUNT(*) FROM identity_command_receipts"
        ).fetchone()[0]

        with self.assertRaises(IdentityLifecycleError) as caught:
            self.service.create_session(SessionCreateLookalike())  # type: ignore[arg-type]

        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(str(caught.exception), "integrity_failure")
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(self.store.load_open_session(profile.profile_id))
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM identity_command_receipts"
            ).fetchone()[0],
            receipts_before,
        )

    def test_session_creation_revalidates_exact_model_construct_instances(self) -> None:
        profile = self._create_profile()
        extra = SessionCreate.model_construct(
            schema_version="wayline.v1",
            request_id="session-request-extra-field",
            profile_id=profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        extra.__dict__["unexpected"] = "private-extra"
        requests = (
            SessionCreate.model_construct(
                schema_version="wayline.invalid",
                request_id="session-request-invalid-schema",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            ),
            SessionCreate.model_construct(
                schema_version="wayline.v1",
                request_id="session-request-missing-build",
                profile_id=profile.profile_id,
            ),
            extra,
        )
        receipts_before = self.store._connection.execute(
            "SELECT COUNT(*) FROM identity_command_receipts"
        ).fetchone()[0]

        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(IdentityLifecycleError) as caught:
                    self.service.create_session(request)
                self.assertEqual(caught.exception.code, "integrity_failure")

        self.assertIsNone(self.store.load_open_session(profile.profile_id))
        self.assertEqual(
            self.store._connection.execute(
                "SELECT COUNT(*) FROM identity_command_receipts"
            ).fetchone()[0],
            receipts_before,
        )

    def test_missing_profile_and_request_reuse_have_typed_failures(self) -> None:
        missing = SessionCreate(
            schemaVersion="wayline.v1",
            requestId="session-request-missing",
            profileId="profile-does-not-exist",
            clientBuild="mac-demo-0.1.0",
        )
        with self.assertRaises(IdentityLifecycleError) as missing_error:
            self.service.create_session(missing)
        self.assertEqual(missing_error.exception.code, "profile_not_found")

        profile = self._create_profile(request_id="shared-request-001")
        conflict = SessionCreate(
            schemaVersion="wayline.v1",
            requestId="shared-request-001",
            profileId=profile.profile_id,
            clientBuild="mac-demo-0.1.0",
        )
        with self.assertRaises(IdentityLifecycleError) as conflict_error:
            self.service.create_session(conflict)
        self.assertEqual(conflict_error.exception.code, "idempotency_conflict")


if __name__ == "__main__":
    unittest.main()
