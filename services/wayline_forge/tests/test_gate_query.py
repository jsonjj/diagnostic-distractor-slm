from __future__ import annotations

from dataclasses import replace
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.wayline_forge.app import gate_query
from services.wayline_forge.app.campaign_catalog import CampaignCatalog
from services.wayline_forge.app.contracts import BossGateResult
from services.wayline_forge.app.events import (
    OUTCOME_EVENT_SCHEMA_VERSION,
    BattleOutcomeEvent,
    ObservationEvent,
    WorldActivatedEvent,
)
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.quiz_machine import mark_ready, submit_initial
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.tests.fixtures import event
from services.wayline_forge.tests.test_quiz_store import QuizStoreFixture


class BossGateQueryModuleTests(unittest.TestCase):
    def test_dependency_free_authenticated_service_api_is_explicit(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec(
                "services.wayline_forge.app.gate_query"
            )
        )
        for name in ("BossGateQueryError", "BossGateQueryService"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(gate_query, name), f"missing {name}")

        service_signature = inspect.signature(gate_query.BossGateQueryService)
        self.assertEqual(
            tuple(service_signature.parameters),
            ("profile_store", "quiz_store"),
        )
        self.assertNotIn("catalog", service_signature.parameters)
        get_signature = inspect.signature(gate_query.BossGateQueryService.get)
        self.assertEqual(
            tuple(get_signature.parameters),
            ("self", "profile_id", "current_session_id", "world_id"),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for name, parameter in get_signature.parameters.items()
                if name != "self"
            )
        )

    def test_error_codes_are_closed_and_non_sensitive(self) -> None:
        for code in (
            "session_not_current",
            "evidence_sync_unavailable",
            "storage_busy",
            "catalog_conflict",
            "integrity_failure",
        ):
            error = gate_query.BossGateQueryError(code)
            self.assertEqual(error.code, code)
            self.assertEqual(str(error), code)
            self.assertNotIn("profile-secret", repr(error))
        with self.assertRaises(ValueError):
            gate_query.BossGateQueryError("caller-controlled-secret")


class BossGateQueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self._temporary_directory.name) / "wayline.sqlite3"
        )
        self.profiles = ProfileStore(self.database_path)
        self.profile = self.profiles.create_profile(
            request_id="profile-request-gate-001"
        )
        self.session = self.profiles.create_session(
            request_id="session-request-gate-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.quizzes = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        self.catalog = CampaignCatalog.packaged_v1()
        self.service = self._service()

    def tearDown(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self._temporary_directory.cleanup()

    def _service(self) -> gate_query.BossGateQueryService:
        return gate_query.BossGateQueryService(
            self.profiles,
            self.quizzes,
        )

    def _query(
        self,
        *,
        profile_id: str | None = None,
        session_id: str | None = None,
        world_id: str = "valuehold",
    ) -> BossGateResult:
        return self.service.get(
            profile_id=profile_id or self.profile.profile_id,
            current_session_id=session_id or self.session.session_id,
            world_id=world_id,
        )

    def _append_valuehold_progress(
        self,
        *,
        latest_ten_correct: int = 7,
        observation_limit: int = 16,
    ) -> None:
        fixtures = event.ready_valuehold_events(latest_ten_correct)[1:]
        observations_seen = 0
        for fixture in fixtures:
            if isinstance(fixture, BattleOutcomeEvent):
                fixture = replace(
                    fixture,
                    schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                )
            if isinstance(fixture, ObservationEvent):
                observations_seen += 1
                if observations_seen > observation_limit:
                    break
            self.profiles.append(
                replace(
                    fixture,
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                )
            )

    def _queue_three_correct_observations(self, *, start_ordinal: int) -> None:
        batch_id = "batch-gate-pending-001"
        preparing = QuizStoreFixture.preparing(batch_id)
        self.quizzes.create(preparing, profile_id=self.profile.profile_id)
        ready = mark_ready(
            preparing,
            sealed_quiz=QuizStoreFixture.sealed(batch_id),
            expected_version=preparing.version,
        )
        self.quizzes.save_transition(
            ready,
            profile_id=self.profile.profile_id,
            expected_version=preparing.version,
        )
        revealed = submit_initial(
            ready,
            QuizStoreFixture.submission(
                "initial-gate-pending-001",
                batch_id=batch_id,
            ),
            QuizStoreFixture.sealed(batch_id),
            expected_version=ready.version,
        )
        observations = tuple(
            replace(
                item,
                session_id=self.session.session_id,
                battle_id="valuehold_elite",
            )
            for item in QuizStoreFixture.observations_for(
                revealed.machine,
                profile_id=self.profile.profile_id,
                start_ordinal=start_ordinal,
            )
        )
        self.quizzes.save_transition(
            revealed.machine,
            profile_id=self.profile.profile_id,
            expected_version=ready.version,
            receipt=revealed.receipt,
            observation_events=observations,
            observation_session_id=self.session.session_id,
        )

    def _append_activation(
        self,
        *,
        world_index: int,
        ordinal: int,
        core_subskills: tuple[str, ...] | None = None,
        curriculum_receipt: str | None = None,
    ) -> None:
        world = self.catalog.worlds[world_index]
        self.profiles.append(
            WorldActivatedEvent(
                schema_version="wayline.event.v1",
                event_id=f"world-activation-query-{ordinal:03d}",
                idempotency_id=f"world-activation-query-request-{ordinal:03d}",
                ordinal=ordinal,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id=world.world_id,
                battle_id="campaign-map",
                occurred_at="2026-07-11T22:00:00+00:00",
                core_subskill_ids=(
                    world.core_subskill_ids
                    if core_subskills is None
                    else core_subskills
                ),
                curriculum_receipt=(
                    self.catalog.curriculum_receipt
                    if curriculum_receipt is None
                    else curriculum_receipt
                ),
            )
        )

    def _assert_code(self, expected: str, call) -> gate_query.BossGateQueryError:
        with self.assertRaises(gate_query.BossGateQueryError) as caught:
            call()
        self.assertEqual(caught.exception.code, expected)
        return caught.exception

    def test_new_valuehold_profile_is_locked(self) -> None:
        result = self._query()

        self.assertIs(type(result), BossGateResult)
        self.assertEqual(result.world_id, "valuehold")
        self.assertFalse(result.unlocked)
        self.assertEqual(result.lead_in_wins, 0)
        self.assertEqual(result.valid_world_items, 0)

    def test_exact_four_wins_sixteen_items_and_seven_latest_correct_unlock(self) -> None:
        self._append_valuehold_progress(latest_ten_correct=7)

        result = self._query()

        self.assertTrue(result.unlocked)
        self.assertEqual(result.lead_in_wins, 4)
        self.assertEqual(result.valid_world_items, 16)
        self.assertEqual(result.latest_ten_item_count, 10)
        self.assertEqual(result.latest_ten_correct_count, 7)
        self.assertEqual(result.ready_core_subskill_count, 2)

    def test_six_of_latest_ten_remains_locked(self) -> None:
        self._append_valuehold_progress(latest_ten_correct=6)

        result = self._query()

        self.assertFalse(result.unlocked)
        self.assertEqual(result.latest_ten_correct_count, 6)
        self.assertIn("latest_ten_accuracy", result.unmet_requirements)

    def test_pending_reveal_outbox_is_drained_before_gate_evaluation_once(self) -> None:
        self._append_valuehold_progress(
            latest_ten_correct=7,
            observation_limit=13,
        )
        self._queue_three_correct_observations(start_ordinal=19)
        self.assertEqual(
            len(self.quizzes.pending_observations(self.profile.profile_id)),
            3,
        )

        first = self._query()
        event_count = len(self.profiles.load_events(self.profile.profile_id))
        profile_changes = self.profiles._connection.total_changes
        quiz_changes = self.quizzes._require_connection().total_changes
        second = self._query()

        self.assertTrue(first.unlocked)
        self.assertEqual(first, second)
        self.assertEqual(first.valid_world_items, 16)
        self.assertEqual(
            self.quizzes.pending_observations(self.profile.profile_id),
            (),
        )
        self.assertEqual(
            len(self.profiles.load_events(self.profile.profile_id)),
            event_count,
        )
        self.assertEqual(self.profiles._connection.total_changes, profile_changes)
        self.assertEqual(
            self.quizzes._require_connection().total_changes,
            quiz_changes,
        )

    def test_restart_returns_identical_gate_result(self) -> None:
        self._append_valuehold_progress(latest_ten_correct=7)
        before = self._query()

        self.quizzes.close()
        self.profiles.close()
        self.profiles = ProfileStore(self.database_path)
        self.quizzes = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            allow_unverified_test_material=True,
        )
        self.service = self._service()

        self.assertEqual(self._query(), before)

    def test_stale_cross_profile_unknown_future_and_prior_worlds_are_denied(self) -> None:
        other_profile = self.profiles.create_profile(
            request_id="profile-request-gate-other-002"
        )
        other_session = self.profiles.create_session(
            request_id="session-request-gate-other-002",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        current = self.profiles.create_session(
            request_id="session-request-gate-current-002",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self._assert_code(
            "session_not_current",
            lambda: self.service.get(
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
                world_id="valuehold",
            ),
        )
        self._assert_code(
            "session_not_current",
            lambda: self.service.get(
                profile_id=self.profile.profile_id,
                current_session_id=other_session.session_id,
                world_id="valuehold",
            ),
        )
        self.session = current
        for denied_world in ("unknown_realm", "decimara"):
            with self.subTest(denied_world=denied_world):
                self._assert_code(
                    "catalog_conflict",
                    lambda denied_world=denied_world: self._query(
                        world_id=denied_world
                    ),
                )

        self._append_activation(world_index=1, ordinal=2)
        self.assertEqual(self._query(world_id="decimara").world_id, "decimara")
        self._assert_code(
            "catalog_conflict",
            lambda: self._query(world_id="valuehold"),
        )

    def test_tampered_activation_roster_and_catalog_receipt_fail_closed(self) -> None:
        cases = (
            (("tampered_skill",), None),
            (None, "tampered-curriculum-v1"),
        )
        for index, (roster, receipt) in enumerate(cases):
            with self.subTest(index=index):
                if index:
                    self.quizzes.close()
                    self.profiles.close()
                    self._temporary_directory.cleanup()
                    self.setUp()
                self._append_activation(
                    world_index=1,
                    ordinal=2,
                    core_subskills=roster,
                    curriculum_receipt=receipt,
                )
                self._assert_code(
                    "catalog_conflict",
                    lambda: self._query(world_id="decimara"),
                )

    def test_tampered_event_hash_and_outbox_hash_fail_integrity_closed(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE event_log SET event_hash = ? WHERE profile_id = ? AND ordinal = 1",
                ("f" * 64, self.profile.profile_id),
            )
            connection.commit()
        finally:
            connection.close()
        self._assert_code("integrity_failure", self._query)

        self.quizzes.close()
        self.profiles.close()
        self._temporary_directory.cleanup()
        self.setUp()
        self._queue_three_correct_observations(start_ordinal=2)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_observation_outbox SET event_sha256 = ? "
                "WHERE profile_id = ?",
                ("f" * 64, self.profile.profile_id),
            )
            connection.commit()
        finally:
            connection.close()
        self._assert_code("integrity_failure", self._query)
        self.assertEqual(len(self.profiles.load_events(self.profile.profile_id)), 1)

    def test_non_lock_operational_storage_damage_is_integrity_not_busy(self) -> None:
        self.profiles._connection.execute("DROP TABLE event_log")
        self.profiles._connection.commit()

        self._assert_code("integrity_failure", self._query)

    def test_prior_world_transfer_cannot_switch_active_campaign_authority(self) -> None:
        self._append_activation(world_index=1, ordinal=2)
        transfer = event.correct(
            ordinal=3,
            world="valuehold",
            battle="decimara_route_1",
            batch="batch-prior-transfer-001",
            skill="place_value",
            question="question-prior-transfer-001",
            template="template-prior-transfer-001",
            transfer=True,
            targeted_procedures=("place_value_shift",),
            profile=self.profile.profile_id,
            session=self.session.session_id,
        )
        self.profiles.append(transfer)

        result = self._query(world_id="decimara")

        self.assertEqual(result.world_id, "decimara")
        self.assertEqual(result.valid_world_items, 0)
        self._assert_code(
            "catalog_conflict",
            lambda: self._query(world_id="valuehold"),
        )

    def test_public_serialization_is_a_gate_only_allowlist(self) -> None:
        self._append_valuehold_progress(latest_ten_correct=7)

        payload = self._query().model_dump(mode="json", by_alias=True)

        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "worldId",
                "unlocked",
                "leadInWins",
                "requiredLeadInWins",
                "validWorldItems",
                "requiredValidWorldItems",
                "latestTenItemCount",
                "latestTenCorrectCount",
                "requiredLatestTenCorrectCount",
                "coreSubskillCount",
                "readyCoreSubskillCount",
                "unmetRequirements",
            },
        )
        serialized = json.dumps(payload, sort_keys=True).casefold()
        for forbidden in (
            "answer_records",
            "answerrecords",
            "procedure_id",
            "procedureid",
            "learner",
            "event_id",
            "eventid",
            "confidence",
            "receipt",
            "session",
            "profile",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_error_text_never_echoes_supplied_identifiers(self) -> None:
        supplied_profile = "profile-secret-does-not-exist"
        supplied_session = "session-secret-does-not-exist"
        error = self._assert_code(
            "session_not_current",
            lambda: self.service.get(
                profile_id=supplied_profile,
                current_session_id=supplied_session,
                world_id="secret_world",
            ),
        )
        rendered = f"{error!s} {error!r}"
        for secret in (supplied_profile, supplied_session, "secret_world"):
            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
