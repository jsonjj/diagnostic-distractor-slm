from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from services.wayline_forge.app.batch_material import VerifiedBatchMaterial
from services.wayline_forge.app.contracts import (
    AnswerSelection,
    BattleQuizRequest,
    InitialSubmission,
    RevisionSubmission,
)
from services.wayline_forge.app.evidence_reducer import EvidenceReplayError
from services.wayline_forge.app.profile_store import (
    EventLogCorruptionError,
    EventOrderError,
    IdempotencyConflictError as ProfileIdempotencyConflictError,
    IdentityStoreCorruptionError,
    OutboxReservationError,
    ProfileStore,
    ProfileStoreError,
    SemanticEventConflictError,
)
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
    QuizStoreError,
)
from services.wayline_forge.app import quiz_submissions


class _Clock:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            self.calls += 1
        return datetime(2026, 7, 11, 21, 30, 0, 123456, tzinfo=timezone.utc)


class _CrashAfterInitialLock:
    def __init__(self, delegate: QuizStore) -> None:
        self._delegate = delegate
        self._raised = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    def save_transition(self, next_machine: object, **kwargs: object) -> object:
        stored = self._delegate.save_transition(next_machine, **kwargs)
        if (
            not self._raised
            and next_machine.state.value == "initial_locked"
            and kwargs.get("receipt") is None
        ):
            self._raised = True
            raise RuntimeError("simulated process interruption")
        return stored


class _DrainFailureStore:
    def __init__(self, delegate: QuizStore, error: Exception) -> None:
        self._delegate = delegate
        self._error = error

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    def drain_observations(self, *args: object, **kwargs: object) -> int:
        del args, kwargs
        raise self._error


