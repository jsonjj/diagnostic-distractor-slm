from __future__ import annotations

from dataclasses import replace
import hashlib
import sqlite3
import tempfile
from pathlib import Path
import unittest

from services.wayline_forge.app.events import (
    EVENT_SCHEMA_VERSION,
    LEGACY_EVENT_SCHEMA_VERSION,
    OUTCOME_EVENT_SCHEMA_VERSION,
    BattleOutcomeEvent,
    BattleCompletionEvent,
    ObservationEvent,
    canonical_event_json,
    compute_event_hash,
    event_from_json,
)
from services.wayline_forge.app.profile_store import (
    LegacyOutcomeProfileError,
    ProfileNotFoundError,
    ProfileStore,
    SemanticEventConflictError,
)
from services.wayline_forge.tests.fixtures import event


class EventV2Tests(unittest.TestCase):
    def test_current_and_outcome_versions_are_v2(self) -> None:
        self.assertEqual(EVENT_SCHEMA_VERSION, "wayline.event.v1")
        self.assertEqual(OUTCOME_EVENT_SCHEMA_VERSION, "wayline.event.v2")
        self.assertEqual(LEGACY_EVENT_SCHEMA_VERSION, "wayline.event.v1")

    def test_v1_observation_remains_canonically_readable(self) -> None:
        legacy = event.correct(ordinal=1)

        decoded = event_from_json(canonical_event_json(legacy))

        self.assertIsInstance(decoded, ObservationEvent)
        self.assertEqual(decoded, legacy)

    def test_v1_outcome_remains_decodable_for_inspection(self) -> None:
        legacy = BattleOutcomeEvent(
            schema_version=LEGACY_EVENT_SCHEMA_VERSION,
            event_id="legacy-battle-outcome",
            idempotency_id="legacy-battle-request",
            ordinal=1,
            profile_id="profile-legacy",
            session_id="session-legacy",
            world_id="valuehold",
            battle_id="valuehold_route_1",
            occurred_at="2026-07-12T12:00:00Z",
            won=True,
            is_lead_in=True,
        )

        decoded = event_from_json(canonical_event_json(legacy))

        self.assertEqual(decoded, legacy)

    def test_fresh_assisted_completion_has_a_byte_stable_canonical_round_trip(self) -> None:
        assisted = event.assisted_completion()

        canonical = canonical_event_json(assisted)
        decoded = event_from_json(canonical)

        self.assertEqual(decoded, assisted)
        self.assertEqual(canonical_event_json(decoded), canonical)
        self.assertIn('"route_revision":"fresh-assisted-v1"', canonical)
        self.assertNotIn("worked_example_batch_id", canonical)
        self.assertNotIn("supported_batch_ids", canonical)

    def test_fresh_assisted_completion_rejects_inconsistent_derived_fields(self) -> None:
        assisted = event.assisted_completion()
        invalid = {
            "legacy revision": lambda: replace(
                assisted, route_revision="reused-seal-v0"
            ),
            "duplicate item": lambda: replace(
                assisted,
                supported_item_ids=("item-supported-001", "item-supported-001"),
            ),
            "duplicate question": lambda: replace(
                assisted,
                supported_question_ids=(
                    "question-supported-001",
                    "question-supported-001",
                ),
            ),
            "incorrect correctness": lambda: replace(
                assisted, correctness=(True, False)
            ),
            "missing wrong procedure": lambda: replace(
                assisted,
                selected_procedure_ids=(None, "place_value_face_value"),
            ),
            "missing wrong explanation": lambda: replace(
                assisted,
                possible_errors=(None, assisted.possible_errors[1]),
            ),
            "noncanonical feedback": lambda: replace(
                assisted,
                canonical_feedback=(("generic feedback",), assisted.canonical_feedback[1]),
            ),
            "wrong item count": lambda: replace(assisted, item_count=3),
            "wrong final count": lambda: replace(assisted, final_correct=1),
        }

        for label, build in invalid.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                build()


class OutcomeMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "profiles.sqlite"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_distinct_commands_cannot_append_the_same_completion_target(self) -> None:
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-target-unique")
            session = store.create_session(
                request_id="session-target-unique",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            first = BattleCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="battle-completion-first",
                idempotency_id="battle-completion-first-request",
                ordinal=2,
                profile_id=profile.profile_id,
                session_id=session.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                occurred_at="2026-07-12T12:00:00Z",
                won=True,
                is_lead_in=True,
                batch_id="batch-first",
                final_correct=2,
                item_count=3,
            )
            duplicate_target = replace(
                first,
                event_id="battle-completion-second",
                idempotency_id="battle-completion-second-request",
                ordinal=3,
                batch_id="batch-second",
            )
            store.append(first)

            with self.assertRaises(SemanticEventConflictError):
                store.append(duplicate_target)

    def _profile_with_legacy_outcome(self, request_suffix: str) -> str:
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id=f"profile-{request_suffix}")
            session = store.create_session(
                request_id=f"session-{request_suffix}",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            events = store.load_events(profile.profile_id)
            previous_hash = "0" * 64
            for existing in events:
                previous_hash = compute_event_hash(previous_hash, existing)
            legacy = BattleOutcomeEvent(
                schema_version=LEGACY_EVENT_SCHEMA_VERSION,
                event_id=f"legacy-outcome-{request_suffix}",
                idempotency_id=f"legacy-outcome-request-{request_suffix}",
                ordinal=len(events) + 1,
                profile_id=profile.profile_id,
                session_id=session.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                occurred_at="2026-07-12T12:00:00Z",
                won=True,
                is_lead_in=True,
            )
            canonical = canonical_event_json(legacy)
            digest = compute_event_hash(previous_hash, legacy)
            store._connection.execute(
                """
                INSERT INTO event_log (
                    profile_id, ordinal, event_id, idempotency_id,
                    event_type, semantic_key, canonical_json,
                    previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    legacy.profile_id,
                    legacy.ordinal,
                    legacy.event_id,
                    legacy.idempotency_id,
                    legacy.event_type,
                    legacy.semantic_key,
                    canonical,
                    previous_hash,
                    digest,
                ),
            )
            store._connection.execute("PRAGMA user_version = 5")
            store._connection.commit()
            return profile.profile_id

    def test_non_allowlisted_legacy_profile_is_preserved_but_blocked(self) -> None:
        profile_id = self._profile_with_legacy_outcome("owner")

        with ProfileStore(self.path) as migrated:
            self.assertEqual(migrated.load_profile(profile_id).profile_id, profile_id)
            inspected = migrated.inspect_events(profile_id)
            self.assertEqual(inspected[-1].schema_version, LEGACY_EVENT_SCHEMA_VERSION)
            with self.assertRaises(LegacyOutcomeProfileError):
                migrated.load_state(profile_id)
            with self.assertRaises(LegacyOutcomeProfileError):
                migrated.append(replace(inspected[-1], ordinal=3))

    def test_deleting_preserved_legacy_profile_removes_the_block_marker(self) -> None:
        profile_id = self._profile_with_legacy_outcome("delete-owner")

        with ProfileStore(self.path) as migrated:
            migrated.delete_profile(profile_id)
            marker_count = migrated._connection.execute(
                "SELECT COUNT(*) FROM legacy_outcome_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()[0]

        self.assertEqual(marker_count, 0)

    def test_only_explicitly_allowlisted_development_profile_resets(self) -> None:
        owner_profile = self._profile_with_legacy_outcome("owner")
        dev_profile = self._profile_with_legacy_outcome("disposable-dev")

        with ProfileStore(
            self.path,
            disposable_development_profile_ids={dev_profile},
        ) as migrated:
            with self.assertRaises(ProfileNotFoundError):
                migrated.load_profile(dev_profile)
            self.assertEqual(
                migrated.load_profile(owner_profile).profile_id,
                owner_profile,
            )
            with self.assertRaises(LegacyOutcomeProfileError):
                migrated.load_state(owner_profile)

    def test_old_reused_assisted_event_is_marked_and_blocked_not_transformed(self) -> None:
        with ProfileStore(self.path) as store:
            profile = store.create_profile(request_id="profile-old-assisted")
            session = store.create_session(
                request_id="session-old-assisted",
                profile_id=profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
            activation = store.load_events(profile.profile_id)[0]
            previous_hash = compute_event_hash("0" * 64, activation)
            old_payload = {
                "schema_version": "wayline.event.v2",
                "event_id": "old-assisted-completion",
                "idempotency_id": "old-assisted-request",
                "ordinal": 2,
                "profile_id": profile.profile_id,
                "session_id": session.session_id,
                "world_id": "valuehold",
                "battle_id": "valuehold_assisted_route",
                "occurred_at": session.opened_at,
                "route_id": "assisted-old-reused-route",
                "worked_example_batch_id": "batch-seal-one",
                "worked_example_id": "item-seal-one",
                "supported_batch_ids": ["batch-seal-one", "batch-seal-two"],
                "supported_item_ids": ["item-seal-two", "item-seal-three"],
                "selected_option_ids": ["option-a", "option-b"],
                "correct_option_ids": ["option-c", "option-d"],
                "confidences": ["leaning", "guessing"],
                "correctness": [False, False],
                "reliable_methods": ["method one", "method two"],
                "trusted_steps": [["step one"], ["step two"]],
                "final_correct": 0,
                "item_count": 2,
                "event_type": "assisted_route_completion",
            }
            canonical = __import__("json").dumps(
                old_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            digest = hashlib.sha256(
                (previous_hash + canonical).encode("utf-8")
            ).hexdigest()
            store._connection.execute(
                """
                INSERT INTO event_log (
                    profile_id, ordinal, event_id, idempotency_id,
                    event_type, semantic_key, canonical_json,
                    previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.profile_id,
                    2,
                    "old-assisted-completion",
                    "old-assisted-request",
                    "assisted_route_completion",
                    "assisted_route_completion:valuehold",
                    canonical,
                    previous_hash,
                    digest,
                ),
            )
            store._connection.execute("PRAGMA user_version = 6")
            store._connection.commit()

        with ProfileStore(self.path) as migrated:
            marker = migrated._connection.execute(
                "SELECT legacy_schema_version FROM legacy_outcome_profiles "
                "WHERE profile_id = ?",
                (profile.profile_id,),
            ).fetchone()[0]
            self.assertEqual(marker, "wayline.assisted-reused.v0")
            with self.assertRaises(LegacyOutcomeProfileError):
                migrated.load_state(profile.profile_id)


if __name__ == "__main__":
    unittest.main()
