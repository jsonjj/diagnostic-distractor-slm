from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from services.wayline_forge.app.adaptive_planner import plan_slots
from services.wayline_forge.app.application import WaylineApplication
from services.wayline_forge.app.assisted_route_store import AssistedRouteStore
from services.wayline_forge.app.battle_preparation import BattlePreparationError
from services.wayline_forge.app.batch_material import (
    BatchContext,
    BatchMaterialBuilder,
)
from services.wayline_forge.app.campaign_catalog import CampaignCatalog
from services.wayline_forge.app.contracts import (
    AnswerSelection,
    BattleQuizRequest,
    InitialSubmission,
    ProfileCreate,
    RevisionSubmission,
    SessionCreate,
)
from services.wayline_forge.app.curriculum import (
    CURRICULUM_V1_SHA256,
    Curriculum,
)
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.events import ObservationEvent, WorldActivatedEvent
from services.wayline_forge.app.gate_query import BossGateQueryError
from services.wayline_forge.app.identity_lifecycle import IdentityLifecycleError
from services.wayline_forge.app.orchestrator import BatchPreparationOrchestrator
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.profile_store import ProfileNotFoundError
from services.wayline_forge.app.providers.distractor import (
    ProviderError,
    RawSlmGeneration,
    SlmRequest,
)
from services.wayline_forge.app.providers.recorded import RecordedDistractorProvider
from services.wayline_forge.app.progression import BattleCompletionRequest
from services.wayline_forge.app.quiz_machine import QuizState
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.app.quiz_submissions import QuizSubmissionError
from services.wayline_forge.app.reviewed_cache import ReviewedCache
from services.wayline_forge.app.slm_prompt import build_slm_request
from services.wayline_forge.app.slot_materializer import materialize_slots
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class _FrozenFixtures:
    recordings: Mapping[str, RawSlmGeneration]
    requests: tuple[SlmRequest, ...]
    correct_by_prompt: Mapping[str, str]
    wrong_by_prompt: Mapping[str, tuple[str, ...]]


class _DeterministicClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.calls = 0
        self.sleep_calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls += 1
        await asyncio.sleep(0.001)
        self.now += max(0.0, float(seconds))


class _UtcClock:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return datetime(
            2026,
            7,
            12,
            12,
            30,
            tzinfo=timezone.utc,
        )


class _DeterministicIds:
    def __init__(self) -> None:
        self.batch_count = 0
        self.item_count = 0

    def batch(self) -> str:
        self.batch_count += 1
        return f"batch-valuehold-app-{self.batch_count:03d}"

    def item(self) -> str:
        self.item_count += 1
        return f"item_{self.item_count:032x}"


class _CountingRecordedProvider:
    """Count calls while delegating every result to the production provider."""

    def __init__(self, recordings: Mapping[str, RawSlmGeneration]) -> None:
        self._delegate = RecordedDistractorProvider(recordings)
        self.requests: list[SlmRequest] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    async def generate(self, request: SlmRequest) -> RawSlmGeneration:
        self.requests.append(request)
        return await self._delegate.generate(request)


class WaylineApplicationValueholdTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = DistractorVerifier.for_tests()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "wayline.sqlite3"
        self.cache_path = self.root / "reviewed-cache.sqlite3"
        build_cache = ReviewedCache.open_build(
            self.cache_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        build_cache.close()
        self.profiles = ProfileStore(self.database_path)
        self.quizzes = self._open_quiz_store()
        self.assisted_routes = self._open_assisted_route_store()
        self.cache = self._open_cache()
        self._install_recordings({})

    def tearDown(self) -> None:
        self.cache.close()
        self.assisted_routes.close()
        self.quizzes.close()
        self.profiles.close()
        self.temporary.cleanup()

    def _open_quiz_store(self) -> QuizStore:
        return QuizStore(
            self.database_path,
            timeout_seconds=0.05,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def _open_assisted_route_store(self) -> AssistedRouteStore:
        return AssistedRouteStore(
            self.database_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
            timeout_seconds=0.05,
        )

    def _open_cache(self) -> ReviewedCache:
        return ReviewedCache.open_learner(
            self.cache_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def _install_recordings(
        self,
        recordings: Mapping[str, RawSlmGeneration],
    ) -> None:
        self.provider = _CountingRecordedProvider(recordings)
        self.monotonic = _DeterministicClock()
        self.utc = _UtcClock()
        self.ids = _DeterministicIds()
        orchestrator = BatchPreparationOrchestrator(
            store=self.quizzes,
            compiler=self.verifier.compiler,
            verifier=self.verifier,
            manifest=self.verifier.manifest,
            provider=self.provider,
            reviewed_cache=self.cache,
            monotonic=self.monotonic,
            sleeper=self.monotonic.sleep,
            batch_id_factory=self.ids.batch,
            item_id_factory=self.ids.item,
        )
        self.application = WaylineApplication(
            profile_store=self.profiles,
            quiz_store=self.quizzes,
            orchestrator=orchestrator,
            assisted_route_store=self.assisted_routes,
            utc_now=self.utc,
        )

    def _restart(
        self,
        recordings: Mapping[str, RawSlmGeneration],
    ) -> None:
        # The caller owns every resource.  The facade intentionally has no
        # lifecycle API and is discarded before these explicit closes.
        self.cache.close()
        self.assisted_routes.close()
        self.quizzes.close()
        self.profiles.close()
        self.profiles = ProfileStore(self.database_path)
        self.quizzes = self._open_quiz_store()
        self.assisted_routes = self._open_assisted_route_store()
        self.cache = self._open_cache()
        self._install_recordings(recordings)

    def _identity(
        self,
        namespace: str,
    ) -> tuple[ProfileCreate, object, SessionCreate, object]:
        profile_request = ProfileCreate(
            schemaVersion="wayline.v1",
            requestId=f"create-profile-{namespace}",
        )
        profile = self.application.create_profile(profile_request)
        session_request = SessionCreate(
            schemaVersion="wayline.v1",
            requestId=f"create-session-{namespace}",
            profileId=profile.profile_id,
            clientBuild="mac-demo-0.1.0",
        )
        session = self.application.create_session(session_request)
        return profile_request, profile, session_request, session

    @staticmethod
    def _battle_request(namespace: str, session_id: str) -> BattleQuizRequest:
        return BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId=f"prepare-{namespace}",
            sessionId=session_id,
            battleId="valuehold_route_1",
            worldId="valuehold",
            battleTier="route_1",
        )

    @staticmethod
    def _batch_seed(request: BattleQuizRequest, profile_id: str) -> int:
        catalog = CampaignCatalog.packaged_v1()
        curriculum = Curriculum.packaged_v1()
        identity = {
            "campaignCatalogId": catalog.catalog_id,
            "campaignCatalogReceipt": catalog.curriculum_receipt,
            "curriculumId": curriculum.curriculum_id,
            "curriculumSha256": CURRICULUM_V1_SHA256,
            "profileId": profile_id,
            "request": request.model_dump(by_alias=True, mode="json"),
        }
        canonical = json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return int.from_bytes(
            hashlib.sha256(canonical).digest()[:8],
            "big",
        ) & (2**63 - 1)

    def _freeze_recordings(
        self,
        profile_id: str,
        request: BattleQuizRequest,
    ) -> _FrozenFixtures:
        state = self.profiles.load_state(profile_id)
        intents = plan_slots(state, request.battle_tier)
        slots = materialize_slots(
            intents,
            request.battle_tier,
            self._batch_seed(request, profile_id),
            self.verifier.compiler,
        )
        recordings: dict[str, RawSlmGeneration] = {}
        requests: list[SlmRequest] = []
        correct_by_prompt: dict[str, str] = {}
        wrong_by_prompt: dict[str, tuple[str, ...]] = {}
        builder = BatchMaterialBuilder(
            batch_id="batch-fixture-preflight",
            context=BatchContext(
                profile_id=profile_id,
                session_id=request.session_id,
                world_id=request.world_id,
                battle_id=request.battle_id,
                core_subskill_ids=state.world(request.world_id).core_subskill_ids,
                content_version_id=Curriculum.packaged_v1().curriculum_id,
                battle_tier=request.battle_tier.value,
            ),
            planned_slots=slots,
        )
        for slot in slots:
            blueprint = slot.blueprint
            slm_request = build_slm_request(blueprint)
            selected = list(slot.required_procedure_ids)
            selected.extend(
                procedure_id
                for procedure_id in blueprint.allowed_procedure_ids
                if procedure_id not in selected
            )
            selected = selected[:3]
            if len(selected) != 3:
                raise AssertionError("frozen blueprint has fewer than three routes")
            distractors = tuple(
                {
                    "misconception": self.verifier.registry.canonical_label(
                        procedure_id
                    ),
                    "computation": self.verifier.registry.canonical_computation(
                        procedure_id,
                        blueprint,
                    ),
                    "answer": self.verifier.registry.evaluate(
                        procedure_id,
                        blueprint,
                    ).display,
                }
                for procedure_id in selected
            )
            generation = RawSlmGeneration(
                text=_canonical_json({"distractors": distractors}),
                model_sha256=self.verifier.manifest.model_sha256,
                prompt_sha256=slm_request.prompt_sha256,
                generated_at_utc="2026-07-12T12:00:00Z",
                adapter_identity_receipt_sha256=(
                    self.verifier.manifest.adapter_identity_receipt_sha256
                ),
                gguf_sha256=self.verifier.manifest.gguf_sha256,
                generator_identity_receipt_sha256=(
                    self.verifier.manifest.generator_identity_receipt_sha256
                ),
                registry_id=self.verifier.manifest.registry_id,
                prompt_template_sha256=(
                    self.verifier.manifest.prompt_template_sha256
                ),
            )
            self.assertEqual(generation.prompt_sha256, slm_request.prompt_sha256)
            verification = self.verifier.verify_generation(
                blueprint,
                generation,
            )
            self.assertTrue(
                verification.accepted,
                f"frozen generation was rejected: {verification.code}",
            )
            if verification.value is None:
                raise AssertionError("accepted frozen generation lacks a value")
            bundle = VerifiedQuestionBundle.from_verified(
                compiler=self.verifier.compiler,
                request=slot.request,
                blueprint=blueprint,
                verified=verification.value,
                generation=generation,
                manifest=self.verifier.manifest,
            )
            builder.accept_live(
                bundle,
                item_instance_id=(
                    "item_"
                    + hashlib.sha256(
                        f"{request.request_id}:{slot.slot_index}".encode("utf-8")
                    ).hexdigest()[:32]
                ),
            )
            recordings[slm_request.question_id] = generation
            requests.append(slm_request)
            correct_by_prompt[blueprint.prompt] = blueprint.canonical_answer.display
            wrong_by_prompt[blueprint.prompt] = tuple(
                distractor["answer"] for distractor in distractors
            )
        self.assertEqual(len(recordings), len(slots))
        return _FrozenFixtures(
            recordings=recordings,
            requests=tuple(requests),
            correct_by_prompt=correct_by_prompt,
            wrong_by_prompt=wrong_by_prompt,
        )

    @staticmethod
    def _option_id(item: object, display_text: str) -> str:
        return next(
            option.option_id
            for option in item.options
            if option.display_text == display_text
        )

    def _submissions(
        self,
        namespace: str,
        public_batch: object,
        fixtures: _FrozenFixtures,
    ) -> tuple[InitialSubmission, RevisionSubmission]:
        initial_selections = []
        revised_selections = []
        for index, item in enumerate(public_batch.items):
            correct = fixtures.correct_by_prompt[item.prompt]
            wrong = fixtures.wrong_by_prompt[item.prompt][0]
            initial_display = wrong if index == 0 else correct
            initial_selections.append(
                AnswerSelection(
                    itemId=item.item_id,
                    optionId=self._option_id(item, initial_display),
                    confidence="leaning",
                )
            )
            revised_selections.append(
                AnswerSelection(
                    itemId=item.item_id,
                    optionId=self._option_id(item, correct),
                    confidence="certain",
                )
            )
        return (
            InitialSubmission(
                schemaVersion="wayline.v1",
                requestId=f"initial-{namespace}",
                batchId=public_batch.batch_id,
                itemCount=public_batch.item_count,
                selections=tuple(initial_selections),
            ),
            RevisionSubmission(
                schemaVersion="wayline.v1",
                requestId=f"revision-{namespace}",
                batchId=public_batch.batch_id,
                itemCount=public_batch.item_count,
                selections=tuple(revised_selections),
            ),
        )

    @staticmethod
    def _change_first_confidence(submission: object, confidence: str) -> object:
        selections = list(submission.selections)
        first = selections[0]
        selections[0] = AnswerSelection(
            itemId=first.item_id,
            optionId=first.option_id,
            confidence=confidence,
        )
        return type(submission)(
            schemaVersion=submission.schema_version,
            requestId=submission.request_id,
            batchId=submission.batch_id,
            itemCount=submission.item_count,
            selections=tuple(selections),
        )

    def _assert_public_batch_allowlist(self, public_batch: object) -> None:
        payload = json.loads(public_batch.model_dump_json(by_alias=True))
        self.assertEqual(
            set(payload),
            {"schemaVersion", "batchId", "itemCount", "items"},
        )
        for item in payload["items"]:
            self.assertEqual(set(item), {"itemId", "prompt", "options"})
            for option in item["options"]:
                self.assertEqual(set(option), {"optionId", "displayText"})

    def _assert_initial_allowlist(self, initial_result: object) -> None:
        payload = json.loads(initial_result.model_dump_json(by_alias=True))
        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "batchId",
                "itemCount",
                "wrongCount",
                "revisionRequired",
                "finalResult",
            },
        )
        self.assertIsNone(payload["finalResult"])

    def _assert_only_learning_events(
        self,
        events: tuple[object, ...],
        item_count: int,
    ) -> None:
        self.assertEqual(len(events), item_count + 1)
        self.assertIs(type(events[0]), WorldActivatedEvent)
        self.assertTrue(
            all(type(event) is ObservationEvent for event in events[1:])
        )
        self.assertEqual(
            tuple(event.event_type for event in events),
            ("world_activated",) + ("observation",) * item_count,
        )
        ordinals = tuple(event.ordinal for event in events)
        self.assertEqual(ordinals, tuple(range(1, item_count + 2)))
        self.assertEqual(len(set(ordinals)), len(ordinals))

    async def test_recorded_valuehold_loop_replays_without_new_side_effects(
        self,
    ) -> None:
        (
            profile_request,
            profile,
            session_request,
            session,
        ) = self._identity("valuehold-main-001")
        self.assertEqual(self.application.create_profile(profile_request), profile)
        self.assertEqual(self.application.create_session(session_request), session)
        self.assertEqual(session.active_world_id, "valuehold")

        conflicting_session = SessionCreate(
            schemaVersion="wayline.v1",
            requestId=session_request.request_id,
            profileId=profile.profile_id,
            clientBuild="mac-demo-changed",
        )
        with self.assertRaises(IdentityLifecycleError) as session_error:
            self.application.create_session(conflicting_session)
        self.assertEqual(session_error.exception.code, "idempotency_conflict")

        battle_request = self._battle_request(
            "valuehold-main-001",
            session.session_id,
        )
        fixtures = self._freeze_recordings(profile.profile_id, battle_request)
        self._install_recordings(fixtures.recordings)
        public_batch = await self.application.prepare_battle(
            battle_request,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        provider_calls = self.provider.calls
        self.assertEqual(provider_calls, public_batch.item_count)
        self.assertEqual(
            await self.application.prepare_battle(
                battle_request,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            ),
            public_batch,
        )
        self.assertEqual(self.provider.calls, provider_calls)
        self.assertEqual(
            {
                request.question_id: request.prompt_sha256
                for request in self.provider.requests
            },
            {
                question_id: generation.prompt_sha256
                for question_id, generation in fixtures.recordings.items()
            },
        )
        self._assert_public_batch_allowlist(public_batch)

        conflicting_battle = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId=battle_request.request_id,
            sessionId=session.session_id,
            battleId="valuehold_route_2",
            worldId="valuehold",
            battleTier="route_1",
        )
        with self.assertRaises(BattlePreparationError) as battle_error:
            await self.application.prepare_battle(
                conflicting_battle,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )
        self.assertEqual(battle_error.exception.code, "idempotency_conflict")

        initial, revision = self._submissions(
            "valuehold-main-001",
            public_batch,
            fixtures,
        )
        initial_result = self.application.submit_initial(
            initial,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        self.assertEqual(initial_result.wrong_count, 1)
        self.assertTrue(initial_result.revision_required)
        self.assertEqual(
            self.application.submit_initial(
                initial,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            ),
            initial_result,
        )
        self._assert_initial_allowlist(initial_result)
        self.assertEqual(self.quizzes.pending_observations(profile.profile_id), ())
        pre_reveal_events = self.profiles.load_events(profile.profile_id)
        self.assertEqual(len(pre_reveal_events), 1)
        self.assertIs(type(pre_reveal_events[0]), WorldActivatedEvent)
        self.assertFalse(
            any(isinstance(event, ObservationEvent) for event in pre_reveal_events)
        )

        changed_initial = self._change_first_confidence(initial, "guessing")
        with self.assertRaises(QuizSubmissionError) as initial_conflict:
            self.application.submit_initial(
                changed_initial,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )
        self.assertEqual(initial_conflict.exception.code, "idempotency_conflict")

        final_result = self.application.submit_revision(
            revision,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        self.assertEqual(final_result.first_pass_wrong_count, 1)
        self.assertEqual(final_result.final_correct_count, public_batch.item_count)
        self.assertTrue(final_result.revision_used)
        utc_calls = self.utc.calls
        self.assertEqual(utc_calls, 1)
        self.assertEqual(
            self.application.submit_revision(
                revision,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            ),
            final_result,
        )
        self.assertEqual(self.utc.calls, utc_calls)

        changed_revision = self._change_first_confidence(revision, "leaning")
        with self.assertRaises(QuizSubmissionError) as revision_conflict:
            self.application.submit_revision(
                changed_revision,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )
        self.assertEqual(revision_conflict.exception.code, "idempotency_conflict")

        second_revision = RevisionSubmission(
            schemaVersion="wayline.v1",
            requestId="revision-valuehold-main-002",
            batchId=revision.batch_id,
            itemCount=revision.item_count,
            selections=revision.selections,
        )
        with self.assertRaises(QuizSubmissionError) as second_revision_error:
            self.application.submit_revision(
                second_revision,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )
        self.assertEqual(
            second_revision_error.exception.code,
            "quiz_state_conflict",
        )

        self.assertEqual(self.quizzes.pending_observations(profile.profile_id), ())
        events_before_restart = self.profiles.load_events(profile.profile_id)
        self._assert_only_learning_events(
            events_before_restart,
            public_batch.item_count,
        )

        self._restart(fixtures.recordings)
        self.assertEqual(self.application.create_profile(profile_request), profile)
        self.assertEqual(self.application.create_session(session_request), session)
        self.assertEqual(
            await self.application.prepare_battle(
                battle_request,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            ),
            public_batch,
        )
        self.assertEqual(
            self.application.submit_initial(
                initial,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            ),
            initial_result,
        )
        self.assertEqual(
            self.application.submit_revision(
                revision,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            ),
            final_result,
        )
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(self.monotonic.calls, 0)
        self.assertEqual(self.monotonic.sleep_calls, 0)
        self.assertEqual(self.utc.calls, 0)
        self.assertEqual(self.ids.batch_count, 0)
        self.assertEqual(self.ids.item_count, 0)
        self.assertEqual(
            self.profiles.load_events(profile.profile_id),
            events_before_restart,
        )

        runtime = self.application.get_runtime_state(
            profile.profile_id,
            session.session_id,
        )
        self.assertEqual(runtime.profile_id, profile.profile_id)
        self.assertEqual(runtime.session_id, session.session_id)
        self.assertEqual(runtime.active_world_id, "valuehold")
        gate = self.application.get_boss_gate(
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
            world_id="valuehold",
        )
        self.assertFalse(gate.unlocked)
        self.assertEqual(gate.lead_in_wins, 0)
        self.assertEqual(
            self.profiles.load_events(profile.profile_id),
            events_before_restart,
        )

        public_names = {
            name for name in dir(self.application) if not name.startswith("_")
        }
        self.assertEqual(
            public_names,
            {
                "activate_world",
                "complete_assisted_route",
                "complete_battle",
                "complete_revived_combat",
                "complete_seal_trial",
                "complete_second_wind",
                "create_profile",
                "create_session",
                "delete_profile",
                "export_profile",
                "get_boss_gate",
                "get_quiz_snapshot",
                "get_runtime_state",
                "prepare_assisted_route",
                "prepare_battle",
                "prepare_seal_trial",
                "start_second_wind",
                "submit_initial",
                "submit_revision",
            },
        )

    async def test_facade_exposes_reload_snapshot_and_authenticated_deletion(
        self,
    ) -> None:
        _profile_request, profile, _session_request, session = self._identity(
            "valuehold-facade-lifecycle-001"
        )
        battle_request = self._battle_request(
            "valuehold-facade-lifecycle-001",
            session.session_id,
        )
        fixtures = self._freeze_recordings(profile.profile_id, battle_request)
        self._install_recordings(fixtures.recordings)
        public_batch = await self.application.prepare_battle(
            battle_request,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )

        snapshot = self.application.get_quiz_snapshot(
            public_batch.batch_id,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        self.assertEqual(snapshot.quiz_state.value, "ready")
        self.assertEqual(snapshot.public_batch, public_batch)
        self.assertIsNone(snapshot.initial_submission)
        self.assertIsNone(snapshot.final_result)

        exported = self.application.export_profile(
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        self.assertEqual(exported.profile_id, profile.profile_id)
        self.assertEqual(
            tuple(item.session_id for item in exported.sessions),
            (session.session_id,),
        )

        self.assertIsNone(
            self.application.delete_profile(
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )
        )
        with self.assertRaises(ProfileNotFoundError):
            self.profiles.load_profile(profile.profile_id)

    async def test_completed_battle_closes_quiz_and_clears_resumable_block(self) -> None:
        _profile_request, profile, _session_request, session = self._identity(
            "valuehold-consumed-quiz-001"
        )
        route_one = self._battle_request(
            "valuehold-consumed-quiz-001",
            session.session_id,
        )
        route_one_fixtures = self._freeze_recordings(
            profile.profile_id,
            route_one,
        )
        self._install_recordings(route_one_fixtures.recordings)
        route_one_batch = await self.application.prepare_battle(
            route_one,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        all_correct = InitialSubmission(
            schemaVersion="wayline.v1",
            requestId="initial-valuehold-consumed-quiz-001",
            batchId=route_one_batch.batch_id,
            itemCount=route_one_batch.item_count,
            selections=tuple(
                AnswerSelection(
                    itemId=item.item_id,
                    optionId=self._option_id(
                        item,
                        route_one_fixtures.correct_by_prompt[item.prompt],
                    ),
                    confidence="certain",
                )
                for item in route_one_batch.items
            ),
        )
        initial_result = self.application.submit_initial(
            all_correct,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        self.assertIsNotNone(initial_result.final_result)

        completion = self.application.complete_battle(
            BattleCompletionRequest(
                request_id="complete-valuehold-route-1",
                profile_id=profile.profile_id,
                session_id=session.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                batch_id=route_one_batch.batch_id,
                combat_won=True,
            )
        )

        self.assertEqual(completion.battle_id, "valuehold_route_1")
        self.assertIs(
            self.quizzes.load(
                route_one_batch.batch_id,
                profile_id=profile.profile_id,
            ).state,
            QuizState.CLOSED,
        )
        self.assertIsNone(self.quizzes.resumable_batch_id(profile.profile_id))

        route_two = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId="prepare-valuehold-consumed-quiz-route-2",
            sessionId=session.session_id,
            battleId="valuehold_route_2",
            worldId="valuehold",
            battleTier="route_2",
        )
        route_two_fixtures = self._freeze_recordings(
            profile.profile_id,
            route_two,
        )
        prior_batch_count = self.ids.batch_count
        prior_item_count = self.ids.item_count
        self._install_recordings(route_two_fixtures.recordings)
        self.ids.batch_count = prior_batch_count
        self.ids.item_count = prior_item_count
        route_two_batch = await self.application.prepare_battle(
            route_two,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )

        self.assertEqual(route_two_batch.item_count, 4)

    async def test_pending_reveal_outbox_recovers_exactly_once_through_gate(
        self,
    ) -> None:
        _profile_request, profile, _session_request, session = self._identity(
            "valuehold-outbox-001"
        )
        battle_request = self._battle_request(
            "valuehold-outbox-001",
            session.session_id,
        )
        fixtures = self._freeze_recordings(profile.profile_id, battle_request)
        self._install_recordings(fixtures.recordings)
        public_batch = await self.application.prepare_battle(
            battle_request,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        initial, revision = self._submissions(
            "valuehold-outbox-001",
            public_batch,
            fixtures,
        )
        self.application.submit_initial(
            initial,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        self.quizzes._failpoint_stage = "after_reveal_commit"
        with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
            self.application.submit_revision(
                revision,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )

        pending = self.quizzes.pending_observations(profile.profile_id)
        self.assertEqual(len(pending), public_batch.item_count)
        self.assertEqual(
            tuple(event.ordinal for event in pending),
            tuple(range(2, public_batch.item_count + 2)),
        )
        self.assertEqual(len(self.profiles.load_events(profile.profile_id)), 1)

        self._restart(fixtures.recordings)
        durable_pending = self.quizzes.pending_observations(profile.profile_id)
        self.assertEqual(durable_pending, pending)
        self.quizzes._failpoint_stage = (
            f"after_profile_append:{durable_pending[0].item_id}"
        )
        with self.assertRaises(BossGateQueryError) as interrupted_gate:
            self.application.get_boss_gate(
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
                world_id="valuehold",
            )
        self.assertEqual(
            interrupted_gate.exception.code,
            "evidence_sync_unavailable",
        )
        self.assertEqual(
            self.quizzes.pending_observations(profile.profile_id),
            durable_pending,
        )
        interrupted_events = self.profiles.load_events(profile.profile_id)
        self.assertEqual(len(interrupted_events), 2)
        self.assertIs(type(interrupted_events[-1]), ObservationEvent)

        self._restart(fixtures.recordings)
        gate = self.application.get_boss_gate(
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
            world_id="valuehold",
        )
        self.assertFalse(gate.unlocked)
        self.assertEqual(gate.lead_in_wins, 0)
        self.assertEqual(self.quizzes.pending_observations(profile.profile_id), ())
        recovered_events = self.profiles.load_events(profile.profile_id)
        self._assert_only_learning_events(
            recovered_events,
            public_batch.item_count,
        )
        observations = recovered_events[1:]
        self.assertEqual(
            len({event.item_id for event in observations}),
            public_batch.item_count,
        )

        replayed_final = self.application.submit_revision(
            revision,
            profile_id=profile.profile_id,
            current_session_id=session.session_id,
        )
        self.assertEqual(replayed_final.first_pass_wrong_count, 1)
        self.assertEqual(replayed_final.final_correct_count, public_batch.item_count)
        self.assertEqual(
            self.application.get_boss_gate(
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
                world_id="valuehold",
            ),
            gate,
        )
        self.assertEqual(
            self.profiles.load_events(profile.profile_id),
            recovered_events,
        )
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(self.utc.calls, 0)

    async def test_recorded_provider_missing_and_mismatch_fail_closed(self) -> None:
        _profile_request, profile, _session_request, session = self._identity(
            "valuehold-provider-001"
        )
        battle_request = self._battle_request(
            "valuehold-provider-001",
            session.session_id,
        )
        fixtures = self._freeze_recordings(profile.profile_id, battle_request)
        slm_request = fixtures.requests[0]
        generation = fixtures.recordings[slm_request.question_id]

        exact_provider = RecordedDistractorProvider(fixtures.recordings)
        self.assertEqual(await exact_provider.generate(slm_request), generation)

        with self.assertRaises(ProviderError) as missing:
            await RecordedDistractorProvider({}).generate(slm_request)
        self.assertEqual(missing.exception.code, "recording_not_found")

        mismatched_generation = replace(
            generation,
            prompt_sha256="0" * 64,
        )
        with self.assertRaises(ProviderError) as mismatched:
            await RecordedDistractorProvider(
                {slm_request.question_id: mismatched_generation}
            ).generate(slm_request)
        self.assertEqual(
            mismatched.exception.code,
            "recording_receipt_mismatch",
        )

        self._install_recordings({})
        with self.assertRaises(BattlePreparationError) as missing_application:
            await self.application.prepare_battle(
                battle_request,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )
        self.assertEqual(
            missing_application.exception.code,
            "safe_content_unavailable",
        )

        mismatched_recordings = {
            question_id: replace(raw, prompt_sha256="0" * 64)
            for question_id, raw in fixtures.recordings.items()
        }
        self._install_recordings(mismatched_recordings)
        with self.assertRaises(BattlePreparationError) as mismatch_application:
            await self.application.prepare_battle(
                battle_request,
                profile_id=profile.profile_id,
                current_session_id=session.session_id,
            )
        self.assertEqual(
            mismatch_application.exception.code,
            "safe_content_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
