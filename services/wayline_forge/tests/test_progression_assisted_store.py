from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.batch_material import VerifiedBatchMaterial
from services.wayline_forge.app.assisted_route_store import AssistedRouteStore
from services.wayline_forge.app.battle_preparation import BattlePreparationService
from services.wayline_forge.app.contracts import (
    AssistedSelection,
    BattleQuizRequest,
    InitialSubmission,
    RevisionSubmission,
)
from services.wayline_forge.app.events import (
    OUTCOME_EVENT_SCHEMA_VERSION,
    AssistedRouteCompletionEvent,
    BossCompletionEvent,
    SealTrialCompletionEvent,
)
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.progression import (
    AssistedRouteCompletionRequest,
    AssistedRoutePreparationRequest,
    ProgressionCommandError,
    ProgressionCommandService,
)
from services.wayline_forge.app.quiz_machine import close_quiz
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.app.quiz_submissions import QuizSubmissionService


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc)
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        result = self.value
        self.value += timedelta(minutes=1)
        return result


class _UnusedSpecialPreparer:
    async def prepare_seal_trial(self, *args, **kwargs):
        raise AssertionError("assisted replay must reuse finalized verified trials")

    async def prepare_second_wind(self, *args, **kwargs):
        raise AssertionError("assisted completion replay must not prepare content")

    async def prepare_assisted_route(self, *args, **kwargs):
        raise AssertionError("assisted completion replay must not prepare content")


class _RecordingAssistedOrchestrator:
    def __init__(self, material: VerifiedBatchMaterial) -> None:
        self.material = material
        self.material_calls: list[dict[str, object]] = []

    async def build_verified_material(self, **kwargs: object) -> VerifiedBatchMaterial:
        self.material_calls.append(kwargs)
        return self.material


class AssistedRouteProductionStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from services.wayline_forge.tests.test_batch_material import BatchMaterialTests

        BatchMaterialTests.setUpClass()
        cls.material_fixture_type = BatchMaterialTests
        cls.verifier = BatchMaterialTests.verifier

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "wayline.sqlite"
        self.profiles = ProfileStore(self.path)
        with patch(
            "services.wayline_forge.app.profile_store._server_timestamp",
            return_value="2026-07-12T10:00:00.000000Z",
        ):
            self.profile = self.profiles.create_profile(
                request_id="profile-assisted-store"
            )
            self.session = self.profiles.create_session(
                request_id="session-assisted-store",
                profile_id=self.profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
        self.quizzes = QuizStore(
            self.path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.assisted_routes = AssistedRouteStore(
            self.path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        from services.wayline_forge.tests.test_assisted_route_machine import (
            AssistedRouteMachineTests,
        )

        assisted_fixture = AssistedRouteMachineTests()
        assisted_fixture.verifier = self.verifier
        self.fresh_material = assisted_fixture._material(
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
        )
        self.orchestrator = _RecordingAssistedOrchestrator(self.fresh_material)
        self.preparer = BattlePreparationService(
            self.profiles,
            self.quizzes,
            self.orchestrator,
            assisted_route_store=self.assisted_routes,
        )
        self.clock = _Clock()
        self.submissions = QuizSubmissionService(
            self.profiles,
            self.quizzes,
            utc_now=self.clock,
        )
        self.progression = ProgressionCommandService(
            self.profiles,
            self.quizzes,
            self.preparer,
            assisted_route_store=self.assisted_routes,
            utc_now=self.clock,
        )
        self.profiles.append(
            BossCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="boss-assisted-store",
                idempotency_id="boss-assisted-store-request",
                ordinal=2,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_boss",
                occurred_at="2026-07-12T10:30:00Z",
                combat_won=True,
                final_correct=5,
                item_count=8,
                is_campaign_finale=False,
                batch_id="batch-boss-assisted-store",
            )
        )

    def tearDown(self) -> None:
        self.assisted_routes.close()
        self.quizzes.close()
        self.profiles.close()
        self.temporary_directory.cleanup()

    def _material(self, attempt: int) -> VerifiedBatchMaterial:
        fixture = self.material_fixture_type(methodName="runTest")
        fixture.setUp()
        fixture.context = replace(
            fixture.context,
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            battle_id=f"valuehold_seal_trial_{attempt}",
            battle_tier="seal_trial",
        )
        builder = fixture.builder()
        for index, slot in enumerate(fixture.slots, start=1):
            builder.accept_live(
                fixture.bundle_for(slot.request),
                item_instance_id=f"item_{attempt * 1000 + index:032x}",
            )
        material = builder.finalize()
        return VerifiedBatchMaterial._create(
            batch_id=f"batch-seal-production-{attempt}",
            context=material.context,
            plan_contract=material.plan_contract,
            items=material.items,
        )

    @staticmethod
    def _submission(model_type, material, request_id: str):
        selections = []
        for index, item in enumerate(material.items):
            is_correct = index == 0
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
            itemCount=3,
            selections=selections,
        )

    def _persist_missed_trial(self, attempt: int) -> VerifiedBatchMaterial:
        material = self._material(attempt)
        request = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId=f"prepare-seal-production-{attempt}",
            sessionId=self.session.session_id,
            battleId=f"valuehold_seal_trial_{attempt}",
            worldId="valuehold",
            battleTier="seal_trial",
        )
        self.quizzes.create_prepared(
            material,
            request=request,
            profile_id=self.profile.profile_id,
        )
        self.submissions.submit_initial(
            self._submission(
                InitialSubmission,
                material,
                f"initial-seal-production-{attempt}",
            ),
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        self.submissions.submit_revision(
            self._submission(
                RevisionSubmission,
                material,
                f"revision-seal-production-{attempt}",
            ),
            profile_id=self.profile.profile_id,
            current_session_id=self.session.session_id,
        )
        revealed = self.quizzes.load(
            material.batch_id,
            profile_id=self.profile.profile_id,
        )
        self.quizzes.save_transition(
            close_quiz(revealed, expected_version=revealed.version),
            profile_id=self.profile.profile_id,
            expected_version=revealed.version,
        )
        self.quizzes.drain_observations(
            self.profile.profile_id,
            profile_store=self.profiles,
        )
        self.profiles.append(
            SealTrialCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id=f"seal-production-{attempt}",
                idempotency_id=f"seal-production-request-{attempt}",
                ordinal=len(self.profiles.load_events(self.profile.profile_id)) + 1,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id=f"valuehold_seal_trial_{attempt}",
                occurred_at=f"2026-07-12T11:0{attempt}:30Z",
                attempt_number=attempt,
                passed=False,
                final_correct=1,
                item_count=3,
                batch_id=material.batch_id,
                gate_recheck_sha256=f"{attempt}" * 64,
            )
        )
        return material

    def _prepare_for_concurrent_completion(self):
        self._persist_missed_trial(1)
        self._persist_missed_trial(2)
        prepared = asyncio.run(self.progression.prepare_assisted_route(
            AssistedRoutePreparationRequest(
                request_id="prepare-assisted-concurrent",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
            )
        ))
        selections = tuple(
            AssistedSelection(
                itemId=public_item.item_id,
                optionId=next(
                    route.option_id
                    for route in verified_item.routes
                    if route.procedure_id is not None
                ),
                confidence="leaning",
            )
            for public_item, verified_item in zip(
                prepared.batch.items,
                self.fresh_material.items[1:],
                strict=True,
            )
        )
        return prepared, selections

    def _concurrent_completion(self, request: AssistedRouteCompletionRequest):
        profiles = ProfileStore(self.path)
        quizzes = QuizStore(
            self.path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        routes = AssistedRouteStore(
            self.path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
            timeout_seconds=2.0,
        )
        service = ProgressionCommandService(
            profiles,
            quizzes,
            _UnusedSpecialPreparer(),
            assisted_route_store=routes,
            utc_now=_Clock(),
        )
        try:
            return service.complete_assisted_route(request)
        except ProgressionCommandError as error:
            return error.code
        finally:
            routes.close()
            quizzes.close()
            profiles.close()

    def test_concurrent_exact_completions_return_one_result_and_one_event(self) -> None:
        prepared, selections = self._prepare_for_concurrent_completion()
        request = AssistedRouteCompletionRequest(
            request_id="complete-assisted-concurrent-exact",
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            route_id=prepared.batch.route_id,
            selections=selections,
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self._concurrent_completion, request)
            second = executor.submit(self._concurrent_completion, request)
            results = (first.result(timeout=10), second.result(timeout=10))

        self.assertEqual(results[0], results[1])
        events = tuple(
            item
            for item in self.profiles.load_events(self.profile.profile_id)
            if isinstance(item, AssistedRouteCompletionEvent)
        )
        self.assertEqual(len(events), 1)

    def test_concurrent_different_completions_have_one_target_conflict(self) -> None:
        prepared, selections = self._prepare_for_concurrent_completion()
        requests = tuple(
            AssistedRouteCompletionRequest(
                request_id=f"complete-assisted-concurrent-{suffix}",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                route_id=prepared.batch.route_id,
                selections=selections,
            )
            for suffix in ("a", "b")
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(
                executor.submit(self._concurrent_completion, request)
                for request in requests
            )
            results = tuple(future.result(timeout=10) for future in futures)

        self.assertEqual(
            sum(result == "target_already_completed" for result in results),
            1,
        )
        self.assertEqual(
            sum(not isinstance(result, str) for result in results),
            1,
        )
        events = tuple(
            item
            for item in self.profiles.load_events(self.profile.profile_id)
            if isinstance(item, AssistedRouteCompletionEvent)
        )
        self.assertEqual(len(events), 1)

    def test_completion_racing_normal_quiz_allows_quiz_only_after_route_closes(self) -> None:
        prepared, selections = self._prepare_for_concurrent_completion()
        completion_request = AssistedRouteCompletionRequest(
            request_id="complete-assisted-race-normal",
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            route_id=prepared.batch.route_id,
            selections=selections,
        )
        fixture = self.material_fixture_type(methodName="runTest")
        fixture.setUp()
        fixture.context = replace(
            fixture.context,
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id="valuehold",
            battle_id="valuehold_route_1",
            battle_tier="route_1",
        )
        normal_material = fixture.complete_material()
        normal_request = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId="prepare-normal-after-assisted",
            sessionId=self.session.session_id,
            battleId="valuehold_route_1",
            worldId="valuehold",
            battleTier="route_1",
        )
        completion_has_lock = Event()
        normal_store_ready = Event()
        race_barrier = Barrier(2)

        def complete():
            profiles = ProfileStore(self.path)
            quizzes = QuizStore(
                self.path,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
            routes = AssistedRouteStore(
                self.path,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
                timeout_seconds=2.0,
            )
            service = ProgressionCommandService(
                profiles,
                quizzes,
                _UnusedSpecialPreparer(),
                assisted_route_store=routes,
                utc_now=_Clock(),
            )
            original_append = profiles._append_event_in_transaction

            def pause_after_insert(event, canonical):
                result = original_append(event, canonical)
                completion_has_lock.set()
                race_barrier.wait(timeout=5)
                return result

            try:
                with patch.object(
                    profiles,
                    "_append_event_in_transaction",
                    side_effect=pause_after_insert,
                ):
                    return service.complete_assisted_route(completion_request)
            finally:
                routes.close()
                quizzes.close()
                profiles.close()

        def prepare_normal():
            quizzes = QuizStore(
                self.path,
                timeout_seconds=2.0,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
            try:
                normal_store_ready.set()
                self.assertTrue(completion_has_lock.wait(timeout=5))
                race_barrier.wait(timeout=5)
                return quizzes.create_prepared(
                    normal_material,
                    request=normal_request,
                    profile_id=self.profile.profile_id,
                )
            finally:
                quizzes.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            normal_future = executor.submit(prepare_normal)
            self.assertTrue(normal_store_ready.wait(timeout=5))
            completion_future = executor.submit(complete)
            completion = completion_future.result(timeout=10)
            normal = normal_future.result(timeout=10)

        self.assertTrue(completion.world_cleared)
        self.assertEqual(normal.material.batch_id, normal_material.batch_id)
        self.assertEqual(
            self.quizzes.resumable_batch_id(self.profile.profile_id),
            normal_material.batch_id,
        )

    def test_assisted_route_uses_fresh_store_material_scores_zero_and_replays(self) -> None:
        missed_trial_materials = (
            self._persist_missed_trial(1),
            self._persist_missed_trial(2),
        )

        prepared = asyncio.run(self.progression.prepare_assisted_route(
            AssistedRoutePreparationRequest(
                request_id="prepare-assisted-production",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
            )
        ))

        old_item_ids = {
            item.item_id
            for material in missed_trial_materials
            for item in material.items
        }
        self.assertTrue(old_item_ids.isdisjoint({
            prepared.batch.worked_example.item_id,
            *(item.item_id for item in prepared.batch.items),
        }))
        self.assertEqual(len(self.orchestrator.material_calls), 1)
        self.assertEqual(len(prepared.batch.items), 2)
        self.assertTrue(prepared.batch.worked_example.correct_answer)
        for item in prepared.batch.items:
            self.assertFalse(hasattr(item, "correct_option_id"))
            self.assertFalse(hasattr(item, "reliable_method"))
            self.assertFalse(hasattr(item, "trusted_steps"))
        source_session_id = self.session.session_id
        with patch(
            "services.wayline_forge.app.profile_store._server_timestamp",
            return_value="2026-07-12T12:00:00.000000Z",
        ):
            self.session = self.profiles.create_session(
                request_id="session-assisted-store-recovery",
                profile_id=self.profile.profile_id,
                client_build="mac-demo-0.1.0",
            )
        recovered = asyncio.run(self.progression.prepare_assisted_route(
            AssistedRoutePreparationRequest(
                request_id="prepare-assisted-recovered-session",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
            )
        ))
        self.assertEqual(recovered.batch, prepared.batch)
        self.assertEqual(len(self.orchestrator.material_calls), 1)
        self.assertEqual(
            self.assisted_routes.load(
                prepared.batch.route_id,
                profile_id=self.profile.profile_id,
            ).source_session_id,
            source_session_id,
        )
        selections = tuple(
            AssistedSelection(
                itemId=public_item.item_id,
                optionId=next(
                    route.option_id
                    for route in verified_item.routes
                    if route.procedure_id is not None
                ),
                confidence="leaning",
            )
            for public_item, verified_item in zip(
                prepared.batch.items,
                self.fresh_material.items[1:],
                strict=True,
            )
        )
        forged = AssistedSelection(
            itemId=selections[0].item_id,
            optionId="forged-option-id",
            confidence=selections[0].confidence,
        )
        with self.assertRaises(ProgressionCommandError) as rejected:
            self.progression.complete_assisted_route(
                AssistedRouteCompletionRequest(
                    request_id="complete-assisted-forged-option",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                    route_id=prepared.batch.route_id,
                    selections=(forged, selections[1]),
                )
            )
        self.assertEqual(rejected.exception.code, "quiz_context_mismatch")
        state_before = self.profiles.load_state(self.profile.profile_id)
        result = self.progression.complete_assisted_route(
            AssistedRouteCompletionRequest(
                request_id="complete-assisted-production",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                route_id=prepared.batch.route_id,
                selections=selections,
            )
        )

        self.assertTrue(result.world_cleared)
        self.assertEqual(result.final_correct, 0)
        self.assertEqual(len(result.items), 2)
        for item in result.items:
            self.assertTrue(item.correct_option_id)
            self.assertEqual(
                item.is_correct,
                item.selected_option_id == item.correct_option_id,
            )
            self.assertTrue(item.reliable_method)
            self.assertTrue(item.trusted_steps)
        durable = self.profiles.load_events(self.profile.profile_id)[-1]
        self.assertIsInstance(durable, AssistedRouteCompletionEvent)
        self.assertEqual(len(durable.supported_item_ids), 2)
        self.assertEqual(durable.final_correct, sum(durable.correctness))
        self.assertEqual(durable.route_revision, "fresh-assisted-v1")
        self.assertEqual(
            durable.material_sha256,
            self.fresh_material.batch_material_sha256,
        )
        self.assertEqual(durable.correct_option_ids, tuple(
            item.correct_option_id for item in result.items
        ))
        self.assertEqual(durable.reliable_methods, tuple(
            item.reliable_method for item in result.items
        ))
        self.assertEqual(durable.trusted_steps, tuple(
            item.trusted_steps for item in result.items
        ))
        state_after = self.profiles.load_state(self.profile.profile_id)
        self.assertEqual(state_after.procedures, state_before.procedures)
        self.assertEqual(state_after.skills, state_before.skills)
        self.assertEqual(
            state_after.world("valuehold").valid_item_count,
            state_before.world("valuehold").valid_item_count,
        )
        self.assertEqual(
            len(state_after.answer_records),
            len(state_before.answer_records) + 2,
        )
        preparation_replay = asyncio.run(self.progression.prepare_assisted_route(
            AssistedRoutePreparationRequest(
                request_id="prepare-assisted-recovered-session",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
            )
        ))
        self.assertEqual(preparation_replay, recovered)
        self.assertEqual(len(self.orchestrator.material_calls), 1)
        with self.assertRaises(ProgressionCommandError) as new_preparation:
            asyncio.run(self.progression.prepare_assisted_route(
                AssistedRoutePreparationRequest(
                    request_id="prepare-assisted-after-clear",
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                )
            ))
        self.assertEqual(
            new_preparation.exception.code,
            "target_already_completed",
        )
        self.assertIsNone(self.quizzes.resumable_batch_id(self.profile.profile_id))
        self.assisted_routes.close()
        self.quizzes.close()
        self.profiles.close()
        self.profiles = ProfileStore(self.path)
        self.quizzes = QuizStore(
            self.path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.assisted_routes = AssistedRouteStore(
            self.path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.progression = ProgressionCommandService(
            self.profiles,
            self.quizzes,
            _UnusedSpecialPreparer(),
            assisted_route_store=self.assisted_routes,
            utc_now=self.clock,
        )
        clock_calls = self.clock.calls

        replay = self.progression.complete_assisted_route(
            AssistedRouteCompletionRequest(
                request_id="complete-assisted-production",
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                route_id=prepared.batch.route_id,
                selections=selections,
            )
        )

        self.assertEqual(replay, result)
        self.assertEqual(self.clock.calls, clock_calls)


if __name__ == "__main__":
    unittest.main()
