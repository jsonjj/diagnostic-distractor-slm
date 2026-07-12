from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from services.wayline_forge.app.campaign_catalog import (
    CAMPAIGN_CATALOG_V1_SHA256,
)
from services.wayline_forge.app.events import (
    BattleOutcomeEvent,
    EVENT_SCHEMA_VERSION,
    OUTCOME_EVENT_SCHEMA_VERSION,
    WorldActivatedEvent,
    canonical_event_json,
    compute_event_hash,
    event_from_json,
)
from services.wayline_forge.app.profile_store import (
    CampaignStateConflictError,
    EventLogCorruptionError,
    IdempotencyConflictError,
    IdentityStoreCorruptionError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
)


class ProfileIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "profiles.sqlite"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _campaign_api(self):
        try:
            module = importlib.import_module(
                "services.wayline_forge.app.campaign_catalog"
            )
        except ModuleNotFoundError:
            self.fail("campaign_catalog runtime is not implemented")
        return module

    def _require_battle_catalog_api(self):
        module = self._campaign_api()
        self.assertTrue(
            hasattr(module, "CampaignBattle"),
            "campaign battle authority is not implemented",
        )
        self.assertTrue(
            hasattr(module.CampaignCatalog, "require_battle"),
            "campaign battle validation is not implemented",
        )
        return module

    def _require_identity_api(self):
        required = (
            "create_profile",
            "create_session",
            "load_profile",
            "load_session",
            "load_open_session",
        )
        missing = [name for name in required if not hasattr(ProfileStore, name)]
        self.assertEqual(missing, [], f"missing profile identity API: {missing}")
        self.assertNotIn(
            "client_build",
            inspect.signature(ProfileStore.create_profile).parameters,
            "profile creation must not collect client build metadata",
        )

    @staticmethod
    def _canonical_json(payload):
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def _replace_receipt_timestamp(
        self,
        store,
        *,
        request_id,
        original_json,
        field,
        value,
    ):
        payload = json.loads(original_json)
        payload[field] = value
        modified = self._canonical_json(payload)
        digest = hashlib.sha256(modified.encode("utf-8")).hexdigest()
        store._connection.execute(
            "UPDATE identity_command_receipts "
            "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
            (modified, digest, request_id),
        )
        store._connection.commit()

    def _downgrade_identity_database_to_v3(
        self,
        path: Path | None = None,
    ) -> None:
        database_path = self.path if path is None else path
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("DROP TABLE IF EXISTS session_opening_log")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(local_sessions)")
            }
            for column in (
                "active_world_id",
                "campaign_catalog_sha256",
                "event_ordinal_at_opening",
                "event_hash_at_opening",
            ):
                if column in columns:
                    connection.execute(f"ALTER TABLE local_sessions DROP COLUMN {column}")
            connection.execute(
                "UPDATE local_sessions SET schema_version = ?",
                ("wayline.local-session.v1",),
            )
            for request_id, response_json in connection.execute(
                "SELECT request_id, response_json FROM identity_command_receipts "
                "WHERE action = 'create_session'"
            ).fetchall():
                payload = json.loads(response_json)
                payload["schema_version"] = "wayline.local-session.v1"
                payload.pop("active_world_id", None)
                payload.pop("campaign_catalog_sha256", None)
                payload.pop("event_ordinal_at_opening", None)
                payload.pop("event_hash_at_opening", None)
                legacy_json = self._canonical_json(payload)
                connection.execute(
                    "UPDATE identity_command_receipts "
                    "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                    (
                        legacy_json,
                        hashlib.sha256(legacy_json.encode("utf-8")).hexdigest(),
                        request_id,
                    ),
                )
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _downgrade_identity_database_to_v4(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute("DROP TABLE IF EXISTS session_opening_log")
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _opening_hash(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _remove_identity_receipt_action_check(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "ALTER TABLE identity_command_receipts "
                "RENAME TO identity_command_receipts_with_check"
            )
            connection.execute(
                """
                CREATE TABLE identity_command_receipts (
                    request_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    session_id TEXT,
                    response_json TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO identity_command_receipts (
                    request_id, action, payload_sha256, profile_id, session_id,
                    response_json, response_sha256
                )
                SELECT request_id, action, payload_sha256, profile_id, session_id,
                       response_json, response_sha256
                FROM identity_command_receipts_with_check
                """
            )
            connection.execute("DROP TABLE identity_command_receipts_with_check")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _compact_identity_receipt_action_check(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "ALTER TABLE identity_command_receipts "
                "RENAME TO identity_command_receipts_spaced_check"
            )
            connection.execute(
                """
                CREATE TABLE identity_command_receipts (
                    request_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL
                        CHECK(action IN('create_profile','create_session')),
                    payload_sha256 TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    session_id TEXT,
                    response_json TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO identity_command_receipts (
                    request_id, action, payload_sha256, profile_id, session_id,
                    response_json, response_sha256
                )
                SELECT request_id, action, payload_sha256, profile_id, session_id,
                       response_json, response_sha256
                FROM identity_command_receipts_spaced_check
                """
            )
            connection.execute("DROP TABLE identity_command_receipts_spaced_check")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _drift_identity_receipt_action_literal(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "ALTER TABLE identity_command_receipts "
                "RENAME TO identity_command_receipts_valid_actions"
            )
            connection.execute(
                """
                CREATE TABLE identity_command_receipts (
                    request_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL
                        CHECK (action IN ('create _profile', 'create_session')),
                    payload_sha256 TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    session_id TEXT,
                    response_json TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO identity_command_receipts (
                    request_id, action, payload_sha256, profile_id, session_id,
                    response_json, response_sha256
                )
                SELECT request_id, action, payload_sha256, profile_id, session_id,
                       response_json, response_sha256
                FROM identity_command_receipts_valid_actions
                """
            )
            connection.execute("DROP TABLE identity_command_receipts_valid_actions")
            connection.commit()
        finally:
            connection.close()

    def test_packaged_campaign_catalog_is_hash_verified_and_starts_in_valuehold(self):
        module = self._campaign_api()

        catalog = module.CampaignCatalog.packaged_v1()

        self.assertRegex(module.CAMPAIGN_CATALOG_V1_SHA256, r"^[0-9a-f]{64}$")
        self.assertEqual(catalog.initial_world.world_id, "valuehold")
        self.assertEqual(
            catalog.initial_world.core_subskill_ids,
            ("place_value", "mental_add_sub"),
        )
        self.assertEqual(
            catalog.curriculum_receipt,
            f"{catalog.catalog_id}:{module.CAMPAIGN_CATALOG_V1_SHA256}",
        )
        self.assertEqual(len(catalog.worlds), 9)
        self.assertEqual(len({world.world_id for world in catalog.worlds}), 9)
        self.assertEqual(
            tuple(world.sequence for world in catalog.worlds),
            tuple(range(1, 10)),
        )

    def test_campaign_catalog_rejects_modified_or_duplicate_key_resources(self):
        module = self._campaign_api()
        packaged = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "campaign_catalog_v1.json"
        )
        temporary = Path(self.temporary_directory.name)

        modified = temporary / "modified-campaign.json"
        payload = json.loads(packaged.read_text(encoding="utf-8"))
        payload["initial_world_id"] = "decimara"
        modified.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(module.CampaignCatalogError):
            module.CampaignCatalog.packaged_v1(resource_path=modified)

        duplicate = temporary / "duplicate-campaign.json"
        original = packaged.read_text(encoding="utf-8")
        duplicate.write_text(
            original.replace(
                '"catalog_id":',
                '"catalog_id": "duplicate", "catalog_id":',
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaises(module.CampaignCatalogError):
            module.CampaignCatalog.load(duplicate)

    def test_valuehold_battle_schedule_is_exact_and_server_derived(self):
        module = self._require_battle_catalog_api()

        catalog = module.CampaignCatalog.packaged_v1()
        battles = catalog.initial_world.battles

        self.assertEqual(
            tuple(battle.battle_id for battle in battles),
            (
                "valuehold_route_1",
                "valuehold_route_2",
                "valuehold_route_3",
                "valuehold_elite",
                "valuehold_boss",
            ),
        )
        self.assertEqual(tuple(battle.sequence for battle in battles), (1, 2, 3, 4, 5))
        self.assertEqual(
            tuple(battle.tier for battle in battles),
            ("route_1", "route_2", "route_3", "elite", "world_boss"),
        )
        self.assertEqual(tuple(battle.item_count for battle in battles), (3, 4, 4, 5, 8))
        self.assertEqual(
            tuple(battle.is_lead_in for battle in battles),
            (True, True, True, True, False),
        )
        self.assertEqual(
            tuple(battle.is_boss for battle in battles),
            (False, False, False, False, True),
        )

    def test_every_world_has_five_battles_and_finale_uses_ten_items(self):
        module = self._require_battle_catalog_api()

        catalog = module.CampaignCatalog.packaged_v1()

        for world in catalog.worlds:
            with self.subTest(world_id=world.world_id):
                self.assertEqual(len(world.battles), 5)
                self.assertEqual(
                    tuple(battle.sequence for battle in world.battles),
                    (1, 2, 3, 4, 5),
                )
                self.assertEqual(
                    tuple(battle.item_count for battle in world.battles[:4]),
                    (3, 4, 4, 5),
                )
                self.assertTrue(all(battle.is_lead_in for battle in world.battles[:4]))
                self.assertTrue(world.battles[-1].is_boss)
        finale = catalog.worlds[-1].battles[-1]
        self.assertEqual(finale.battle_id, "order_spire_boss")
        self.assertEqual(finale.tier, "campaign_finale")
        self.assertEqual(finale.item_count, 10)

    def test_catalog_rejects_unknown_cross_world_or_out_of_order_battle_fields(self):
        module = self._require_battle_catalog_api()
        catalog = module.CampaignCatalog.packaged_v1()

        accepted = catalog.require_battle(
            world_id="valuehold",
            battle_id="valuehold_route_1",
            battle_tier="route_1",
            expected_world_sequence=1,
            expected_battle_sequence=1,
        )

        self.assertEqual(accepted.item_count, 3)
        invalid = (
            {
                "world_id": "missing_world",
                "battle_id": "missing_world_route_1",
                "battle_tier": "route_1",
                "expected_world_sequence": 1,
                "expected_battle_sequence": 1,
            },
            {
                "world_id": "valuehold",
                "battle_id": "missing_battle",
                "battle_tier": "route_1",
                "expected_world_sequence": 1,
                "expected_battle_sequence": 1,
            },
            {
                "world_id": "valuehold",
                "battle_id": "valuehold_route_1",
                "battle_tier": "elite",
                "expected_world_sequence": 1,
                "expected_battle_sequence": 1,
            },
            {
                "world_id": "valuehold",
                "battle_id": "decimara_route_1",
                "battle_tier": "route_1",
                "expected_world_sequence": 1,
                "expected_battle_sequence": 1,
            },
            {
                "world_id": "valuehold",
                "battle_id": "valuehold_route_2",
                "battle_tier": "route_2",
                "expected_world_sequence": 1,
                "expected_battle_sequence": 1,
            },
            {
                "world_id": "decimara",
                "battle_id": "decimara_route_1",
                "battle_tier": "route_1",
                "expected_world_sequence": 1,
                "expected_battle_sequence": 1,
            },
        )
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(
                module.CampaignCatalogError
            ):
                catalog.require_battle(**fields)

    def test_campaign_loader_rejects_tampered_battle_schedule_before_use(self):
        module = self._require_battle_catalog_api()
        packaged = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "campaign_catalog_v1.json"
        )
        payload = json.loads(packaged.read_text(encoding="utf-8"))
        self.assertIn("battles", payload["worlds"][0])
        payload["worlds"][0]["battles"][0]["sequence"] = 2
        modified = Path(self.temporary_directory.name) / "bad-battle-order.json"
        modified.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(module.CampaignCatalogError):
            module.CampaignCatalog.load(modified)
        with self.assertRaises(module.CampaignCatalogError):
            module.CampaignCatalog.packaged_v1(resource_path=modified)

    def test_profile_is_server_minted_durable_and_exactly_idempotent(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            first = store.create_profile(
                request_id="profile-request-001",
            )
            replay = store.create_profile(
                request_id="profile-request-001",
            )

            self.assertEqual(first, replay)
            self.assertEqual(store.load_profile(first.profile_id), first)
            self.assertTrue(first.profile_id.startswith("profile-"))
            self.assertNotEqual(first.profile_id, "profile-request-001")
            self.assertTrue(first.created_at.endswith("Z"))
            self.assertNotIn("+00:00", first.created_at)
            self.assertNotIn("display_name", asdict(first))
            self.assertNotIn("client_build", asdict(first))
            columns = {
                row[1]
                for row in store._connection.execute(
                    "PRAGMA table_info(local_profiles)"
                )
            }
            self.assertNotIn("display_name", columns)
            self.assertNotIn("client_build", columns)

        with ProfileStore(self.path) as restarted:
            replay_after_restart = restarted.create_profile(
                request_id="profile-request-001",
            )
            self.assertEqual(replay_after_restart, first)
            self.assertEqual(
                restarted._connection.execute(
                    "SELECT COUNT(*) FROM local_profiles"
                ).fetchone()[0],
                1,
            )

    def test_profile_request_id_cannot_be_reused_for_a_session_payload(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            original = store.create_profile(
                request_id="profile-request-001",
            )

            with self.assertRaises(IdempotencyConflictError):
                store.create_session(
                    request_id="profile-request-001",
                    profile_id=original.profile_id,
                    client_build="mac-demo-0.2.0",
                )

            self.assertEqual(store.load_profile(original.profile_id), original)
            digest = store._connection.execute(
                "SELECT payload_sha256 FROM identity_command_receipts "
                "WHERE request_id = ?",
                ("profile-request-001",),
            ).fetchone()[0]
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_identity_rows_reject_noncanonical_or_impossible_utc_timestamps(self):
        self._require_identity_api()
        invalid = (
            "2026-02-30T10:20:30Z",
            "2026-07-11T10:20:30+00:00",
            "2026-07-11T10:20:30.123Z",
        )
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            first = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            second = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            original = {
                ("local_profiles", "created_at", "profile_id", profile.profile_id): (
                    profile.created_at,
                    lambda: store.load_profile(profile.profile_id),
                ),
                ("local_sessions", "opened_at", "session_id", first.session_id): (
                    first.opened_at,
                    lambda: store.load_session(first.session_id),
                ),
                ("local_sessions", "closed_at", "session_id", first.session_id): (
                    second.opened_at,
                    lambda: store.load_session(first.session_id),
                ),
            }

            for (table, column, key, identifier), (valid, load) in original.items():
                for value in invalid:
                    with self.subTest(table=table, column=column, value=value):
                        store._connection.execute(
                            f"UPDATE {table} SET {column} = ? WHERE {key} = ?",
                            (value, identifier),
                        )
                        store._connection.commit()
                        with self.assertRaisesRegex(
                            IdentityStoreCorruptionError,
                            "canonical UTC",
                        ):
                            load()
                        store._connection.execute(
                            f"UPDATE {table} SET {column} = ? WHERE {key} = ?",
                            (valid, identifier),
                        )
                        store._connection.commit()

    def test_identity_receipts_reject_noncanonical_or_impossible_timestamps(self):
        self._require_identity_api()
        invalid = (
            "2026-02-30T10:20:30Z",
            "2026-07-11T10:20:30+00:00",
            "2026-07-11T10:20:30.123Z",
        )
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            receipts = {
                "profile-request-001": store._connection.execute(
                    "SELECT response_json FROM identity_command_receipts "
                    "WHERE request_id = 'profile-request-001'"
                ).fetchone()[0],
                "session-request-001": store._connection.execute(
                    "SELECT response_json FROM identity_command_receipts "
                    "WHERE request_id = 'session-request-001'"
                ).fetchone()[0],
            }
            cases = (
                (
                    "profile-request-001",
                    "created_at",
                    lambda: store.create_profile(request_id="profile-request-001"),
                ),
                (
                    "session-request-001",
                    "opened_at",
                    lambda: store.create_session(
                        request_id="session-request-001",
                        profile_id=profile.profile_id,
                        client_build="mac-demo-0.1.0",
                    ),
                ),
                (
                    "session-request-001",
                    "closed_at",
                    lambda: store.create_session(
                        request_id="session-request-001",
                        profile_id=profile.profile_id,
                        client_build="mac-demo-0.1.0",
                    ),
                ),
            )

            for request_id, field, replay in cases:
                original_json = receipts[request_id]
                for value in invalid:
                    with self.subTest(request_id=request_id, field=field, value=value):
                        self._replace_receipt_timestamp(
                            store,
                            request_id=request_id,
                            original_json=original_json,
                            field=field,
                            value=value,
                        )
                        with self.assertRaisesRegex(
                            IdentityStoreCorruptionError,
                            "canonical UTC",
                        ):
                            replay()
                        original_digest = hashlib.sha256(
                            original_json.encode("utf-8")
                        ).hexdigest()
                        store._connection.execute(
                            "UPDATE identity_command_receipts "
                            "SET response_json = ?, response_sha256 = ? "
                            "WHERE request_id = ?",
                            (original_json, original_digest, request_id),
                        )
                        store._connection.commit()

    def test_session_for_nonexistent_profile_fails_without_durable_side_effects(self):
        self._require_identity_api()
        module = importlib.import_module(
            "services.wayline_forge.app.profile_store"
        )
        self.assertTrue(hasattr(module, "ProfileNotFoundError"))
        with ProfileStore(self.path) as store:
            with self.assertRaises(module.ProfileNotFoundError):
                store.create_session(
                    request_id="session-request-001",
                    profile_id="profile-does-not-exist",
                    client_build="mac-demo-0.1.0",
                )

            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM local_sessions"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                store._connection.execute("SELECT COUNT(*) FROM event_log").fetchone()[0],
                0,
            )
            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM identity_command_receipts "
                    "WHERE action = 'create_session'"
                ).fetchone()[0],
                0,
            )

    def test_first_session_atomically_activates_valuehold_exactly_once(self):
        self._require_identity_api()
        campaign = self._campaign_api().CampaignCatalog.packaged_v1()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(
                request_id="profile-request-001",
            )
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            replay = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

            self.assertEqual(replay, session)
            self.assertEqual(store.load_open_session(profile.profile_id), session)
            events = store.load_events(profile.profile_id)
            self.assertEqual(len(events), 1)
            activation = events[0]
            self.assertIsInstance(activation, WorldActivatedEvent)
            self.assertEqual(activation.ordinal, 1)
            self.assertEqual(activation.profile_id, profile.profile_id)
            self.assertEqual(activation.session_id, session.session_id)
            self.assertEqual(activation.world_id, "valuehold")
            self.assertEqual(activation.battle_id, "campaign-map")
            self.assertEqual(
                activation.core_subskill_ids,
                ("place_value", "mental_add_sub"),
            )
            self.assertEqual(
                activation.curriculum_receipt,
                campaign.curriculum_receipt,
            )
            state = store.load_state(profile.profile_id)
            self.assertEqual(state.active_world_id, "valuehold")
            self.assertEqual(
                state.world("valuehold").core_subskill_ids,
                ("place_value", "mental_add_sub"),
            )

        with ProfileStore(self.path) as restarted:
            replay_after_restart = restarted.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            self.assertEqual(replay_after_restart, session)
            self.assertEqual(len(restarted.load_events(profile.profile_id)), 1)

    def test_session_row_and_receipt_persist_the_campaign_snapshot(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            stored_fields = asdict(session)
            receipt_fields = json.loads(
                store._connection.execute(
                    "SELECT response_json FROM identity_command_receipts "
                    "WHERE request_id = 'session-request-001'"
                ).fetchone()[0]
            )
            columns = {
                row[1]
                for row in store._connection.execute(
                    "PRAGMA table_info(local_sessions)"
                )
            }

            self.assertEqual(stored_fields.get("active_world_id"), "valuehold")
            self.assertEqual(
                stored_fields.get("campaign_catalog_sha256"),
                CAMPAIGN_CATALOG_V1_SHA256,
            )
            self.assertEqual(receipt_fields, stored_fields)
            self.assertIn("active_world_id", columns)
            self.assertIn("campaign_catalog_sha256", columns)
            self.assertEqual(stored_fields.get("event_ordinal_at_opening"), 0)
            self.assertEqual(stored_fields.get("event_hash_at_opening"), "0" * 64)
            self.assertIn("event_ordinal_at_opening", columns)
            self.assertIn("event_hash_at_opening", columns)

    def test_first_session_replay_rejects_coherent_noninitial_world_tampering(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            receipt_json = store._connection.execute(
                "SELECT response_json FROM identity_command_receipts "
                "WHERE request_id = 'session-request-001'"
            ).fetchone()[0]
            receipt = json.loads(receipt_json)
            receipt["active_world_id"] = "decimara"
            modified = self._canonical_json(receipt)
            store._connection.execute(
                "UPDATE local_sessions SET active_world_id = ? WHERE session_id = ?",
                ("decimara", session.session_id),
            )
            store._connection.execute(
                "UPDATE identity_command_receipts "
                "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                (
                    modified,
                    hashlib.sha256(modified.encode("utf-8")).hexdigest(),
                    "session-request-001",
                ),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

    def test_later_session_replay_rejects_coherent_older_world_tampering(self):
        campaign = self._campaign_api().CampaignCatalog.packaged_v1()
        with ProfileStore(self.path) as store:
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:00:00.000000Z",
            ):
                profile = store.create_profile(request_id="profile-request-001")
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:01:00.000000Z",
            ):
                first = store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )
            decimara = campaign.worlds[1]
            store.append(
                WorldActivatedEvent(
                    schema_version=EVENT_SCHEMA_VERSION,
                    event_id="world-activation-002",
                    idempotency_id="activate-world-002",
                    ordinal=2,
                    profile_id=profile.profile_id,
                    session_id=first.session_id,
                    world_id=decimara.world_id,
                    battle_id="campaign-map",
                    occurred_at="2026-07-11T12:02:00.000000Z",
                    core_subskill_ids=decimara.core_subskill_ids,
                    curriculum_receipt=campaign.curriculum_receipt,
                )
            )
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:03:00.000000Z",
            ):
                second = store.create_session(
                    request_id="session-request-002",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )
            exact_replay = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            self.assertEqual(exact_replay, second)
            self.assertEqual(second.active_world_id, "decimara")
            self.assertEqual(second.event_ordinal_at_opening, 2)
            boundary_hash = store._connection.execute(
                "SELECT event_hash FROM event_log "
                "WHERE profile_id = ? AND ordinal = ?",
                (profile.profile_id, second.event_ordinal_at_opening),
            ).fetchone()[0]
            self.assertEqual(second.event_hash_at_opening, boundary_hash)

            receipt_json = store._connection.execute(
                "SELECT response_json FROM identity_command_receipts "
                "WHERE request_id = 'session-request-002'"
            ).fetchone()[0]
            receipt = json.loads(receipt_json)
            earlier_hash = store._connection.execute(
                "SELECT event_hash FROM event_log "
                "WHERE profile_id = ? AND ordinal = 1",
                (profile.profile_id,),
            ).fetchone()[0]
            receipt["active_world_id"] = "valuehold"
            receipt["event_ordinal_at_opening"] = 1
            receipt["event_hash_at_opening"] = earlier_hash
            modified = self._canonical_json(receipt)
            store._connection.execute(
                "UPDATE local_sessions "
                "SET active_world_id = ?, event_ordinal_at_opening = ?, "
                "event_hash_at_opening = ? WHERE session_id = ?",
                ("valuehold", 1, earlier_hash, second.session_id),
            )
            store._connection.execute(
                "UPDATE identity_command_receipts "
                "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                (
                    modified,
                    hashlib.sha256(modified.encode("utf-8")).hexdigest(),
                    "session-request-002",
                ),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.create_session(
                    request_id="session-request-002",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

    def test_replay_rejects_rehashed_invalid_nonactivation_boundary_event(self):
        with ProfileStore(self.path) as store:
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:00:00.000000Z",
            ):
                profile = store.create_profile(request_id="profile-request-001")
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:01:00.000000Z",
            ):
                first = store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )
            outcome = BattleOutcomeEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="battle-outcome-002",
                idempotency_id="battle-outcome-request-002",
                ordinal=2,
                profile_id=profile.profile_id,
                session_id=first.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                occurred_at="2026-07-11T12:02:00.000000Z",
                won=True,
                is_lead_in=True,
            )
            store.append(outcome)
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:03:00.000000Z",
            ):
                second = store.create_session(
                    request_id="session-request-002",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

            payload = json.loads(canonical_event_json(outcome))
            payload["world_id"] = "decimara"
            tampered_json = self._canonical_json(payload)
            tampered_event = event_from_json(tampered_json)
            prior_hash = store._connection.execute(
                "SELECT event_hash FROM event_log "
                "WHERE profile_id = ? AND ordinal = 1",
                (profile.profile_id,),
            ).fetchone()[0]
            tampered_hash = compute_event_hash(prior_hash, tampered_event)
            store._connection.execute(
                "UPDATE event_log SET canonical_json = ?, event_hash = ? "
                "WHERE profile_id = ? AND ordinal = 2",
                (tampered_json, tampered_hash, profile.profile_id),
            )
            store._connection.execute(
                "UPDATE local_sessions SET event_hash_at_opening = ? "
                "WHERE session_id = ?",
                (tampered_hash, second.session_id),
            )
            receipt_json = store._connection.execute(
                "SELECT response_json FROM identity_command_receipts "
                "WHERE request_id = 'session-request-002'"
            ).fetchone()[0]
            receipt = json.loads(receipt_json)
            receipt["event_hash_at_opening"] = tampered_hash
            modified_receipt = self._canonical_json(receipt)
            store._connection.execute(
                "UPDATE identity_command_receipts "
                "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                (
                    modified_receipt,
                    hashlib.sha256(modified_receipt.encode("utf-8")).hexdigest(),
                    "session-request-002",
                ),
            )
            ledger_exists = store._connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'session_opening_log'"
            ).fetchone()
            if ledger_exists is not None:
                opening = store._connection.execute(
                    "SELECT * FROM session_opening_log WHERE session_id = ?",
                    (second.session_id,),
                ).fetchone()
                opening_payload = {
                    "activeWorldId": opening["active_world_id"],
                    "eventHashAtOpening": tampered_hash,
                    "eventOrdinalAtOpening": opening["event_ordinal_at_opening"],
                    "openedAt": opening["opened_at"],
                    "openingOrdinal": opening["opening_ordinal"],
                    "previousOpeningHash": opening["previous_opening_hash"],
                    "profileId": opening["profile_id"],
                    "sessionId": opening["session_id"],
                }
                store._connection.execute(
                    "UPDATE session_opening_log "
                    "SET event_hash_at_opening = ?, opening_hash = ? "
                    "WHERE session_id = ?",
                    (
                        tampered_hash,
                        self._opening_hash(opening_payload),
                        second.session_id,
                    ),
                )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.create_session(
                    request_id="session-request-002",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

    def test_load_session_verifies_creation_receipt_equality(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "UPDATE local_sessions SET client_build = ? WHERE session_id = ?",
                ("tampered-build-9.9.9", session.session_id),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_session(session.session_id)

    def test_load_open_session_verifies_independent_opening_authority(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            receipt_json = store._connection.execute(
                "SELECT response_json FROM identity_command_receipts "
                "WHERE request_id = 'session-request-001'"
            ).fetchone()[0]
            receipt = json.loads(receipt_json)
            receipt["active_world_id"] = "decimara"
            modified = self._canonical_json(receipt)
            store._connection.execute(
                "UPDATE local_sessions SET active_world_id = ? WHERE session_id = ?",
                ("decimara", session.session_id),
            )
            store._connection.execute(
                "UPDATE identity_command_receipts "
                "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                (
                    modified,
                    hashlib.sha256(modified.encode("utf-8")).hexdigest(),
                    "session-request-001",
                ),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_open_session(profile.profile_id)

    def test_new_session_verifies_prior_session_receipt(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "UPDATE local_sessions SET client_build = ? WHERE session_id = ?",
                ("tampered-build-9.9.9", session.session_id),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.create_session(
                    request_id="session-request-002",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

    def test_verified_session_reads_issue_no_sql_writes(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            statements: list[str] = []
            store._connection.set_trace_callback(statements.append)
            try:
                self.assertEqual(store.load_session(session.session_id), session)
                self.assertEqual(store.load_open_session(profile.profile_id), session)
            finally:
                store._connection.set_trace_callback(None)

            writes = (
                "BEGIN",
                "COMMIT",
                "DELETE",
                "INSERT",
                "PRAGMA",
                "REPLACE",
                "UPDATE",
                "VACUUM",
            )
            self.assertFalse(
                [
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith(writes)
                ]
            )

    def test_authoritative_session_paths_reject_missing_campaign_events(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "DELETE FROM event_log WHERE profile_id = ?",
                (profile.profile_id,),
            )
            store._connection.commit()

            with self.assertRaises(EventLogCorruptionError):
                store.load_session(session.session_id)
            with self.assertRaises(EventLogCorruptionError):
                store.load_open_session(profile.profile_id)
            with self.assertRaises(EventLogCorruptionError):
                store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

    def test_authoritative_load_rejects_missing_profile_receipt(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            current = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "DELETE FROM identity_command_receipts "
                "WHERE profile_id = ? AND action = 'create_profile'",
                (profile.profile_id,),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_session(current.session_id)
            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_open_session(profile.profile_id)

    def test_authoritative_load_rejects_corrupt_sibling_session_receipt(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            current = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            response_json = store._connection.execute(
                "SELECT response_json FROM identity_command_receipts "
                "WHERE request_id = 'session-request-001'"
            ).fetchone()[0]
            response = json.loads(response_json)
            response["client_build"] = "tampered-build-9.9.9"
            tampered_json = self._canonical_json(response)
            store._connection.execute(
                "UPDATE identity_command_receipts "
                "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                (
                    tampered_json,
                    hashlib.sha256(tampered_json.encode("utf-8")).hexdigest(),
                    "session-request-001",
                ),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_session(current.session_id)
            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_open_session(profile.profile_id)

    def test_authoritative_load_rejects_orphaned_session_identity(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute("PRAGMA foreign_keys = OFF")
            store._connection.execute(
                "DELETE FROM local_profiles WHERE profile_id = ?",
                (profile.profile_id,),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_session(session.session_id)

    def test_new_session_validates_each_prior_event_prefix_authority(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            second = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            receipt_json = store._connection.execute(
                "SELECT response_json FROM identity_command_receipts "
                "WHERE request_id = 'session-request-002'"
            ).fetchone()[0]
            receipt = json.loads(receipt_json)
            receipt["event_ordinal_at_opening"] = 0
            receipt["event_hash_at_opening"] = "0" * 64
            modified_receipt = self._canonical_json(receipt)
            store._connection.execute(
                "UPDATE local_sessions SET event_ordinal_at_opening = 0, "
                "event_hash_at_opening = ? WHERE session_id = ?",
                ("0" * 64, second.session_id),
            )
            store._connection.execute(
                "UPDATE identity_command_receipts "
                "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                (
                    modified_receipt,
                    hashlib.sha256(modified_receipt.encode("utf-8")).hexdigest(),
                    "session-request-002",
                ),
            )
            opening = store._connection.execute(
                "SELECT * FROM session_opening_log WHERE session_id = ?",
                (second.session_id,),
            ).fetchone()
            opening_payload = {
                "activeWorldId": opening["active_world_id"],
                "eventHashAtOpening": "0" * 64,
                "eventOrdinalAtOpening": 0,
                "openedAt": opening["opened_at"],
                "openingOrdinal": opening["opening_ordinal"],
                "previousOpeningHash": opening["previous_opening_hash"],
                "profileId": opening["profile_id"],
                "sessionId": opening["session_id"],
            }
            store._connection.execute(
                "UPDATE session_opening_log SET event_ordinal_at_opening = 0, "
                "event_hash_at_opening = ?, opening_hash = ? WHERE session_id = ?",
                (
                    "0" * 64,
                    self._opening_hash(opening_payload),
                    second.session_id,
                ),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.create_session(
                    request_id="session-request-003",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

    def test_v4_migration_validates_full_campaign_history(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            outcome = BattleOutcomeEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="battle-outcome-002",
                idempotency_id="battle-outcome-request-002",
                ordinal=2,
                profile_id=profile.profile_id,
                session_id=session.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                occurred_at=session.opened_at,
                won=True,
                is_lead_in=True,
            )
            store.append(outcome)
            payload = json.loads(canonical_event_json(outcome))
            payload["world_id"] = "decimara"
            tampered_json = self._canonical_json(payload)
            tampered_event = event_from_json(tampered_json)
            prior_hash = store._connection.execute(
                "SELECT event_hash FROM event_log "
                "WHERE profile_id = ? AND ordinal = 1",
                (profile.profile_id,),
            ).fetchone()[0]
            store._connection.execute(
                "UPDATE event_log SET canonical_json = ?, event_hash = ? "
                "WHERE profile_id = ? AND ordinal = 2",
                (
                    tampered_json,
                    compute_event_hash(prior_hash, tampered_event),
                    profile.profile_id,
                ),
            )
            store._connection.commit()

        self._downgrade_identity_database_to_v4(self.path)
        backup = self.path.with_suffix(self.path.suffix + ".backup-v4")

        with self.assertRaises(ProfileStoreError):
            ProfileStore(self.path)

        self.assertTrue(backup.exists())

    def test_authoritative_load_requires_derived_session_closure(self):
        with ProfileStore(self.path) as store:
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:00:00.000000Z",
            ):
                profile = store.create_profile(request_id="profile-request-001")
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:01:00.000000Z",
            ):
                first = store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:02:00.000000Z",
            ):
                second = store.create_session(
                    request_id="session-request-002",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )
            store._connection.execute(
                "UPDATE local_sessions SET closed_at = ? WHERE session_id = ?",
                ("2026-07-11T12:01:30.000000Z", first.session_id),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_session(second.session_id)

    def test_authoritative_load_requires_terminal_session_to_remain_open(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "UPDATE local_sessions SET closed_at = ? WHERE session_id = ?",
                (session.opened_at, session.session_id),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.load_session(session.session_id)

    def test_session_rotation_and_current_session_read_are_linearizable(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

        reader_ready = threading.Event()
        start_reader = threading.Event()
        reader_started = threading.Event()
        reader_finished = threading.Event()
        rotation_inside_transaction = threading.Event()
        release_rotation = threading.Event()
        errors: list[BaseException] = []
        read_sessions = []
        rotated_sessions = []
        original_snapshot = ProfileStore._campaign_snapshot_for_new_session

        def blocking_snapshot(store, durable_profile, *, session_id, opened_at):
            result = original_snapshot(
                store,
                durable_profile,
                session_id=session_id,
                opened_at=opened_at,
            )
            rotation_inside_transaction.set()
            if not release_rotation.wait(5):
                raise RuntimeError("test did not release session rotation")
            return result

        def read_current_session():
            try:
                with ProfileStore(self.path) as store:
                    reader_ready.set()
                    if not start_reader.wait(5):
                        raise RuntimeError("test did not start current-session read")
                    reader_started.set()
                    read_sessions.append(store.load_open_session(profile.profile_id))
                    reader_finished.set()
            except BaseException as exc:
                errors.append(exc)
                reader_finished.set()

        def rotate_session():
            try:
                with ProfileStore(self.path) as store:
                    rotated_sessions.append(
                        store.create_session(
                            request_id="session-request-002",
                            profile_id=profile.profile_id,
                            client_build="mac-demo-0.1.0",
                        )
                    )
            except BaseException as exc:
                errors.append(exc)

        reader = threading.Thread(target=read_current_session)
        rotator = threading.Thread(target=rotate_session)
        reader.start()
        self.assertTrue(reader_ready.wait(5))
        try:
            with patch.object(
                ProfileStore,
                "_campaign_snapshot_for_new_session",
                blocking_snapshot,
            ):
                rotator.start()
                self.assertTrue(rotation_inside_transaction.wait(5))
                start_reader.set()
                self.assertTrue(reader_started.wait(5))
                completed_before_commit = reader_finished.wait(0.2)
        finally:
            release_rotation.set()
            reader.join(5)
            rotator.join(5)

        self.assertFalse(reader.is_alive())
        self.assertFalse(rotator.is_alive())
        self.assertFalse(completed_before_commit)
        self.assertEqual(errors, [])
        self.assertEqual(len(read_sessions), 1)
        self.assertEqual(len(rotated_sessions), 1)
        self.assertEqual(
            read_sessions[0].session_id,
            rotated_sessions[0].session_id,
        )

    def test_first_session_projection_failure_cannot_hide_committed_identity(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")

            with patch.object(
                store,
                "_write_projection",
                side_effect=RuntimeError("injected projection failure"),
            ) as projection_write:
                session = store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

            projection_write.assert_not_called()
            self.assertEqual(store.load_open_session(profile.profile_id), session)
            self.assertEqual(len(store.load_events(profile.profile_id)), 1)
            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM identity_command_receipts "
                    "WHERE request_id = 'session-request-001'"
                ).fetchone()[0],
                1,
            )

    def test_session_receipt_replay_rejects_nonnull_creation_closed_at(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            first = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            second = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            receipt_json = store._connection.execute(
                "SELECT response_json FROM identity_command_receipts "
                "WHERE request_id = 'session-request-001'"
            ).fetchone()[0]
            receipt = json.loads(receipt_json)
            receipt["closed_at"] = second.opened_at
            modified = self._canonical_json(receipt)
            store._connection.execute(
                "UPDATE identity_command_receipts "
                "SET response_json = ?, response_sha256 = ? WHERE request_id = ?",
                (
                    modified,
                    hashlib.sha256(modified.encode("utf-8")).hexdigest(),
                    "session-request-001",
                ),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )
            self.assertEqual(
                store._connection.execute(
                    "SELECT closed_at FROM local_sessions WHERE session_id = ?",
                    (first.session_id,),
                ).fetchone()[0],
                second.opened_at,
            )

    def test_v3_identity_schema_migrates_session_snapshots_without_export_drift(
        self,
    ):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:00:00.000000Z",
            ):
                profile = store.create_profile(request_id="profile-request-001")
            with patch(
                "services.wayline_forge.app.profile_store._server_timestamp",
                return_value="2026-07-11T12:01:00.000000Z",
            ):
                first = store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )
            export_before = store.export_profile(profile.profile_id)

        self._downgrade_identity_database_to_v3()

        with ProfileStore(self.path) as migrated:
            migrated_first = migrated.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

            self.assertEqual(asdict(migrated_first).get("active_world_id"), "valuehold")
            self.assertEqual(
                asdict(migrated_first).get("campaign_catalog_sha256"),
                CAMPAIGN_CATALOG_V1_SHA256,
            )
            self.assertEqual(
                asdict(migrated_first).get("event_ordinal_at_opening"),
                0,
            )
            self.assertEqual(
                asdict(migrated_first).get("event_hash_at_opening"),
                "0" * 64,
            )
            self.assertEqual(migrated.export_profile(profile.profile_id), export_before)
            columns = {
                row[1]: tuple(row[2:])
                for row in migrated._connection.execute(
                    "PRAGMA table_info(local_sessions)"
                )
            }
            self.assertEqual(columns["active_world_id"], ("TEXT", 1, None, 0))
            self.assertEqual(
                columns["campaign_catalog_sha256"],
                ("TEXT", 1, None, 0),
            )
            self.assertEqual(
                columns["event_ordinal_at_opening"],
                ("INTEGER", 1, None, 0),
            )
            self.assertEqual(
                columns["event_hash_at_opening"],
                ("TEXT", 1, None, 0),
            )
            self.assertEqual(
                tuple(
                    tuple(row[2:])
                    for row in migrated._connection.execute(
                        "PRAGMA foreign_key_list(local_sessions)"
                    )
                ),
                (("local_profiles", "profile_id", "profile_id", "NO ACTION", "CASCADE", "NONE"),),
            )
            indexes = {
                row[1]: (row[2], row[3], row[4])
                for row in migrated._connection.execute(
                    "PRAGMA index_list(local_sessions)"
                )
            }
            self.assertEqual(
                indexes["one_open_session_per_profile"],
                (1, "c", 1),
            )
            self.assertEqual(
                indexes["local_sessions_by_profile"],
                (0, "c", 0),
            )

    def test_schema_v6_persists_hash_linked_session_opening_authority(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            first = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            second = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            self.assertEqual(
                store._connection.execute("PRAGMA user_version").fetchone()[0],
                6,
            )
            openings = store._connection.execute(
                "SELECT * FROM session_opening_log "
                "WHERE profile_id = ? ORDER BY opening_ordinal",
                (profile.profile_id,),
            ).fetchall()

            self.assertEqual(len(openings), 2)
            self.assertEqual(
                tuple(row["opening_ordinal"] for row in openings),
                (1, 2),
            )
            self.assertEqual(
                tuple(row["session_id"] for row in openings),
                (first.session_id, second.session_id),
            )
            self.assertEqual(openings[0]["previous_opening_hash"], "0" * 64)
            self.assertEqual(
                openings[1]["previous_opening_hash"],
                openings[0]["opening_hash"],
            )
            # Threat boundary: this unkeyed ledger detects partial corruption;
            # it does not claim to defeat rewriting every SQLite authority.
            for row in openings:
                payload = {
                    "activeWorldId": row["active_world_id"],
                    "eventHashAtOpening": row["event_hash_at_opening"],
                    "eventOrdinalAtOpening": row["event_ordinal_at_opening"],
                    "openedAt": row["opened_at"],
                    "openingOrdinal": row["opening_ordinal"],
                    "previousOpeningHash": row["previous_opening_hash"],
                    "profileId": row["profile_id"],
                    "sessionId": row["session_id"],
                }
                self.assertEqual(row["opening_hash"], self._opening_hash(payload))

    def test_single_session_v4_dev_database_migrates_explicitly_to_v6(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
        self._downgrade_identity_database_to_v4(self.path)

        with ProfileStore(self.path) as migrated:
            self.assertEqual(
                migrated._connection.execute("PRAGMA user_version").fetchone()[0],
                6,
            )
            self.assertEqual(
                migrated.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                ),
                session,
            )
            opening = migrated._connection.execute(
                "SELECT * FROM session_opening_log WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()
            self.assertIsNotNone(opening)
            self.assertEqual(opening["opening_ordinal"], 1)

    def test_committed_migration_ignores_and_retries_backup_cleanup_failure(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
        self._downgrade_identity_database_to_v4(self.path)
        backup = self.path.with_suffix(self.path.suffix + ".backup-v4")
        original_unlink = Path.unlink

        def reject_committed_backup_cleanup(path, *args, **kwargs):
            if path == backup and path.exists():
                raise OSError("injected committed-backup cleanup failure")
            return original_unlink(path, *args, **kwargs)

        migrated = None
        failure = None
        with patch.object(Path, "unlink", reject_committed_backup_cleanup):
            try:
                migrated = ProfileStore(self.path)
            except BaseException as exc:
                failure = exc

        self.assertIsNone(failure)
        self.assertIsNotNone(migrated)
        if migrated is not None:
            self.assertEqual(
                migrated._connection.execute("PRAGMA user_version").fetchone()[0],
                6,
            )
            self.assertEqual(migrated.load_session(session.session_id), session)
            migrated.close()
        self.assertTrue(backup.exists())

        with ProfileStore(self.path) as retried:
            self.assertEqual(
                retried._connection.execute("PRAGMA user_version").fetchone()[0],
                6,
            )
        self.assertFalse(backup.exists())

    def test_multi_session_v4_dev_database_fails_closed_with_backup(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
        self._downgrade_identity_database_to_v4(self.path)
        backup = self.path.with_suffix(self.path.suffix + ".backup-v4")

        with self.assertRaisesRegex(ProfileStoreError, "v4.*cannot.*prove"):
            ProfileStore(self.path)

        self.assertTrue(backup.exists())
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'session_opening_log'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_v3_migration_rejects_unprovable_multi_session_history_with_backup(
        self,
    ):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

        self._downgrade_identity_database_to_v3()
        backup = self.path.with_suffix(self.path.suffix + ".backup-v3")

        with self.assertRaisesRegex(ProfileStoreError, "cannot prove"):
            ProfileStore(self.path)

        self.assertTrue(backup.exists())
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM local_sessions").fetchone()[0],
                2,
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(local_sessions)")
            }
            self.assertNotIn("active_world_id", columns)
            self.assertNotIn("campaign_catalog_sha256", columns)
            self.assertNotIn("event_ordinal_at_opening", columns)
            self.assertNotIn("event_hash_at_opening", columns)
        finally:
            connection.close()

    def test_rejected_v3_preflight_does_not_recreate_a_missing_index(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

        self._downgrade_identity_database_to_v3()
        connection = sqlite3.connect(self.path)
        connection.execute("DROP INDEX local_sessions_by_profile")
        connection.commit()
        schema_before = tuple(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "ORDER BY type, name"
            ).fetchall()
        )
        sessions_before = tuple(
            connection.execute(
                "SELECT * FROM local_sessions ORDER BY session_id"
            ).fetchall()
        )
        receipts_before = tuple(
            connection.execute(
                "SELECT * FROM identity_command_receipts ORDER BY request_id"
            ).fetchall()
        )
        events_before = tuple(
            connection.execute(
                "SELECT * FROM event_log ORDER BY profile_id, ordinal"
            ).fetchall()
        )
        version_before = connection.execute("PRAGMA user_version").fetchone()[0]
        connection.close()
        backup = self.path.with_suffix(self.path.suffix + ".backup-v3")

        with self.assertRaisesRegex(ProfileStoreError, "cannot prove"):
            ProfileStore(self.path)

        self.assertTrue(backup.exists())
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                version_before,
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master "
                        "ORDER BY type, name"
                    ).fetchall()
                ),
                schema_before,
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT * FROM local_sessions ORDER BY session_id"
                    ).fetchall()
                ),
                sessions_before,
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT * FROM identity_command_receipts ORDER BY request_id"
                    ).fetchall()
                ),
                receipts_before,
            )
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT * FROM event_log ORDER BY profile_id, ordinal"
                    ).fetchall()
                ),
                events_before,
            )
        finally:
            connection.close()

    def test_v4_schema_rejects_wrong_open_session_index_predicate(self):
        with ProfileStore(self.path):
            pass
        connection = sqlite3.connect(self.path)
        connection.execute("DROP INDEX one_open_session_per_profile")
        connection.execute(
            "CREATE UNIQUE INDEX one_open_session_per_profile "
            "ON local_sessions(profile_id) WHERE closed_at IS NOT NULL"
        )
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(ProfileStoreError, "indexes"):
            ProfileStore(self.path)

    def test_v4_schema_rejects_identity_receipts_without_action_check(self):
        with ProfileStore(self.path):
            pass
        self._remove_identity_receipt_action_check(self.path)

        with self.assertRaisesRegex(ProfileStoreError, "constraints"):
            ProfileStore(self.path)

    def test_identity_receipt_check_tolerates_punctuation_whitespace(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
        self._compact_identity_receipt_action_check(self.path)

        try:
            with ProfileStore(self.path) as reopened:
                self.assertEqual(reopened.load_profile(profile.profile_id), profile)
        except ProfileStoreError as error:
            self.fail(f"equivalent CHECK whitespace was rejected: {error}")

    def test_identity_receipt_check_rejects_whitespace_inside_action_literal(self):
        with ProfileStore(self.path):
            pass
        self._drift_identity_receipt_action_literal(self.path)

        with self.assertRaisesRegex(ProfileStoreError, "constraints"):
            ProfileStore(self.path)

    def test_v3_preflight_rejects_missing_receipt_check_and_keeps_backup(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
        self._downgrade_identity_database_to_v3()
        self._remove_identity_receipt_action_check(self.path)
        backup = self.path.with_suffix(self.path.suffix + ".backup-v3")

        with self.assertRaisesRegex(ProfileStoreError, "constraints"):
            ProfileStore(self.path)

        self.assertTrue(backup.exists())
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            receipt_sql = " ".join(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'identity_command_receipts'"
                ).fetchone()[0].split()
            )
            self.assertNotIn("CHECK", receipt_sql)
        finally:
            connection.close()

    def test_failed_v3_snapshot_write_rolls_back_schema_and_keeps_backup(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

        self._downgrade_identity_database_to_v3()
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TRIGGER reject_session_snapshot_migration
            BEFORE UPDATE OF response_json ON identity_command_receipts
            BEGIN
                SELECT RAISE(ABORT, 'injected snapshot migration failure');
            END
            """
        )
        connection.commit()
        connection.close()
        backup = self.path.with_suffix(self.path.suffix + ".backup-v3")

        opened_connections: list[sqlite3.Connection] = []
        real_connect = sqlite3.connect

        def tracking_connect(*args, **kwargs):
            opened = real_connect(*args, **kwargs)
            opened_connections.append(opened)
            return opened

        with patch(
            "services.wayline_forge.app.profile_store.sqlite3.connect",
            side_effect=tracking_connect,
        ), self.assertRaises(sqlite3.IntegrityError):
            ProfileStore(self.path)

        self.assertTrue(backup.exists())
        for opened in opened_connections:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                opened.execute("SELECT 1")
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(local_sessions)")
            }
            self.assertNotIn("active_world_id", columns)
            self.assertNotIn("campaign_catalog_sha256", columns)
            self.assertNotIn("event_ordinal_at_opening", columns)
            self.assertNotIn("event_hash_at_opening", columns)
        finally:
            connection.close()

    def test_v3_zero_session_profile_rejects_orphan_receipts_and_events(self):
        self._require_identity_api()
        for orphan_kind in ("receipt", "event"):
            with self.subTest(orphan_kind=orphan_kind):
                path = self.path.with_name(f"orphan-{orphan_kind}.sqlite")
                with ProfileStore(path) as store:
                    profile = store.create_profile(
                        request_id=f"profile-request-{orphan_kind}"
                    )
                    session = store.create_session(
                        request_id=f"session-request-{orphan_kind}",
                        profile_id=profile.profile_id,
                        client_build="mac-demo-0.1.0",
                    )
                    store._connection.execute(
                        "DELETE FROM local_sessions WHERE session_id = ?",
                        (session.session_id,),
                    )
                    if orphan_kind == "receipt":
                        store._connection.execute(
                            "DELETE FROM event_log WHERE profile_id = ?",
                            (profile.profile_id,),
                        )
                    else:
                        store._connection.execute(
                            "DELETE FROM identity_command_receipts "
                            "WHERE request_id = ?",
                            (f"session-request-{orphan_kind}",),
                        )
                    store._connection.commit()

                self._downgrade_identity_database_to_v3(path)
                backup = path.with_suffix(path.suffix + ".backup-v3")

                with self.assertRaisesRegex(ProfileStoreError, "zero sessions"):
                    ProfileStore(path)

                self.assertTrue(backup.exists())
                connection = sqlite3.connect(path)
                try:
                    self.assertEqual(
                        connection.execute("PRAGMA user_version").fetchone()[0],
                        3,
                    )
                finally:
                    connection.close()

    def test_v3_preflight_rejects_global_identity_coverage_corruption(self):
        corruptions = (
            "missing_profile_receipt",
            "duplicate_profile_receipt",
            "event_without_profile",
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                path = self.path.with_name(f"global-{corruption}.sqlite")
                with ProfileStore(path) as store:
                    profile = store.create_profile(
                        request_id=f"profile-request-{corruption}"
                    )
                    store.create_session(
                        request_id=f"session-request-{corruption}",
                        profile_id=profile.profile_id,
                        client_build="mac-demo-0.1.0",
                    )
                    if corruption == "missing_profile_receipt":
                        store._connection.execute(
                            "DELETE FROM identity_command_receipts "
                            "WHERE profile_id = ? AND action = 'create_profile'",
                            (profile.profile_id,),
                        )
                    elif corruption == "duplicate_profile_receipt":
                        store._connection.execute(
                            """
                            INSERT INTO identity_command_receipts (
                                request_id, action, payload_sha256, profile_id,
                                session_id, response_json, response_sha256
                            )
                            SELECT ?, action, payload_sha256, profile_id,
                                   session_id, response_json, response_sha256
                            FROM identity_command_receipts
                            WHERE profile_id = ? AND action = 'create_profile'
                            """,
                            (f"duplicate-profile-{corruption}", profile.profile_id),
                        )
                    else:
                        store._connection.execute(
                            """
                            INSERT INTO event_log (
                                profile_id, ordinal, event_id, idempotency_id,
                                event_type, semantic_key, canonical_json,
                                previous_event_hash, event_hash
                            )
                            SELECT 'profile-missing-global', ordinal,
                                   'event-missing-global',
                                   'idempotency-missing-global', event_type,
                                   semantic_key, canonical_json,
                                   previous_event_hash, event_hash
                            FROM event_log
                            WHERE profile_id = ? AND ordinal = 1
                            """,
                            (profile.profile_id,),
                        )
                    store._connection.commit()

                self._downgrade_identity_database_to_v3(path)
                backup = path.with_suffix(path.suffix + ".backup-v3")

                with self.assertRaisesRegex(
                    ProfileStoreError,
                    "v3 identity preflight",
                ):
                    ProfileStore(path)

                self.assertTrue(backup.exists())
                connection = sqlite3.connect(path)
                try:
                    self.assertEqual(
                        connection.execute("PRAGMA user_version").fetchone()[0],
                        3,
                    )
                finally:
                    connection.close()

    def test_session_request_replay_with_changed_payload_is_rejected(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(
                request_id="profile-request-001",
            )
            original = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

            with self.assertRaises(IdempotencyConflictError):
                store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.2.0",
                )

            self.assertEqual(store.load_open_session(profile.profile_id), original)
            self.assertEqual(len(store.load_events(profile.profile_id)), 1)

    def test_new_session_closes_prior_and_old_replay_never_reopens_it(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(
                request_id="profile-request-001",
            )
            first = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            second = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

            stored_first = store.load_session(first.session_id)
            self.assertEqual(stored_first.closed_at, second.opened_at)
            self.assertTrue(first.opened_at.endswith("Z"))
            self.assertTrue(second.opened_at.endswith("Z"))
            self.assertIsNone(store.load_session(second.session_id).closed_at)
            self.assertEqual(store.load_open_session(profile.profile_id), second)
            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM local_sessions "
                    "WHERE profile_id = ? AND closed_at IS NULL",
                    (profile.profile_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(len(store.load_events(profile.profile_id)), 1)

            old_replay = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            self.assertEqual(old_replay, first)
            self.assertEqual(store.load_open_session(profile.profile_id), second)

    def test_session_creation_rolls_back_when_initial_activation_cannot_append(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(
                request_id="profile-request-001",
            )
            store._connection.execute(
                """
                CREATE TRIGGER reject_initial_activation
                BEFORE INSERT ON event_log
                WHEN NEW.event_type = 'world_activated'
                BEGIN
                    SELECT RAISE(ABORT, 'injected activation failure');
                END
                """
            )
            store._connection.commit()

            with self.assertRaises(ProfileStoreError):
                store.create_session(
                    request_id="session-request-001",
                    profile_id=profile.profile_id,
                    client_build="mac-demo-0.1.0",
                )

            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM local_sessions"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(len(store.load_events(profile.profile_id)), 0)
            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM identity_command_receipts "
                    "WHERE action = 'create_session'"
                ).fetchone()[0],
                0,
            )

            store._connection.execute("DROP TRIGGER reject_initial_activation")
            store._connection.commit()
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            self.assertEqual(store.load_open_session(profile.profile_id), session)
            self.assertEqual(len(store.load_events(profile.profile_id)), 1)

    def test_session_api_has_no_caller_selected_world_activation(self):
        self._require_identity_api()

        parameters = inspect.signature(ProfileStore.create_session).parameters

        self.assertNotIn("world_id", parameters)
        self.assertFalse(hasattr(ProfileStore, "activate_world"))

    def test_delete_profile_removes_identity_commands_sessions_and_private_material(self):
        self._require_identity_api()
        with ProfileStore(self.path) as store:
            profile = store.create_profile(
                request_id="profile-request-001",
            )
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "CREATE TABLE quiz_batch_material ("
                "batch_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL)"
            )
            store._connection.execute(
                "CREATE TABLE quiz_preparation_receipts ("
                "request_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL)"
            )
            store._connection.execute(
                "INSERT INTO quiz_batch_material VALUES (?, ?)",
                ("batch-private-001", profile.profile_id),
            )
            store._connection.execute(
                "INSERT INTO quiz_preparation_receipts VALUES (?, ?)",
                ("prepare-private-001", profile.profile_id),
            )
            store._connection.commit()

            store.delete_profile(profile.profile_id)

            for table in (
                "local_profiles",
                "local_sessions",
                "session_opening_log",
                "identity_command_receipts",
                "quiz_batch_material",
                "quiz_preparation_receipts",
                "event_log",
                "learner_projection",
            ):
                with self.subTest(table=table):
                    self.assertEqual(
                        store._connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0],
                        0,
                    )
            wal_path = Path(str(self.path) + "-wal")
            if wal_path.exists():
                self.assertNotIn(profile.profile_id.encode("utf-8"), wal_path.read_bytes())

    def test_delete_profile_fails_before_mutation_when_wal_reader_is_held(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            current = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            reader = sqlite3.connect(self.path)
            try:
                reader.execute("PRAGMA busy_timeout = 1")
                reader.execute("BEGIN")
                reader.execute(
                    "SELECT response_json FROM identity_command_receipts "
                    "WHERE profile_id = ?",
                    (profile.profile_id,),
                ).fetchall()
                wal_path = Path(str(self.path) + "-wal")
                self.assertTrue(wal_path.exists())
                self.assertIn(
                    profile.profile_id.encode("utf-8"),
                    wal_path.read_bytes(),
                )
                store._connection.execute("PRAGMA busy_timeout = 1")

                failure = None
                try:
                    store.delete_profile(
                        profile.profile_id,
                        expected_session_id=current.session_id,
                    )
                except BaseException as exc:
                    failure = exc

                self.assertIsInstance(failure, ProfileStoreError)
                self.assertEqual(
                    store._connection.execute(
                        "SELECT COUNT(*) FROM local_profiles WHERE profile_id = ?",
                        (profile.profile_id,),
                    ).fetchone()[0],
                    1,
                )
            finally:
                reader.rollback()
                reader.close()

    def test_delete_profile_backup_unlink_failure_rolls_back_rows(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            current = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            backup = self.path.with_suffix(self.path.suffix + ".backup-v4")
            backup.write_bytes(b"private migration backup")
            original_unlink = Path.unlink

            def reject_backup_unlink(path, *args, **kwargs):
                if path == backup:
                    raise OSError("injected backup unlink failure")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", reject_backup_unlink):
                with self.assertRaises(OSError):
                    store.delete_profile(
                        profile.profile_id,
                        expected_session_id=current.session_id,
                    )

            remaining_profiles = store._connection.execute(
                "SELECT COUNT(*) FROM local_profiles WHERE profile_id = ?",
                (profile.profile_id,),
            ).fetchone()[0]
            self.assertEqual(remaining_profiles, 1)
            if remaining_profiles:
                self.assertEqual(store.load_profile(profile.profile_id), profile)
                self.assertEqual(store.load_open_session(profile.profile_id), current)

    def test_delete_profile_never_reports_post_commit_cleanup_failure(self):
        class CleanupFailureStore(ProfileStore):
            cleanup_called = False

            def _post_delete_maintenance(self):
                self.cleanup_called = True
                raise sqlite3.OperationalError("injected post-commit cleanup failure")

        with CleanupFailureStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            current = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

            result = store.delete_profile(
                profile.profile_id,
                expected_session_id=current.session_id,
            )

            self.assertIsNone(result)
            self.assertTrue(store.cleanup_called)
            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM local_profiles WHERE profile_id = ?",
                    (profile.profile_id,),
                ).fetchone()[0],
                0,
            )

    def test_delete_profile_rejects_stale_expected_session_without_deleting(self):
        self.assertIn(
            "expected_session_id",
            inspect.signature(ProfileStore.delete_profile).parameters,
        )
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            stale = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            current = store.create_session(
                request_id="session-request-002",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )

            with self.assertRaises(CampaignStateConflictError):
                store.delete_profile(
                    profile.profile_id,
                    expected_session_id=stale.session_id,
                )

            self.assertEqual(store.load_profile(profile.profile_id), profile)
            self.assertEqual(store.load_open_session(profile.profile_id), current)

    def test_delete_profile_authorizes_and_deletes_under_one_immediate_transaction(self):
        self.assertIn(
            "expected_session_id",
            inspect.signature(ProfileStore.delete_profile).parameters,
        )
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            current = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            statements: list[str] = []
            store._connection.set_trace_callback(statements.append)
            try:
                store.delete_profile(
                    profile.profile_id,
                    expected_session_id=current.session_id,
                )
            finally:
                store._connection.set_trace_callback(None)

            normalized = [" ".join(statement.upper().split()) for statement in statements]
            begin_index = normalized.index("BEGIN IMMEDIATE")
            authorization_index = next(
                index
                for index, statement in enumerate(normalized)
                if statement.startswith("SELECT SESSION_ID FROM LOCAL_SESSIONS")
            )
            first_delete_index = next(
                index
                for index, statement in enumerate(normalized)
                if statement.startswith("DELETE FROM")
            )
            commit_index = normalized.index("COMMIT")
            self.assertLess(begin_index, authorization_index)
            self.assertLess(authorization_index, first_delete_index)
            self.assertLess(first_delete_index, commit_index)
            with self.assertRaises(ProfileNotFoundError):
                store.load_profile(profile.profile_id)


if __name__ == "__main__":
    unittest.main()
