from __future__ import annotations

from dataclasses import replace
import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.wayline_forge.app import runtime_state
from services.wayline_forge.app.batch_material import VerifiedBatchMaterial
from services.wayline_forge.app.campaign_catalog import (
    CAMPAIGN_CATALOG_V1_SHA256,
    CampaignCatalog,
)
from services.wayline_forge.app.contracts import (
    BattleQuizRequest,
    RuntimeState,
)
from services.wayline_forge.app.events import ObservationEvent, WorldActivatedEvent
from services.wayline_forge.app.profile_store import (
    IdentityStoreCorruptionError,
    ProfileStore,
    ProfileStoreError,
)
from services.wayline_forge.app.quiz_machine import (
    QuizSelection,
    QuizState,
    QuizSubmission,
    close_quiz,
    new_quiz,
    submit_initial,
    submit_revision,
)
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
)


class _FailingProfileStore:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def load_profile(self, profile_id: str) -> object:
        raise self._error


class _FailingRuntimeQuizStore:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def resumable_batch_id(self, profile_id: str) -> str | None:
        raise self._error


class RuntimeStateModuleTests(unittest.TestCase):
    def test_runtime_state_service_module_exists(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec(
                "services.wayline_forge.app.runtime_state"
            ),
            "authenticated runtime-state service is missing",
        )

    def test_runtime_state_api_is_explicit_and_typed(self) -> None:
        for name in (
            "RuntimeStateError",
            "RuntimeStateAuthenticationError",
            "RuntimeStateUnavailableError",
            "RuntimeStateCatalogError",
            "RuntimeStateIntegrityError",
            "RuntimeStateService",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(runtime_state, name), f"missing {name}")
        self.assertTrue(hasattr(QuizStore, "resumable_batch_id"))

    def test_runtime_state_service_cannot_receive_an_unpinned_catalog(self) -> None:
        self.assertNotIn(
            "catalog",
            inspect.signature(runtime_state.RuntimeStateService).parameters,
        )


class RuntimeStateServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from services.wayline_forge.tests.test_batch_material import (
            BatchMaterialTests,
        )

        BatchMaterialTests.setUpClass()
        cls.material_fixture_type = BatchMaterialTests
        cls.verifier = BatchMaterialTests.verifier

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self._temporary_directory.name) / "wayline.sqlite3"
        )
        self.profiles = ProfileStore(self.database_path)
        self.profile = self.profiles.create_profile(
            request_id="profile-request-001"
        )
        self.session = self.profiles.create_session(
            request_id="session-request-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.quizzes = self._open_quiz_store()
        self.service = self._service()

    def tearDown(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self._temporary_directory.cleanup()

    def _open_quiz_store(self) -> QuizStore:
        return QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def _service(self) -> runtime_state.RuntimeStateService:
        return runtime_state.RuntimeStateService(
            self.profiles,
            self.quizzes,
        )

    def _restart(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self.profiles = ProfileStore(self.database_path)
        self.quizzes = self._open_quiz_store()
        self.service = self._service()

    def _material(
        self,
        *,
        batch_id: str = "batch-valuehold-001",
        session_id: str | None = None,
    ) -> tuple[object, VerifiedBatchMaterial]:
        fixture = self.material_fixture_type(methodName="runTest")
        fixture.setUp()
        fixture.context = replace(
            fixture.context,
            profile_id=self.profile.profile_id,
            session_id=session_id or self.session.session_id,
            battle_id="valuehold_route_1",
        )
        material = fixture.complete_material()
        if material.batch_id != batch_id:
            material = VerifiedBatchMaterial._create(
                batch_id=batch_id,
                context=material.context,
                plan_contract=material.plan_contract,
                items=material.items,
            )
        return fixture, material

    def _prepare(
        self,
        *,
        batch_id: str = "batch-valuehold-001",
        request_id: str = "prepare-request-001",
        session_id: str | None = None,
    ) -> tuple[object, VerifiedBatchMaterial, object]:
        fixture, material = self._material(
            batch_id=batch_id,
            session_id=session_id,
        )
        request = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId=request_id,
            sessionId=material.context.session_id,
            battleId=material.context.battle_id,
            worldId=material.context.world_id,
            battleTier=material.context.battle_tier,
        )
        prepared = self.quizzes.create_prepared(
            material,
            request=request,
            profile_id=self.profile.profile_id,
        )
        return fixture, material, prepared

    @staticmethod
    def _submission(
        material: VerifiedBatchMaterial,
        request_id: str,
        *,
        correct: bool,
    ) -> QuizSubmission:
        selections: list[QuizSelection] = []
        for item in material.items:
            route = next(
                route
                for route in item.routes
                if (route.procedure_id is None) is correct
            )
            selections.append(
                QuizSelection(
                    item_id=item.item_id,
                    option_id=route.option_id,
                    confidence="certain" if correct else "leaning",
                )
            )
        return QuizSubmission(
            schema_version="wayline.v1",
            request_id=request_id,
            batch_id=material.batch_id,
            item_count=len(selections),
            selections=tuple(selections),
        )

    def _observations(
        self,
        material: VerifiedBatchMaterial,
        revealed: object,
    ) -> tuple[ObservationEvent, ...]:
        result = revealed.machine.final_result
        self.assertIsNotNone(result)
        assert result is not None
        material_by_item = {item.item_id: item for item in material.items}
        events: list[ObservationEvent] = []
        for offset, final in enumerate(result.items, start=2):
            item = material_by_item[final.item_id]
            first_route = item.route_for_option(final.first_selection.option_id)
            final_route = item.route_for_option(final.final_selection.option_id)
            feedback = tuple(
                value
                for value in (final.possible_error, final.reliable_method)
                if value is not None
            )
            events.append(
                ObservationEvent(
                    schema_version="wayline.event.v1",
                    event_id=(
                        f"observation-{material.batch_id}-{offset:03d}"
                    ),
                    idempotency_id=(
                        f"observation-request-{material.batch_id}-{offset:03d}"
                    ),
                    ordinal=offset,
                    profile_id=self.profile.profile_id,
                    session_id=material.context.session_id,
                    world_id=item.bundle.blueprint.world_id,
                    battle_id=material.context.battle_id,
                    occurred_at=f"2026-07-11T20:{offset:02d}:00+00:00",
                    batch_id=material.batch_id,
                    item_id=item.item_id,
                    question_id=item.bundle.blueprint.question_id,
                    template_id=item.bundle.template_id,
                    content_version_id=material.context.content_version_id,
                    skill_id=item.bundle.blueprint.skill_id,
                    world_core_subskill_ids=(
                        material.context.core_subskill_ids
                    ),
                    operand_signature=item.bundle.operand_signature,
                    context_id=item.bundle.context_id,
                    first_option_id=final.first_selection.option_id,
                    final_option_id=final.final_selection.option_id,
                    first_confidence=final.first_selection.confidence,
                    final_confidence=final.final_selection.confidence,
                    first_correct=final.first_selection.is_correct,
                    final_correct=final.final_selection.is_correct,
                    choice_changed=(
                        final.first_selection.option_id
                        != final.final_selection.option_id
                    ),
                    self_corrected=final.self_corrected,
                    first_procedure_id=first_route.procedure_id,
                    final_procedure_id=final_route.procedure_id,
                    targeted_procedure_ids=item.required_procedure_ids,
                    is_transfer=item.is_transfer,
                    is_changed_context_transfer=(
                        item.is_changed_context_transfer
                    ),
                    valid_for_progression=item.valid_for_progression,
                    batch_wrong_count=result.first_pass_wrong_count,
                    canonical_feedback=feedback,
                    optional_wording_shown=None,
                    receipts=item.event_receipts,
                )
            )
        return tuple(events)

    def _runtime(self, session_id: str | None = None) -> RuntimeState:
        return self.service.get(
            self.profile.profile_id,
            session_id or self.session.session_id,
        )

    def test_first_session_returns_hash_pinned_valuehold_state(self) -> None:
        result = self._runtime()

        self.assertIs(type(result), RuntimeState)
        self.assertEqual(result.schema_version, "wayline.v1")
        self.assertEqual(result.profile_id, self.profile.profile_id)
        self.assertEqual(result.session_id, self.session.session_id)
        self.assertEqual(result.active_world_id, "valuehold")
        self.assertEqual(result.campaign_ordinal, 1)
        self.assertIsNone(result.resumable_batch_id)
        self.assertEqual(
            result.campaign_catalog_sha256,
            CAMPAIGN_CATALOG_V1_SHA256,
        )

    def test_restart_resumes_ready_revision_open_and_revealed_but_not_closed(self) -> None:
        _, material, prepared = self._prepare()
        self.assertEqual(prepared.machine.state, QuizState.READY)

        self._restart()
        self.assertEqual(self._runtime().resumable_batch_id, material.batch_id)
        self.assertEqual(
            self.quizzes.load(
                material.batch_id,
                profile_id=self.profile.profile_id,
            ),
            prepared.machine,
        )

        initial = submit_initial(
            prepared.machine,
            self._submission(material, "initial-request-001", correct=False),
            material.sealed_quiz,
            expected_version=prepared.machine.version,
        )
        self.quizzes.save_transition(
            initial.machine,
            profile_id=self.profile.profile_id,
            expected_version=prepared.machine.version,
            receipt=initial.receipt,
        )
        self.assertEqual(initial.machine.state, QuizState.REVISION_OPEN)

        self._restart()
        self.assertEqual(self._runtime().resumable_batch_id, material.batch_id)
        self.assertEqual(
            self.quizzes.load(
                material.batch_id,
                profile_id=self.profile.profile_id,
            ),
            initial.machine,
        )

        revision = submit_revision(
            initial.machine,
            self._submission(material, "revision-request-001", correct=True),
            material.sealed_quiz,
            expected_version=initial.machine.version,
        )
        observations = self._observations(material, revision)
        self.quizzes.save_transition(
            revision.machine,
            profile_id=self.profile.profile_id,
            expected_version=initial.machine.version,
            receipt=revision.receipt,
            observation_events=observations,
            observation_session_id=observations[0].session_id,
        )
        self.assertEqual(revision.machine.state, QuizState.REVEALED)

        self._restart()
        self.assertEqual(self._runtime().resumable_batch_id, material.batch_id)
        self.assertEqual(
            self.quizzes.load(
                material.batch_id,
                profile_id=self.profile.profile_id,
            ),
            revision.machine,
        )

        closed = close_quiz(
            revision.machine,
            expected_version=revision.machine.version,
        )
        self.quizzes.save_transition(
            closed,
            profile_id=self.profile.profile_id,
            expected_version=revision.machine.version,
        )
        self._restart()
        self.assertIsNone(self._runtime().resumable_batch_id)

    def test_new_same_profile_session_resumes_batch_from_original_session(self) -> None:
        original_session_id = self.session.session_id
        _, material, _ = self._prepare(session_id=original_session_id)
        current = self.profiles.create_session(
            request_id="session-request-002",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )

        with self.assertRaises(runtime_state.RuntimeStateAuthenticationError):
            self.service.get(self.profile.profile_id, original_session_id)
        resumed = self.service.get(self.profile.profile_id, current.session_id)
        self.assertEqual(resumed.resumable_batch_id, material.batch_id)
        persisted = self.quizzes.load_batch_material(
            material.batch_id,
            profile_id=self.profile.profile_id,
        )
        self.assertEqual(persisted.context.session_id, original_session_id)
        self.assertNotEqual(persisted.context.session_id, current.session_id)

    def test_cross_profile_stale_and_nonexistent_sessions_are_denied(self) -> None:
        other_profile = self.profiles.create_profile(
            request_id="profile-request-002"
        )
        other_session = self.profiles.create_session(
            request_id="session-request-other-001",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        current = self.profiles.create_session(
            request_id="session-request-002",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )

        denied = (
            (self.profile.profile_id, other_session.session_id),
            (self.profile.profile_id, self.session.session_id),
            (self.profile.profile_id, "session-does-not-exist"),
            ("profile-does-not-exist", current.session_id),
        )
        for profile_id, session_id in denied:
            with self.subTest(profile_id=profile_id, session_id=session_id):
                with self.assertRaises(
                    runtime_state.RuntimeStateAuthenticationError
                ) as caught:
                    self.service.get(profile_id, session_id)
                self.assertEqual(caught.exception.code, "session_not_current")
                self.assertEqual(str(caught.exception), "session_not_current")

    def test_profile_storage_busy_is_redacted_as_retryable_unavailable(self) -> None:
        causes: tuple[BaseException, ...] = (
            sqlite3.OperationalError(
                "database is locked near /private/learner.sqlite"
            ),
            self._wrapped_profile_busy_error(),
        )
        for cause in causes:
            with self.subTest(cause=type(cause).__name__):
                service = runtime_state.RuntimeStateService(
                    _FailingProfileStore(cause),
                    self.quizzes,
                )

                with self.assertRaises(
                    runtime_state.RuntimeStateUnavailableError
                ) as caught:
                    service.get(
                        self.profile.profile_id,
                        self.session.session_id,
                    )

                self.assertEqual(caught.exception.code, "storage_busy")
                self.assertEqual(str(caught.exception), "storage_busy")
                self.assertNotIn("private", repr(caught.exception).casefold())

    @staticmethod
    def _wrapped_profile_busy_error() -> ProfileStoreError:
        cause = sqlite3.OperationalError(
            "database is busy near /private/learner.sqlite"
        )
        error = ProfileStoreError("sensitive profile store wrapper")
        error.__cause__ = cause
        return error

    def test_quiz_storage_busy_is_redacted_as_retryable_unavailable(self) -> None:
        service = runtime_state.RuntimeStateService(
            self.profiles,
            _FailingRuntimeQuizStore(
                QuizStoreBusyError("sensitive SQL and learner identity")
            ),
        )

        with self.assertRaises(
            runtime_state.RuntimeStateUnavailableError
        ) as caught:
            service.get(self.profile.profile_id, self.session.session_id)

        self.assertEqual(caught.exception.code, "storage_busy")
        self.assertEqual(str(caught.exception), "storage_busy")
        self.assertNotIn("sensitive", repr(caught.exception).casefold())

    def test_profile_corruption_is_redacted_as_integrity_failure(self) -> None:
        service = runtime_state.RuntimeStateService(
            _FailingProfileStore(
                IdentityStoreCorruptionError(
                    "profile-id-secret in /private/learner.sqlite"
                )
            ),
            self.quizzes,
        )

        with self.assertRaises(
            runtime_state.RuntimeStateIntegrityError
        ) as caught:
            service.get(self.profile.profile_id, self.session.session_id)

        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(str(caught.exception), "integrity_failure")
        self.assertNotIn("secret", repr(caught.exception).casefold())

    def test_unexpected_dependency_failure_is_redacted_as_integrity_failure(
        self,
    ) -> None:
        failures = (
            _FailingProfileStore(
                RuntimeError("api-token-secret from /private/profile.sqlite")
            ),
            _FailingRuntimeQuizStore(
                RuntimeError("SELECT private_payload FROM quiz_material")
            ),
        )
        for dependency in failures:
            with self.subTest(dependency=type(dependency).__name__):
                if isinstance(dependency, _FailingProfileStore):
                    service = runtime_state.RuntimeStateService(
                        dependency,
                        self.quizzes,
                    )
                else:
                    service = runtime_state.RuntimeStateService(
                        self.profiles,
                        dependency,
                    )
                with self.assertRaises(
                    runtime_state.RuntimeStateIntegrityError
                ) as caught:
                    service.get(
                        self.profile.profile_id,
                        self.session.session_id,
                    )
                self.assertEqual(caught.exception.code, "integrity_failure")
                self.assertEqual(str(caught.exception), "integrity_failure")
                self.assertNotIn("private", repr(caught.exception).casefold())

    def test_more_than_one_open_batch_fails_closed(self) -> None:
        _, _, prepared = self._prepare(
            batch_id="batch-valuehold-001",
            request_id="prepare-request-001",
        )
        impossible_second = new_quiz(
            "batch-valuehold-002",
            prepared.machine.item_layouts,
        )
        self.quizzes.create(
            impossible_second,
            profile_id="profile-corruption-staging-002",
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE quiz_machines SET profile_id = ? WHERE batch_id = ?",
                (self.profile.profile_id, impossible_second.batch_id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            QuizStoreCorruptionError,
            "more than one resumable",
        ):
            self.quizzes.resumable_batch_id(self.profile.profile_id)
        with self.assertRaises(
            runtime_state.RuntimeStateIntegrityError
        ) as caught:
            self._runtime()
        self.assertEqual(caught.exception.code, "integrity_failure")
        self.assertEqual(str(caught.exception), "integrity_failure")

    def test_resumable_batch_read_rejects_tampered_indexes_material_and_receipt(self) -> None:
        _, material, _ = self._prepare()
        connection = sqlite3.connect(self.database_path)
        try:
            cases = (
                (
                    "UPDATE quiz_machines SET profile_id = ? WHERE batch_id = ?",
                    ("profile-other-002", material.batch_id),
                    "UPDATE quiz_machines SET profile_id = ? WHERE batch_id = ?",
                    (self.profile.profile_id, material.batch_id),
                ),
                (
                    "UPDATE quiz_machines SET state = ? WHERE batch_id = ?",
                    ("unknown_state", material.batch_id),
                    "UPDATE quiz_machines SET state = ? WHERE batch_id = ?",
                    ("ready", material.batch_id),
                ),
                (
                    "UPDATE quiz_batch_material SET profile_id = ? WHERE batch_id = ?",
                    ("profile-other-002", material.batch_id),
                    "UPDATE quiz_batch_material SET profile_id = ? WHERE batch_id = ?",
                    (self.profile.profile_id, material.batch_id),
                ),
                (
                    "UPDATE quiz_batch_material SET private_json_sha256 = ? WHERE batch_id = ?",
                    ("f" * 64, material.batch_id),
                    "UPDATE quiz_batch_material SET private_json_sha256 = ? WHERE batch_id = ?",
                    (
                        self._sha256(material.to_private_json()),
                        material.batch_id,
                    ),
                ),
                (
                    "UPDATE quiz_preparation_receipts SET receipt_sha256 = ? WHERE batch_id = ?",
                    ("f" * 64, material.batch_id),
                    "UPDATE quiz_preparation_receipts SET receipt_sha256 = ? WHERE batch_id = ?",
                    (
                        self._receipt_row_sha256(connection, material.batch_id),
                        material.batch_id,
                    ),
                ),
            )
            for corrupt_sql, corrupt_values, restore_sql, restore_values in cases:
                with self.subTest(sql=corrupt_sql):
                    connection.execute(corrupt_sql, corrupt_values)
                    connection.commit()
                    with self.assertRaises(QuizStoreCorruptionError):
                        self.quizzes.resumable_batch_id(
                            self.profile.profile_id
                        )
                    connection.execute(restore_sql, restore_values)
                    connection.commit()
                    self.assertEqual(
                        self.quizzes.resumable_batch_id(
                            self.profile.profile_id
                        ),
                        material.batch_id,
                    )
        finally:
            connection.close()

    @staticmethod
    def _sha256(value: str) -> str:
        import hashlib

        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _receipt_row_sha256(
        connection: sqlite3.Connection,
        batch_id: str,
    ) -> str:
        row = connection.execute(
            "SELECT receipt_json FROM quiz_preparation_receipts WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        assert row is not None
        return RuntimeStateServiceTests._sha256(str(row[0]))

    def test_missing_events_and_unknown_or_tampered_active_world_fail_closed(self) -> None:
        self.profiles._connection.execute(
            "DELETE FROM event_log WHERE profile_id = ?",
            (self.profile.profile_id,),
        )
        self.profiles._connection.commit()
        with self.assertRaises(runtime_state.RuntimeStateIntegrityError):
            self._runtime()

        self.profiles.close()
        self.quizzes.close()
        self.database_path.unlink()
        self.profiles = ProfileStore(self.database_path)
        self.profile = self.profiles.create_profile(
            request_id="profile-request-001"
        )
        self.session = self.profiles.create_session(
            request_id="session-request-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.quizzes = self._open_quiz_store()
        self.service = self._service()

        unknown = WorldActivatedEvent(
            schema_version="wayline.event.v1",
            event_id="world-activation-unknown-002",
            idempotency_id="world-activation-request-unknown-002",
            ordinal=2,
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="unknown_realm",
            battle_id="campaign-map",
            occurred_at="2026-07-11T21:02:00+00:00",
            core_subskill_ids=("unknown_skill",),
            curriculum_receipt=CampaignCatalog.packaged_v1().curriculum_receipt,
        )
        self.profiles.append(unknown)
        with self.assertRaises(runtime_state.RuntimeStateCatalogError):
            self._runtime()

    def test_known_world_with_tampered_curriculum_authority_fails_closed(self) -> None:
        tampered = WorldActivatedEvent(
            schema_version="wayline.event.v1",
            event_id="world-activation-tampered-002",
            idempotency_id="world-activation-request-tampered-002",
            ordinal=2,
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            battle_id="campaign-map",
            occurred_at="2026-07-11T21:02:00+00:00",
            core_subskill_ids=("place_value",),
            curriculum_receipt="tampered-curriculum-v1",
        )
        self.profiles.append(tampered)

        with self.assertRaises(runtime_state.RuntimeStateCatalogError):
            self._runtime()

    def test_serialized_runtime_state_contains_only_public_resume_fields(self) -> None:
        _, material, _ = self._prepare()
        result = self._runtime()
        payload = result.model_dump(mode="json", by_alias=True)

        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "profileId",
                "sessionId",
                "activeWorldId",
                "campaignOrdinal",
                "resumableBatchId",
                "campaignCatalogSha256",
            },
        )
        self.assertEqual(payload["resumableBatchId"], material.batch_id)
        serialized = json.dumps(payload, sort_keys=True).casefold()
        for forbidden in (
            "answer",
            "confidence",
            "correct",
            "procedure",
            "private",
            "learner",
            "key",
            "material",
            "receipt",
            "trustedstep",
            "events",
            "skills",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_runtime_reads_issue_no_sql_writes(self) -> None:
        _, material, _ = self._prepare()
        profile_statements: list[str] = []
        quiz_statements: list[str] = []
        profile_changes = self.profiles._connection.total_changes
        quiz_connection = self.quizzes._require_connection()
        quiz_changes = quiz_connection.total_changes
        self.profiles._connection.set_trace_callback(profile_statements.append)
        quiz_connection.set_trace_callback(quiz_statements.append)
        try:
            first = self._runtime()
            second = self._runtime()
        finally:
            self.profiles._connection.set_trace_callback(None)
            quiz_connection.set_trace_callback(None)

        self.assertEqual(first, second)
        self.assertEqual(first.resumable_batch_id, material.batch_id)
        self.assertEqual(
            self.profiles._connection.total_changes,
            profile_changes,
        )
        self.assertEqual(quiz_connection.total_changes, quiz_changes)
        forbidden = (
            "insert ",
            "update ",
            "delete ",
            "replace ",
            "create ",
            "drop ",
            "alter ",
            "vacuum",
        )
        for statement in (*profile_statements, *quiz_statements):
            normalized = " ".join(statement.casefold().split())
            self.assertFalse(
                normalized.startswith(forbidden),
                f"runtime read issued a write: {statement}",
            )


if __name__ == "__main__":
    unittest.main()