class QuizSubmissionServiceTests(unittest.TestCase):
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
        self.quizzes = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.clock = _Clock()
        self.assertTrue(
            hasattr(quiz_submissions, "QuizSubmissionService"),
            "authenticated application service is not implemented",
        )
        self.service = quiz_submissions.QuizSubmissionService(
            self.profiles,
            self.quizzes,
            utc_now=self.clock,
        )

    def tearDown(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self._temporary_directory.cleanup()

    def _restart(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self.profiles = ProfileStore(self.database_path)
        self.quizzes = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.service = quiz_submissions.QuizSubmissionService(
            self.profiles,
            self.quizzes,
            utc_now=self.clock,
        )

    def _assert_error_code(self, code: str, action: object) -> None:
        self.assertTrue(
            hasattr(quiz_submissions, "QuizSubmissionError"),
            "stable submission error contract is not implemented",
        )
        with self.assertRaises(quiz_submissions.QuizSubmissionError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        error_repr = repr(caught.exception)
        self.assertIn(code, error_repr)
        for secret in (
            self.profile.profile_id,
            self.session.session_id,
            "initial-request-001",
            "item_00000000000000000000000000000001",
        ):
            self.assertNotIn(secret, error_repr)

    def _material(
        self,
        *,
        batch_id: str = "batch-valuehold-001",
        origin_session_id: str | None = None,
    ) -> VerifiedBatchMaterial:
        fixture = self.material_fixture_type(methodName="runTest")
        fixture.setUp()
        fixture.context = replace(
            fixture.context,
            profile_id=self.profile.profile_id,
            session_id=origin_session_id or self.session.session_id,
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
        return material

    def _prepare(
        self,
        *,
        material: VerifiedBatchMaterial | None = None,
        request_id: str = "prepare-request-001",
    ) -> VerifiedBatchMaterial:
        material = material or self._material()
        request = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId=request_id,
            sessionId=material.context.session_id,
            battleId=material.context.battle_id,
            worldId=material.context.world_id,
            battleTier=material.context.battle_tier,
        )
        self.quizzes.create_prepared(
            material,
            request=request,
            profile_id=self.profile.profile_id,
        )
        return material

    @staticmethod
    def _submission(
        model_type: type[InitialSubmission] | type[RevisionSubmission],
        material: VerifiedBatchMaterial,
        request_id: str,
        *,
        correct: tuple[bool, bool, bool],
    ) -> InitialSubmission | RevisionSubmission:
        selections: list[dict[str, object]] = []
        for item, is_correct in zip(material.items, correct, strict=True):
            route = next(
                route
                for route in item.routes
                if (route.procedure_id is None) is is_correct
            )
            selections.append(
                {
                    "itemId": item.item_id,
                    "optionId": route.option_id,
                    "confidence": "certain" if is_correct else "leaning",
                }
            )
        return model_type(
            schemaVersion="wayline.v1",
            requestId=request_id,
            batchId=material.batch_id,
            itemCount=len(selections),
            selections=selections,
        )

    def test_nonzero_initial_returns_only_exact_aggregate_and_persists_receipt(
        self,
    ) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-request-001",
            correct=(False, True, False),
        )

        result = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(result.wrong_count, 2)
        self.assertTrue(result.revision_required)
        self.assertIsNone(result.final_result)
        public_json = result.model_dump_json(by_alias=True)
        for forbidden in (
            "correctOptionId",
            "correctAnswer",
            "trustedSteps",
            "possibleError",
            "reliableMethod",
            "optionId",
        ):
            self.assertNotIn(forbidden, public_json)
        stored = self.quizzes.load(
            material.batch_id,
            profile_id=self.profile.profile_id,
        )
        self.assertEqual(stored.state.value, "revision_open")
        self.assertEqual(stored.initial_result.wrong_count, 2)
        self.assertIsNotNone(stored.initial_receipt)
        self.assertEqual(self.quizzes.pending_observations(self.profile.profile_id), ())
        self.assertEqual(self.clock.calls, 0)

        connection = sqlite3.connect(self.database_path)
        try:
            receipt_json = connection.execute(
                "SELECT receipt_json FROM quiz_transition_receipts "
                "WHERE batch_id = ? AND action = 'initial'",
                (material.batch_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        receipt_payload = json.loads(receipt_json)
        self.assertEqual(receipt_payload["action"], "initial")
        for item in material.items:
            self.assertNotIn(item.placement.correct_option_id, receipt_json)
            self.assertNotIn(item.bundle.blueprint.canonical_answer.display, receipt_json)

    def test_zero_wrong_initial_atomically_reveals_and_drains_observations(
        self,
    ) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-zero-001",
            correct=(True, True, True),
        )

        try:
            result = self.service.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        except Exception as error:  # pragma: no cover - exercised only on RED
            self.fail(f"zero-wrong reveal was not committed: {type(error).__name__}")

        self.assertEqual(result.wrong_count, 0)
        self.assertFalse(result.revision_required)
        self.assertIsNotNone(result.final_result)
        assert result.final_result is not None
        self.assertFalse(result.final_result.revision_used)
        self.assertEqual(result.final_result.final_correct_count, 3)
        self.assertTrue(
            all(item.final_selection.is_correct for item in result.final_result.items)
        )
        machine = self.quizzes.load(
            material.batch_id,
            profile_id=self.profile.profile_id,
        )
        self.assertEqual(machine.state.value, "revealed")
        self.assertEqual(self.quizzes.pending_observations(self.profile.profile_id), ())
        observations = tuple(
            event
            for event in self.profiles.load_events(self.profile.profile_id)
            if event.event_type == "observation"
        )
        self.assertEqual(len(observations), 3)
        self.assertEqual(tuple(event.ordinal for event in observations), (2, 3, 4))
        self.assertEqual(
            {event.session_id for event in observations},
            {self.session.session_id},
        )
        self.assertEqual(
            {event.occurred_at for event in observations},
            {"2026-07-11T21:30:00.123456Z"},
        )
        self.assertEqual(self.clock.calls, 1)

    def test_one_revision_reveals_truth_and_drains_complete_evidence(self) -> None:
        material = self._prepare()
        initial = self._submission(
            InitialSubmission,
            material,
            "initial-revision-001",
            correct=(False, True, False),
        )
        first = self.service.submit_initial(
            initial,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        self.assertEqual(first.wrong_count, 2)
        revision = self._submission(
            RevisionSubmission,
            material,
            "revision-request-001",
            correct=(True, True, True),
        )

        self.assertTrue(
            hasattr(self.service, "submit_revision"),
            "revision application service is not implemented",
        )
        result = self.service.submit_revision(
            revision,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(result.first_pass_wrong_count, 2)
        self.assertEqual(result.final_correct_count, 3)
        self.assertTrue(result.revision_used)
        self.assertEqual(
            tuple(item.self_corrected for item in result.items),
            (True, False, True),
        )
        machine = self.quizzes.load(
            material.batch_id,
            profile_id=self.profile.profile_id,
        )
        self.assertEqual(machine.state.value, "revealed")
        self.assertEqual(self.quizzes.pending_observations(self.profile.profile_id), ())
        observations = tuple(
            event
            for event in self.profiles.load_events(self.profile.profile_id)
            if event.event_type == "observation"
        )
        self.assertEqual(len(observations), 3)
        self.assertEqual({event.batch_wrong_count for event in observations}, {2})
        self.assertEqual(self.clock.calls, 1)

    def test_exact_initial_retries_replay_original_output_without_clock(self) -> None:
        material = self._prepare()
        nonzero = self._submission(
            InitialSubmission,
            material,
            "initial-replay-001",
            correct=(False, True, False),
        )
        first = self.service.submit_initial(
            nonzero,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        replay = self.service.submit_initial(
            nonzero,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(
            replay.model_dump_json(by_alias=True),
            first.model_dump_json(by_alias=True),
        )
        self.assertEqual(self.clock.calls, 0)

    def test_zero_wrong_retry_after_restart_does_not_rebuild_events_or_clock(
        self,
    ) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-zero-replay-001",
            correct=(True, True, True),
        )
        first = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        original_events = tuple(
            event
            for event in self.profiles.load_events(self.profile.profile_id)
            if event.event_type == "observation"
        )
        self.assertEqual(self.clock.calls, 1)
        self._restart()

        replay = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(
            replay.model_dump_json(by_alias=True),
            first.model_dump_json(by_alias=True),
        )
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(
            tuple(
                event
                for event in self.profiles.load_events(self.profile.profile_id)
                if event.event_type == "observation"
            ),
            original_events,
        )

    def test_exact_revision_retry_returns_original_result_without_clock(self) -> None:
        material = self._prepare()
        initial = self._submission(
            InitialSubmission,
            material,
            "initial-before-revision-replay-001",
            correct=(False, True, False),
        )
        self.service.submit_initial(
            initial,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        revision = self._submission(
            RevisionSubmission,
            material,
            "revision-replay-001",
            correct=(True, True, True),
        )
        first = self.service.submit_revision(
            revision,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        self.assertEqual(self.clock.calls, 1)
        self._restart()

        replay = self.service.submit_revision(
            revision,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(
            replay.model_dump_json(by_alias=True),
            first.model_dump_json(by_alias=True),
        )
        self.assertEqual(self.clock.calls, 1)

    def test_initial_retry_after_revision_returns_original_aggregate_only(self) -> None:
        material = self._prepare()
        initial = self._submission(
            InitialSubmission,
            material,
            "initial-before-later-revision-001",
            correct=(False, True, False),
        )
        original = self.service.submit_initial(
            initial,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        revision = self._submission(
            RevisionSubmission,
            material,
            "revision-before-initial-retry-001",
            correct=(True, True, True),
        )
        self.service.submit_revision(
            revision,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        self.assertEqual(self.clock.calls, 1)

        try:
            replay = self.service.submit_initial(
                initial,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        except quiz_submissions.QuizSubmissionError as error:
            self.fail(f"exact initial retry failed with {error.code}")

        self.assertEqual(
            replay.model_dump_json(by_alias=True),
            original.model_dump_json(by_alias=True),
        )
        self.assertIsNone(replay.final_result)
        self.assertEqual(self.clock.calls, 1)

    def test_noncurrent_identity_is_coalesced_to_one_private_error(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-auth-001",
            correct=(False, True, False),
        )
        other_profile = self.profiles.create_profile(
            request_id="profile-request-other-002"
        )
        other_session = self.profiles.create_session(
            request_id="session-request-other-002",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )

        cases = (
            ("profile-missing-999", "session-missing-999"),
            (self.profile.profile_id, other_session.session_id),
        )
        for profile_id, session_id in cases:
            with self.subTest(profile_id=profile_id):
                self._assert_error_code(
                    "session_not_current",
                    lambda profile_id=profile_id, session_id=session_id: (
                        self.service.submit_initial(
                            submission,
                            profile_id=profile_id,
                            current_session_id=session_id,
                        )
                    ),
                )

        replacement = self.profiles.create_session(
            request_id="session-request-replacement-003",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self._assert_error_code(
            "session_not_current",
            lambda: self.service.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )
        self.assertIsNone(
            self.quizzes.load(
                material.batch_id,
                profile_id=self.profile.profile_id,
            ).initial_submission
        )
        self.session = replacement

    def test_inaccessible_batch_is_coalesced_without_existence_or_owner_leak(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-unavailable-001",
            correct=(False, True, False),
        )
        missing = InitialSubmission.model_validate(
            {
                **submission.model_dump(mode="json", by_alias=True),
                "batchId": "batch-missing-999",
            }
        )
        self._assert_error_code(
            "batch_unavailable",
            lambda: self.service.submit_initial(
                missing,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )

        other_profile = self.profiles.create_profile(
            request_id="profile-request-owner-check-002"
        )
        other_session = self.profiles.create_session(
            request_id="session-request-owner-check-002",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self._assert_error_code(
            "batch_unavailable",
            lambda: self.service.submit_initial(
                submission,
                profile_id=other_profile.profile_id,
                current_session_id=other_session.session_id,
            ),
        )

    def test_changed_or_distinct_second_initial_has_stable_conflict_codes(self) -> None:
        material = self._prepare()
        original = self._submission(
            InitialSubmission,
            material,
            "initial-request-001",
            correct=(False, True, False),
        )
        self.service.submit_initial(
            original,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        changed = self._submission(
            InitialSubmission,
            material,
            "initial-request-001",
            correct=(True, True, False),
        )
        distinct = self._submission(
            InitialSubmission,
            material,
            "initial-request-002",
            correct=(False, True, False),
        )

        self._assert_error_code(
            "idempotency_conflict",
            lambda: self.service.submit_initial(
                changed,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )
        self._assert_error_code(
            "quiz_state_conflict",
            lambda: self.service.submit_initial(
                distinct,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )

    def test_restart_recovers_exact_request_from_durable_initial_lock(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-lock-crash-001",
            correct=(False, True, False),
        )
        crashing = quiz_submissions.QuizSubmissionService(
            self.profiles,
            _CrashAfterInitialLock(self.quizzes),
            utc_now=self.clock,
        )

        with self.assertRaisesRegex(RuntimeError, "simulated process interruption"):
            crashing.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        self.assertEqual(
            self.quizzes.load(
                material.batch_id,
                profile_id=self.profile.profile_id,
            ).state.value,
            "initial_locked",
        )
        self._restart()

        result = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(result.wrong_count, 2)
        self.assertEqual(self.clock.calls, 0)

    def test_reveal_commit_crash_replays_without_rebuilding_events(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-reveal-crash-001",
            correct=(True, True, True),
        )
        self.quizzes._failpoint_stage = "after_reveal_commit"

        with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
            self.service.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(
            self.quizzes.load(
                material.batch_id,
                profile_id=self.profile.profile_id,
            ).state.value,
            "revealed",
        )
        self.assertEqual(len(self.quizzes.pending_observations(self.profile.profile_id)), 3)
        self.quizzes._failpoint_stage = None
        self._restart()

        replay = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(replay.wrong_count, 0)
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(self.quizzes.pending_observations(self.profile.profile_id), ())

    def test_outbox_drain_crash_fails_closed_then_retry_completes(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-drain-crash-001",
            correct=(True, True, True),
        )
        self.quizzes._failpoint_stage = (
            f"after_profile_append:{material.items[0].item_id}"
        )

        self._assert_error_code(
            "evidence_sync_unavailable",
            lambda: self.service.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(
            self.quizzes.load(
                material.batch_id,
                profile_id=self.profile.profile_id,
            ).state.value,
            "revealed",
        )
        self.quizzes._failpoint_stage = None
        self._restart()

        replay = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )

        self.assertEqual(replay.wrong_count, 0)
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(
            len(
                tuple(
                    event
                    for event in self.profiles.load_events(self.profile.profile_id)
                    if event.event_type == "observation"
                )
            ),
            3,
        )

    def test_bad_clock_fails_before_reveal_write_and_retry_can_resume(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-bad-clock-001",
            correct=(True, True, True),
        )
        bad_service = quiz_submissions.QuizSubmissionService(
            self.profiles,
            self.quizzes,
            utc_now=lambda: datetime(2026, 7, 11, 21, 30, 0),
        )

        with self.assertRaises(quiz_submissions.QuizSubmissionError) as caught:
            bad_service.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        self.assertEqual(caught.exception.code, "integrity_failure")
        machine = self.quizzes.load(
            material.batch_id,
            profile_id=self.profile.profile_id,
        )
        self.assertEqual(machine.state.value, "initial_locked")
        self.assertIsNone(machine.initial_result)

        result = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        self.assertEqual(result.wrong_count, 0)

    def test_closed_origin_session_is_valid_but_reveal_uses_actual_current_session(
        self,
    ) -> None:
        material = self._prepare()
        origin_session_id = self.session.session_id
        current = self.profiles.create_session(
            request_id="session-request-resume-002",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.session = current
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-resumed-session-001",
            correct=(True, True, True),
        )

        result = self.service.submit_initial(
            submission,
            profile_id=self.profile.profile_id,
            current_session_id=current.session_id,
        )

        self.assertEqual(result.wrong_count, 0)
        observations = tuple(
            event
            for event in self.profiles.load_events(self.profile.profile_id)
            if event.event_type == "observation"
        )
        self.assertEqual({event.session_id for event in observations}, {current.session_id})
        self.assertNotEqual(current.session_id, origin_session_id)

    def test_machine_rejects_unknown_option_and_missing_confidence_privately(self) -> None:
        material = self._prepare()
        valid = self._submission(
            InitialSubmission,
            material,
            "initial-tamper-001",
            correct=(False, True, False),
        )
        selections = list(valid.selections)
        selections[0] = selections[0].model_copy(
            update={"option_id": "option-tampered-999"}
        )
        unknown_option = valid.model_copy(update={"selections": tuple(selections)})
        self._assert_error_code(
            "invalid_submission",
            lambda: self.service.submit_initial(
                unknown_option,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )

        missing = list(valid.selections)
        missing[0] = AnswerSelection.model_construct(
            item_id=valid.selections[0].item_id,
            option_id=valid.selections[0].option_id,
        )
        missing_confidence = valid.model_copy(update={"selections": tuple(missing)})
        self._assert_error_code(
            "invalid_submission",
            lambda: self.service.submit_initial(
                missing_confidence,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )
        machine = self.quizzes.load(
            material.batch_id,
            profile_id=self.profile.profile_id,
        )
        self.assertEqual(machine.state.value, "ready")

    def test_changed_or_distinct_second_revision_has_stable_conflict_codes(self) -> None:
        material = self._prepare()
        initial = self._submission(
            InitialSubmission,
            material,
            "initial-before-revision-conflict-001",
            correct=(False, True, False),
        )
        self.service.submit_initial(
            initial,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        original = self._submission(
            RevisionSubmission,
            material,
            "revision-conflict-001",
            correct=(True, True, True),
        )
        self.service.submit_revision(
            original,
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        changed = self._submission(
            RevisionSubmission,
            material,
            "revision-conflict-001",
            correct=(True, False, True),
        )
        distinct = self._submission(
            RevisionSubmission,
            material,
            "revision-conflict-002",
            correct=(True, True, True),
        )

        self._assert_error_code(
            "idempotency_conflict",
            lambda: self.service.submit_revision(
                changed,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )
        self._assert_error_code(
            "quiz_state_conflict",
            lambda: self.service.submit_revision(
                distinct,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            ),
        )
        self.assertEqual(self.clock.calls, 1)

    def test_missing_preparation_origin_is_integrity_failure(self) -> None:
        material = self._prepare()
        origin_session_id = self.session.session_id
        current = self.profiles.create_session(
            request_id="session-request-origin-check-002",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.session = current
        self.profiles._connection.execute(
            "DELETE FROM local_sessions WHERE session_id = ?",
            (origin_session_id,),
        )
        self.profiles._connection.commit()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-origin-missing-001",
            correct=(False, True, False),
        )

        self._assert_error_code(
            "integrity_failure",
            lambda: self.service.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=current.session_id,
            ),
        )

    def test_concurrent_identical_initials_share_one_reveal_and_clock(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-concurrent-001",
            correct=(True, True, True),
        )
        start = threading.Barrier(2)

        def submit() -> str:
            profiles = ProfileStore(self.database_path)
            quizzes = QuizStore(
                self.database_path,
                timeout_seconds=0.25,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
            service = quiz_submissions.QuizSubmissionService(
                profiles,
                quizzes,
                utc_now=self.clock,
            )
            try:
                start.wait(timeout=5)
                result = service.submit_initial(
                    submission,
                    profile_id=self.profile.profile_id,
                    current_session_id=self.session.session_id,
                )
                return result.model_dump_json(by_alias=True)
            finally:
                quizzes.close()
                profiles.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outputs = tuple(executor.map(lambda _: submit(), range(2)))

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(self.clock.calls, 1)
        self.assertEqual(
            len(
                tuple(
                    event
                    for event in self.profiles.load_events(self.profile.profile_id)
                    if event.event_type == "observation"
                )
            ),
            3,
        )

    def test_command_lock_is_shared_only_by_canonical_database_identity(self) -> None:
        second_store = QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        try:
            try:
                first = quiz_submissions._command_lock(self.quizzes)
                second = quiz_submissions._command_lock(second_store)
            except TypeError:
                self.fail("command locks still require caller-controlled identities")
            self.assertIs(first, second)
        finally:
            second_store.close()

    def test_drain_failures_map_to_precise_non_sensitive_error_codes(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-drain-mapping-001",
            correct=(False, True, False),
        )
        cases = (
            (QuizStoreBusyError("secret-drain-detail"), "storage_busy"),
            (QuizStoreCorruptionError("secret-drain-detail"), "integrity_failure"),
            (EventLogCorruptionError("secret-drain-detail"), "integrity_failure"),
            (IdentityStoreCorruptionError("secret-drain-detail"), "integrity_failure"),
            (ProfileIdempotencyConflictError("secret-drain-detail"), "integrity_failure"),
            (SemanticEventConflictError("secret-drain-detail"), "integrity_failure"),
            (EventOrderError("secret-drain-detail"), "integrity_failure"),
            (OutboxReservationError("secret-drain-detail"), "integrity_failure"),
            (QuizStoreError("secret-drain-detail"), "evidence_sync_unavailable"),
            (ProfileStoreError("secret-drain-detail"), "evidence_sync_unavailable"),
            (RuntimeError("secret-drain-detail"), "evidence_sync_unavailable"),
        )

        for failure, expected_code in cases:
            with self.subTest(failure=type(failure).__name__):
                service = quiz_submissions.QuizSubmissionService(
                    self.profiles,
                    _DrainFailureStore(self.quizzes, failure),
                    utc_now=self.clock,
                )
                with self.assertRaises(quiz_submissions.QuizSubmissionError) as caught:
                    service.submit_initial(
                        submission,
                        profile_id=self.profile.profile_id,
                        current_session_id=self.session.session_id,
                    )
                self.assertEqual(caught.exception.code, expected_code)
                self.assertEqual(str(caught.exception), expected_code)
                self.assertNotIn("secret-drain-detail", repr(caught.exception))

    def test_drain_evidence_replay_corruption_maps_to_integrity_failure(self) -> None:
        material = self._prepare()
        submission = self._submission(
            InitialSubmission,
            material,
            "initial-drain-replay-corruption-001",
            correct=(False, True, False),
        )
        service = quiz_submissions.QuizSubmissionService(
            self.profiles,
            _DrainFailureStore(
                self.quizzes,
                EvidenceReplayError("secret-replay-detail"),
            ),
            utc_now=self.clock,
        )

        try:
            service.submit_initial(
                submission,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        except EvidenceReplayError:
            self.fail("evidence replay corruption escaped the stable boundary")
        except quiz_submissions.QuizSubmissionError as error:
            self.assertEqual(error.code, "integrity_failure")
            self.assertEqual(str(error), "integrity_failure")
            self.assertNotIn("secret-replay-detail", repr(error))
        else:
            self.fail("evidence replay corruption was not rejected")


if __name__ == "__main__":
    unittest.main()
