"""Strict, deterministic local profile export contract tests."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from services.wayline_forge.app import contracts as public_contracts
from services.wayline_forge.app.campaign_catalog import CampaignCatalog
from services.wayline_forge.app.profile_store import (
    EventLogCorruptionError,
    IdentityStoreCorruptionError,
    ProfileNotFoundError,
    ProfileStore,
)
from services.wayline_forge.app.events import (
    EVENT_SCHEMA_VERSION,
    WorldActivatedEvent,
    canonical_event_json,
    compute_event_hash,
    event_from_json,
)
from services.wayline_forge.tests.fixtures import EventFactory


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts/wayline/v1/profile-export.schema.json"
CATALOG_SHA256 = "5509097676eccc6c3848bfb64295ac931c73621a1120b9431af0ccc8e793d513"
GENESIS_HASH = "0" * 64


def empty_export_payload() -> dict[str, object]:
    return {
        "schemaVersion": "wayline.profile-export.v1",
        "profileId": "profile-001",
        "createdAtUtc": "2026-07-11T12:34:56.123456Z",
        "campaignCatalogSha256": CATALOG_SHA256,
        "activeWorldId": None,
        "campaignOrdinal": None,
        "sessions": [],
        "events": [],
        "terminalEventChainSha256": GENESIS_HASH,
    }


class ProfileExportContractExistenceTests(unittest.TestCase):
    def test_closed_profile_export_models_exist(self):
        for name in (
            "ProfileExportSessionV1",
            "ProfileExportEventV1",
            "ProfileExportV1",
        ):
            with self.subTest(model=name):
                self.assertTrue(
                    hasattr(public_contracts, name),
                    f"missing public export model: {name}",
                )

    def test_empty_export_contract_is_strict_frozen_and_round_trips_aliases(self):
        payload = empty_export_payload()

        export = public_contracts.ProfileExportV1.model_validate(payload)

        self.assertEqual(export.model_dump(mode="json", by_alias=True), payload)
        with self.assertRaises(ValidationError):
            public_contracts.ProfileExportV1.model_validate(
                payload | {"displayName": "Child"}
            )
        with self.assertRaises(ValidationError):
            export.profile_id = "profile-002"

    def test_session_order_uses_parsed_time_across_timestamp_precisions(self):
        payload = empty_export_payload()
        session = {
            "sessionId": "session-001",
            "clientBuild": "mac-demo-0.1.0",
            "openedAtUtc": "2026-07-11T12:34:56.500000Z",
            "closedAtUtc": None,
        }

        accepted = public_contracts.ProfileExportV1.model_validate(
            payload
            | {
                "createdAtUtc": "2026-07-11T12:34:56Z",
                "sessions": [session],
            }
        )
        self.assertEqual(
            accepted.sessions[0].opened_at_utc,
            "2026-07-11T12:34:56.500000Z",
        )

        with self.assertRaisesRegex(
            ValidationError,
            "a session cannot precede profile creation",
        ):
            public_contracts.ProfileExportV1.model_validate(
                payload
                | {
                    "createdAtUtc": "2026-07-11T12:34:56.500000Z",
                    "sessions": [
                        session
                        | {"openedAtUtc": "2026-07-11T12:34:56Z"}
                    ],
                }
            )

    def test_nested_records_are_closed_strict_and_integrity_checked(self):
        session_payload = {
            "sessionId": "session-001",
            "clientBuild": "mac-demo-0.1.0",
            "openedAtUtc": "2026-07-11T12:34:56Z",
            "closedAtUtc": None,
        }
        sample_event = replace(
            EventFactory.activate(
                profile="profile-001",
                session="session-001",
            ),
            occurred_at="2026-07-11T12:34:56Z",
        )
        canonical = canonical_event_json(sample_event)
        event_payload = {
            "ordinal": 1,
            "canonicalEventJson": canonical,
            "eventSha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
        reordered_payload = json.loads(canonical)
        reordered_payload = {
            "ordinal": reordered_payload.pop("ordinal"),
            **reordered_payload,
        }
        noncanonical = json.dumps(
            reordered_payload,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        session = public_contracts.ProfileExportSessionV1.model_validate(
            session_payload
        )
        event = public_contracts.ProfileExportEventV1.model_validate(event_payload)
        self.assertEqual(
            session.model_dump(mode="json", by_alias=True), session_payload
        )
        self.assertEqual(event.model_dump(mode="json", by_alias=True), event_payload)

        for model_type, payload in (
            (public_contracts.ProfileExportSessionV1, session_payload),
            (public_contracts.ProfileExportEventV1, event_payload),
        ):
            with self.subTest(model=model_type.__name__, case="unknown"):
                with self.assertRaises(ValidationError):
                    model_type.model_validate(payload | {"secret": "no"})

        for invalid in (
            event_payload | {"ordinal": True},
            event_payload | {"ordinal": 2},
            event_payload | {"eventSha256": "f" * 64},
            event_payload | {"canonicalEventJson": noncanonical},
            event_payload | {"canonicalEventJson": "{" + ("x" * 32_768) + "}"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    public_contracts.ProfileExportEventV1.model_validate(invalid)

        with self.assertRaises(ValidationError):
            public_contracts.ProfileExportSessionV1.model_validate(
                session_payload
                | {
                    "openedAtUtc": "2026-07-11T12:34:56.500000Z",
                    "closedAtUtc": "2026-07-11T12:34:56Z",
                }
            )

        unknown_event_payload = json.loads(canonical)
        unknown_event_payload["sealed_answer_material"] = "must-not-pass"
        unknown_event_json = json.dumps(
            unknown_event_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self.assertRaises(ValidationError):
            public_contracts.ProfileExportEventV1.model_validate(
                {
                    "ordinal": 1,
                    "canonicalEventJson": unknown_event_json,
                    "eventSha256": hashlib.sha256(
                        unknown_event_json.encode("utf-8")
                    ).hexdigest(),
                }
            )

        oversized_event = replace(
            EventFactory.wrong(
                "align_by_ends",
                ordinal=1,
                profile="profile-001",
                session="session-001",
            ),
            occurred_at="2026-07-11T12:34:56Z",
            optional_wording_shown="é" * 20_000,
        )
        oversized_unicode = canonical_event_json(oversized_event)
        self.assertLess(len(oversized_unicode), 32_768)
        self.assertGreater(len(oversized_unicode.encode("utf-8")), 32_768)
        with self.assertRaises(ValidationError):
            public_contracts.ProfileExportEventV1.model_validate(
                {
                    "ordinal": 1,
                    "canonicalEventJson": oversized_unicode,
                    "eventSha256": hashlib.sha256(
                        oversized_unicode.encode("utf-8")
                    ).hexdigest(),
                }
            )

    def test_empty_export_requires_null_campaign_state_and_genesis_chain(self):
        payload = empty_export_payload()
        for invalid in (
            payload | {"activeWorldId": "valuehold"},
            payload | {"campaignOrdinal": 1},
            payload | {"terminalEventChainSha256": "f" * 64},
            payload | {"campaignCatalogSha256": "a" * 64},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    public_contracts.ProfileExportV1.model_validate(invalid)

    def test_schema_is_draft_2020_12_closed_and_matches_model_aliases(self):
        self.assertTrue(SCHEMA_PATH.is_file(), "missing profile export schema")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema["additionalProperties"], False)

        model_schema = public_contracts.ProfileExportV1.model_json_schema(
            by_alias=True
        )
        self.assertEqual(set(schema["properties"]), set(model_schema["properties"]))
        self.assertEqual(set(schema["required"]), set(model_schema["required"]))
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

        expected_nested = {
            "ProfileExportSessionV1": {
                "sessionId",
                "clientBuild",
                "openedAtUtc",
                "closedAtUtc",
            },
            "ProfileExportEventV1": {
                "ordinal",
                "canonicalEventJson",
                "eventSha256",
            },
        }
        for name, aliases in expected_nested.items():
            with self.subTest(definition=name):
                definition = schema["$defs"][name]
                self.assertEqual(definition["type"], "object")
                self.assertIs(definition["additionalProperties"], False)
                self.assertEqual(set(definition["properties"]), aliases)
                self.assertEqual(set(definition["required"]), aliases)


class ProfileExportStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "profiles.sqlite3"

    def test_missing_profile_is_rejected_instead_of_exporting_an_empty_identity(self):
        with ProfileStore(self.path) as store:
            with self.assertRaises(ProfileNotFoundError):
                store.export_profile("profile-does-not-exist")

    def test_atomic_export_bypasses_subclass_commit_and_write_hook(self):
        class SideEffectingStore(ProfileStore):
            export_hook_called = False

            def export_profile(self, profile_id: str):
                self.export_hook_called = True
                exported = ProfileStore.export_profile(self, profile_id)
                self._connection.execute(
                    "INSERT INTO subclass_export_probe VALUES ('private')"
                )
                self._connection.commit()
                return exported

        with SideEffectingStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-subclass-001")
            session = store.create_session(
                request_id="session-request-subclass-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "CREATE TABLE subclass_export_probe (value TEXT NOT NULL)"
            )
            store._connection.commit()

            exported = store.export_current_profile(
                profile.profile_id,
                session.session_id,
            )

            self.assertEqual(exported.profile_id, profile.profile_id)
            self.assertFalse(store.export_hook_called)
            self.assertFalse(store._connection.in_transaction)
            self.assertEqual(
                store._connection.execute(
                    "SELECT COUNT(*) FROM subclass_export_probe"
                ).fetchone()[0],
                0,
            )

    def test_new_profile_exports_as_an_integrity_verified_empty_record(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")

            exported = store.export_profile(profile.profile_id)

        self.assertIsInstance(exported, public_contracts.ProfileExportV1)
        self.assertEqual(exported.profile_id, profile.profile_id)
        self.assertEqual(exported.created_at_utc, profile.created_at)
        self.assertEqual(exported.campaign_catalog_sha256, CATALOG_SHA256)
        self.assertIsNone(exported.active_world_id)
        self.assertIsNone(exported.campaign_ordinal)
        self.assertEqual(exported.sessions, ())
        self.assertEqual(exported.events, ())
        self.assertEqual(exported.terminal_event_chain_sha256, GENESIS_HASH)
        self.assertNotIn(
            str(self.path),
            exported.model_dump_json(by_alias=True),
        )

    def test_activated_profile_exports_session_campaign_and_hashed_event(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            activation = store.load_events(profile.profile_id)[0]

            exported = store.export_profile(profile.profile_id)

        self.assertEqual(exported.active_world_id, "valuehold")
        self.assertEqual(exported.campaign_ordinal, 1)
        self.assertEqual(len(exported.sessions), 1)
        self.assertEqual(exported.sessions[0].session_id, session.session_id)
        self.assertEqual(exported.sessions[0].client_build, session.client_build)
        self.assertEqual(exported.sessions[0].opened_at_utc, session.opened_at)
        self.assertIsNone(exported.sessions[0].closed_at_utc)
        self.assertEqual(len(exported.events), 1)
        canonical = canonical_event_json(activation)
        self.assertEqual(exported.events[0].ordinal, 1)
        self.assertEqual(exported.events[0].canonical_event_json, canonical)
        self.assertEqual(
            exported.events[0].event_sha256,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            exported.terminal_event_chain_sha256,
            compute_event_hash(GENESIS_HASH, activation),
        )

    def test_export_is_byte_identical_across_repeated_calls_and_restart(self):
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
                client_build="mac-demo-0.1.1",
            )
            first = store.export_profile(profile.profile_id).model_dump_json(
                by_alias=True
            )
            repeated = store.export_profile(profile.profile_id).model_dump_json(
                by_alias=True
            )

        with ProfileStore(self.path) as restarted:
            after_restart = restarted.export_profile(
                profile.profile_id
            ).model_dump_json(by_alias=True)

        self.assertEqual(first.encode("utf-8"), repeated.encode("utf-8"))
        self.assertEqual(first.encode("utf-8"), after_restart.encode("utf-8"))

    def test_export_isolates_other_profiles_sessions_and_events(self):
        with ProfileStore(self.path) as store:
            first = store.create_profile(request_id="profile-request-001")
            first_session = store.create_session(
                request_id="session-request-001",
                profile_id=first.profile_id,
                client_build="mac-demo-0.1.0",
            )
            other = store.create_profile(request_id="profile-request-002")
            other_session = store.create_session(
                request_id="session-request-002",
                profile_id=other.profile_id,
                client_build="mac-demo-0.2.0",
            )

            serialized = store.export_profile(first.profile_id).model_dump_json(
                by_alias=True
            )

        self.assertIn(first.profile_id, serialized)
        self.assertIn(first_session.session_id, serialized)
        self.assertNotIn(other.profile_id, serialized)
        self.assertNotIn(other_session.session_id, serialized)

    def test_export_rejects_a_coherently_rehashed_noncanonical_event_timestamp(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            activation = store.load_events(profile.profile_id)[0]
            payload = json.loads(canonical_event_json(activation))
            payload["occurred_at"] = "2026-07-11T12:34:56+00:00"
            tampered_json = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            tampered_event = event_from_json(tampered_json)
            tampered_chain_hash = compute_event_hash(GENESIS_HASH, tampered_event)
            store._connection.execute(
                """
                UPDATE event_log
                SET canonical_json = ?, event_hash = ?
                WHERE profile_id = ? AND ordinal = 1
                """,
                (tampered_json, tampered_chain_hash, profile.profile_id),
            )
            store._connection.commit()

            with self.assertRaises(EventLogCorruptionError):
                store.export_profile(profile.profile_id)

    def test_export_rejects_a_tampered_identity_receipt(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                """
                UPDATE identity_command_receipts
                SET response_sha256 = ?
                WHERE request_id = ?
                """,
                ("f" * 64, "session-request-001"),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.export_profile(profile.profile_id)

    def test_export_rejects_a_tampered_session_timestamp(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store._connection.execute(
                "UPDATE local_sessions SET opened_at = ? WHERE session_id = ?",
                ("2026-02-30T12:34:56Z", session.session_id),
            )
            store._connection.commit()

            with self.assertRaises(IdentityStoreCorruptionError):
                store.export_profile(profile.profile_id)

    def test_export_rejects_catalog_inconsistent_derived_campaign_state(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            activation = store.load_events(profile.profile_id)[0]
            payload = json.loads(canonical_event_json(activation))
            payload["core_subskill_ids"] = ["forged_skill"]
            tampered_json = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            tampered_event = event_from_json(tampered_json)
            store._connection.execute(
                """
                UPDATE event_log
                SET canonical_json = ?, event_hash = ?
                WHERE profile_id = ? AND ordinal = 1
                """,
                (
                    tampered_json,
                    compute_event_hash(GENESIS_HASH, tampered_event),
                    profile.profile_id,
                ),
            )
            store._connection.commit()

            with self.assertRaises(EventLogCorruptionError):
                store.export_profile(profile.profile_id)

    def test_observation_evidence_is_preserved_while_private_material_is_excluded(self):
        private_sentinels = (
            "CHILD-DISPLAY-NAME-SECRET",
            "API-LAUNCH-TOKEN-SECRET",
            "RAW-SLM-TEXT-SECRET",
            "SEALED-ANSWER-KEY-SECRET",
            "CACHE-PRIVATE-JSON-SECRET",
            "/private/database/path/secret.sqlite3",
        )
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            observation = replace(
                EventFactory.wrong(
                    "align_by_ends",
                    ordinal=2,
                    confidence="certain",
                    keep_wrong=False,
                    profile=profile.profile_id,
                    session=session.session_id,
                    battle="valuehold_route_1",
                    batch="batch-001",
                ),
                occurred_at=session.opened_at,
            )
            store.append(observation)
            store._connection.execute(
                """
                CREATE TABLE export_private_probe (
                    profile_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    api_token TEXT NOT NULL,
                    raw_slm_text TEXT NOT NULL,
                    sealed_answer_material TEXT NOT NULL,
                    cache_private_json TEXT NOT NULL,
                    database_path TEXT NOT NULL
                )
                """
            )
            store._connection.execute(
                "INSERT INTO export_private_probe VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile.profile_id, *private_sentinels),
            )
            store._connection.commit()

            exported = store.export_profile(profile.profile_id)

        event_payload = json.loads(exported.events[1].canonical_event_json)
        self.assertEqual(event_payload["first_option_id"], observation.first_option_id)
        self.assertEqual(event_payload["final_option_id"], observation.final_option_id)
        self.assertEqual(event_payload["first_confidence"], "certain")
        self.assertEqual(event_payload["final_confidence"], "certain")
        self.assertEqual(event_payload["first_procedure_id"], "align_by_ends")
        self.assertIsNone(event_payload["final_procedure_id"])
        self.assertEqual(
            tuple(event_payload["canonical_feedback"]),
            observation.canonical_feedback,
        )

        serialized = exported.model_dump_json(by_alias=True)
        for sentinel in private_sentinels:
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, serialized)

        forbidden_keys = {
            "displayName",
            "display_name",
            "apiToken",
            "api_token",
            "launchToken",
            "launch_token",
            "credentials",
            "rawSlmText",
            "raw_slm_text",
            "sealedAnswerMaterial",
            "sealed_answer_material",
            "correctOptionId",
            "correct_option_id",
            "correctAnswer",
            "correct_answer",
            "cachePrivateJson",
            "cache_private_json",
            "databasePath",
            "database_path",
        }

        def all_keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | {
                    nested
                    for child in value.values()
                    for nested in all_keys(child)
                }
            if isinstance(value, list):
                return {
                    nested
                    for child in value
                    for nested in all_keys(child)
                }
            return set()

        public_payload = exported.model_dump(mode="json", by_alias=True)
        self.assertTrue(forbidden_keys.isdisjoint(all_keys(public_payload)))
        self.assertTrue(forbidden_keys.isdisjoint(all_keys(event_payload)))

    def test_prior_world_transfer_observation_keeps_current_campaign_world(self):
        campaign = CampaignCatalog.packaged_v1()
        decimara = campaign.worlds[1]
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-request-001")
            session = store.create_session(
                request_id="session-request-001",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            store.append(
                WorldActivatedEvent(
                    schema_version=EVENT_SCHEMA_VERSION,
                    event_id="world-activated-002-decimara",
                    idempotency_id="activate-decimara-002",
                    ordinal=2,
                    profile_id=profile.profile_id,
                    session_id=session.session_id,
                    world_id=decimara.world_id,
                    battle_id="campaign-map",
                    occurred_at=session.opened_at,
                    core_subskill_ids=decimara.core_subskill_ids,
                    curriculum_receipt=campaign.curriculum_receipt,
                )
            )
            prior_world_transfer = replace(
                EventFactory.correct(
                    ordinal=3,
                    world="valuehold",
                    skill="place_value",
                    battle="decimara_route_1",
                    batch="batch-transfer-001",
                    transfer=True,
                    changed_context=True,
                    targeted_procedures=("align_by_ends",),
                    profile=profile.profile_id,
                    session=session.session_id,
                ),
                occurred_at=session.opened_at,
            )
            store.append(prior_world_transfer)

            exported = store.export_profile(profile.profile_id)

        self.assertEqual(exported.active_world_id, "decimara")
        self.assertEqual(exported.campaign_ordinal, 2)
        self.assertEqual(
            json.loads(exported.events[-1].canonical_event_json)["world_id"],
            "valuehold",
        )

    def test_fresh_assisted_answers_and_feedback_are_retained_in_local_export(self):
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-assisted-export")
            session = store.create_session(
                request_id="session-assisted-export",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            assisted = replace(
                EventFactory.assisted_completion(
                    ordinal=2,
                    profile=profile.profile_id,
                    session=session.session_id,
                ),
                occurred_at=session.opened_at,
            )
            store.append(assisted)

            exported = store.export_profile(profile.profile_id)

        payload = json.loads(exported.events[-1].canonical_event_json)
        self.assertEqual(tuple(payload["selected_answers"]), assisted.selected_answers)
        self.assertEqual(tuple(payload["confidences"]), assisted.confidences)
        self.assertEqual(
            tuple(payload["selected_procedure_ids"]),
            assisted.selected_procedure_ids,
        )
        self.assertEqual(
            tuple(tuple(item) for item in payload["canonical_feedback"]),
            assisted.canonical_feedback,
        )


if __name__ == "__main__":
    unittest.main()
