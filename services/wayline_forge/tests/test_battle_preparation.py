from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from services.wayline_forge.app.contracts import (
    AssistedRouteBatch,
    BattleQuizRequest,
    PublicOption,
    PublicQuizBatch,
    PublicQuizItem,
)
from services.wayline_forge.app.assisted_route_store import AssistedRouteStore
from services.wayline_forge.app.events import (
    EVENT_SCHEMA_VERSION,
    OUTCOME_EVENT_SCHEMA_VERSION,
    BattleOutcomeEvent,
    BossCompletionEvent,
    BossOutcomeEvent,
    ObservationEvent,
    SealTrialCompletionEvent,
    SecondWindStartedEvent,
    WorldActivatedEvent,
)
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
    QuizStoreError,
    QuizTransitionConflictError,
)
from services.wayline_forge.tests.fixtures import event
from services.wayline_forge.tests.test_assisted_route_machine import (
    AssistedRouteMachineTests,
)


def _public_batch(
    batch_id: str = "batch-valuehold-route-001",
    *,
    item_count: int = 3,
) -> PublicQuizBatch:
    items = tuple(
        PublicQuizItem(
            itemId=f"item-valuehold-{index:03d}",
            prompt=f"Trusted prompt {index}",
            options=tuple(
                PublicOption(
                    optionId=f"option-{index:03d}-{option}",
                    displayText=str(option),
                )
                for option in range(4)
            ),
        )
        for index in range(1, item_count + 1)
    )
    return PublicQuizBatch(
        schemaVersion="wayline.v1",
        batchId=batch_id,
        itemCount=len(items),
        items=items,
    )


@dataclass(frozen=True)
class _Prepared:
    public_output: PublicQuizBatch


class _RecordingOrchestrator:
    def __init__(
        self,
        output: PublicQuizBatch | None = None,
        *,
        material: object | None = None,
    ) -> None:
        self.output = output or _public_batch()
        self.material = material
        self.calls: list[dict[str, object]] = []
        self.material_calls: list[dict[str, object]] = []

    async def prepare(self, request: BattleQuizRequest, **kwargs: object) -> _Prepared:
        self.calls.append({"request": request, **kwargs})
        return _Prepared(self.output)

    async def build_verified_material(self, **kwargs: object) -> object:
        self.material_calls.append(kwargs)
        if self.material is None:
            raise AssertionError("no assisted material fixture was configured")
        return self.material


class _NeverOrchestrator:
    async def prepare(self, request: BattleQuizRequest, **kwargs: object) -> object:
        raise AssertionError("exact replay must not invoke the orchestrator")


class _ReplayOnlyQuizStore:
    def __init__(self, store: QuizStore) -> None:
        self.store = store

    def load_preparation(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
    ) -> object:
        return self.store.load_preparation(request, profile_id=profile_id)

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"exact replay touched forbidden dependency: {name}")


class _DrainFailureQuizStore:
    def load_preparation(self, request: BattleQuizRequest, *, profile_id: str) -> None:
        return None

    def drain_observations(self, profile_id: str, *, profile_store: object) -> int:
        raise QuizStoreError("sensitive evidence transport details")


class _StageFailureQuizStore:
    def __init__(self, stage: str, error: BaseException) -> None:
        self.stage = stage
        self.error = error

    def _fail(self, stage: str) -> None:
        if self.stage == stage:
            raise self.error

    def load_preparation(self, request: BattleQuizRequest, *, profile_id: str) -> None:
        self._fail("load")
        return None

    def drain_observations(self, profile_id: str, *, profile_store: object) -> int:
        self._fail("drain")
        return 0

    def resumable_batch_id(self, profile_id: str) -> str | None:
        self._fail("resumable")
        return None


class _FailingOrchestrator:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    async def prepare(self, request: BattleQuizRequest, **kwargs: object) -> object:
        self.calls += 1
        raise self.error


class _ProfileProxy:
    def __init__(
        self,
        store: ProfileStore,
        *,
        load_profile_error: BaseException | None = None,
        load_state_error: BaseException | None = None,
    ) -> None:
        self.store = store
        self.load_profile_error = load_profile_error
        self.load_state_error = load_state_error

    def load_profile(self, profile_id: str) -> object:
        if self.load_profile_error is not None:
            raise self.load_profile_error
        return self.store.load_profile(profile_id)

    def load_state(self, profile_id: str) -> object:
        if self.load_state_error is not None:
            raise self.load_state_error
        return self.store.load_state(profile_id)

    def __getattr__(self, name: str) -> object:
        return getattr(self.store, name)


class BattlePreparationModuleTests(unittest.TestCase):
    def test_dependency_free_service_api_exists_and_accepts_no_authority_injection(self) -> None:
        spec = importlib.util.find_spec(
            "services.wayline_forge.app.battle_preparation"
        )
        self.assertIsNotNone(spec, "battle-preparation application service is missing")

        from services.wayline_forge.app import battle_preparation

        self.assertTrue(hasattr(battle_preparation, "BattlePreparationError"))
        self.assertTrue(hasattr(battle_preparation, "BattlePreparationService"))
        signature = inspect.signature(battle_preparation.BattlePreparationService)
        self.assertNotIn("catalog", signature.parameters)
        self.assertNotIn("curriculum", signature.parameters)


class BattlePreparationServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from services.wayline_forge.tests.test_batch_material import (
            BatchMaterialTests,
        )

        BatchMaterialTests.setUpClass()
        cls.material_fixture_type = BatchMaterialTests
        cls.verifier = BatchMaterialTests.verifier

    def setUp(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationService,
        )

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "wayline.sqlite3"
        self.profiles = ProfileStore(self.database_path)
        self.profile = self.profiles.create_profile(request_id="profile-request-001")
        self.session = self.profiles.create_session(
            request_id="session-request-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        self.quizzes = QuizStore(
            self.database_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.orchestrator = _RecordingOrchestrator()
        self.service = BattlePreparationService(
            self.profiles,
            self.quizzes,
            self.orchestrator,
        )

    def tearDown(self) -> None:
        self.quizzes.close()
        self.profiles.close()
        self.temporary_directory.cleanup()

    def request(self, **changes: object) -> BattleQuizRequest:
        payload: dict[str, object] = {
            "schemaVersion": "wayline.v1",
            "requestId": "prepare-request-001",
            "sessionId": self.session.session_id,
            "battleId": "valuehold_route_1",
            "worldId": "valuehold",
            "battleTier": "route_1",
        }
        payload.update(changes)
        return BattleQuizRequest.model_validate(payload)

    def test_fresh_assisted_preparation_persists_only_the_route_store(self) -> None:
        self.profiles.append(
            BossCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="boss-assisted-preparation-001",
                idempotency_id="boss-assisted-preparation-request-001",
                ordinal=2,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_boss",
                occurred_at="2026-07-12T17:00:00Z",
                combat_won=True,
                final_correct=5,
                item_count=8,
                is_campaign_finale=False,
                batch_id="batch-boss-assisted-preparation",
            )
        )
        for attempt, ordinal in ((1, 3), (2, 4)):
            self.profiles.append(
                SealTrialCompletionEvent(
                    schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                    event_id=f"seal-assisted-preparation-{attempt}",
                    idempotency_id=(
                        f"seal-assisted-preparation-request-{attempt}"
                    ),
                    ordinal=ordinal,
                    profile_id=self.profile.profile_id,
                    session_id=self.session.session_id,
                    world_id="valuehold",
                    battle_id=f"valuehold_seal_trial_{attempt}",
                    occurred_at=f"2026-07-12T17:0{attempt}:00Z",
                    attempt_number=attempt,
                    passed=False,
                    final_correct=1,
                    item_count=3,
                    batch_id=f"batch-seal-assisted-preparation-{attempt}",
                    gate_recheck_sha256="a" * 64,
                )
            )
        fixture = AssistedRouteMachineTests()
        fixture.verifier = self.verifier
        material = fixture._material(
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
        )
        orchestrator = _RecordingOrchestrator(material=material)
        route_store = AssistedRouteStore(
            self.database_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationService,
        )
        service = BattlePreparationService(
            self.profiles,
            self.quizzes,
            orchestrator,
            assisted_route_store=route_store,
        )
        try:
            batch = asyncio.run(service.prepare_assisted_route(
                request_id="prepare-assisted-fresh-001",
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
                world_id="valuehold",
            ))
            replay = asyncio.run(service.prepare_assisted_route(
                request_id="prepare-assisted-fresh-001",
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
                world_id="valuehold",
            ))
        finally:
            route_store.close()

        self.assertIsInstance(batch, AssistedRouteBatch)
        self.assertEqual(replay, batch)
        self.assertEqual(len(orchestrator.material_calls), 1)
        self.assertIsNone(
            self.quizzes.resumable_batch_id(self.profile.profile_id)
        )

    def prepare(self, request: BattleQuizRequest | None = None) -> PublicQuizBatch:
        return asyncio.run(
            self.service.prepare(
                request or self.request(),
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        )

    def test_first_authored_route_uses_authoritative_state_content_and_seed(self) -> None:
        result = self.prepare()

        self.assertEqual(result, self.orchestrator.output)
        self.assertEqual(len(self.orchestrator.calls), 1)
        call = self.orchestrator.calls[0]
        self.assertEqual(call["profile_id"], self.profile.profile_id)
        self.assertEqual(call["learner_state"].active_world_id, "valuehold")
        self.assertEqual(call["content_version_id"], "wayline-launch-core-v1")
        seed = call["batch_seed"]
        self.assertIs(type(seed), int)
        self.assertGreaterEqual(seed, 0)
        self.assertLess(seed, 2**63)

    def _persist_exact_preparation(
        self,
        request: BattleQuizRequest | None = None,
    ) -> tuple[BattleQuizRequest, PublicQuizBatch]:
        actual_request = request or self.request()
        material = self._material_for_request(actual_request)
        stored = self.quizzes.create_prepared(
            material,
            request=actual_request,
            profile_id=self.profile.profile_id,
        )
        return actual_request, stored.public_output

    def _material_for_request(self, request: BattleQuizRequest):
        from services.wayline_forge.app.batch_material import VerifiedBatchMaterial

        fixture = self.material_fixture_type(methodName="runTest")
        fixture.setUp()
        fixture.context = replace(
            fixture.context,
            profile_id=self.profile.profile_id,
            session_id=self.session.session_id,
            world_id=request.world_id,
            battle_id=request.battle_id,
            content_version_id=self.verifier.compiler.curriculum.curriculum_id,
            battle_tier=request.battle_tier.value,
        )
        material = fixture.complete_material()
        self.assertIs(type(material), VerifiedBatchMaterial)
        return material

    def test_exact_request_replay_returns_before_sync_planning_or_runtime_dependencies(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationService,
        )

        request, expected = self._persist_exact_preparation()
        replay_service = BattlePreparationService(
            self.profiles,
            _ReplayOnlyQuizStore(self.quizzes),
            _NeverOrchestrator(),
        )

        actual = asyncio.run(
            replay_service.prepare(
                request,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        )

        self.assertEqual(actual, expected)
        public_json = json.dumps(actual.model_dump(by_alias=True, mode="json"))
        for private_name in (
            "correctAnswer",
            "correctOptionId",
            "misconception",
            "computation",
            "sealed",
            "rawSlm",
            "token",
            "credential",
        ):
            self.assertNotIn(private_name, public_json)

    def test_reused_request_id_with_changed_payload_is_a_stable_conflict(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        request, _expected = self._persist_exact_preparation()
        changed = request.model_copy(
            update={
                "battle_id": "valuehold_route_2",
                "battle_tier": request.battle_tier.ROUTE_2,
            }
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare(changed)

        self.assertEqual(raised.exception.code, "idempotency_conflict")

    def _append_win(self, battle_id: str) -> None:
        ordinal = len(self.profiles.load_events(self.profile.profile_id)) + 1
        self.profiles.append(
            BattleOutcomeEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id=f"battle-outcome-{ordinal:03d}-{battle_id}",
                idempotency_id=f"battle-request-{ordinal:03d}-{battle_id}",
                ordinal=ordinal,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id=battle_id,
                won=True,
                is_lead_in=True,
                occurred_at=f"2026-07-11T21:{ordinal:02d}:00Z",
            )
        )

    def _append_activation(
        self,
        world_index: int,
        *,
        battle_id: str = "campaign-map",
    ) -> None:
        from services.wayline_forge.app.campaign_catalog import CampaignCatalog

        world = CampaignCatalog.packaged_v1().worlds[world_index]
        ordinal = len(self.profiles.load_events(self.profile.profile_id)) + 1
        self.profiles.append(
            WorldActivatedEvent(
                schema_version="wayline.event.v1",
                event_id=f"world-activation-hardening-{ordinal:03d}",
                idempotency_id=f"world-activation-hardening-request-{ordinal:03d}",
                ordinal=ordinal,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id=world.world_id,
                battle_id=battle_id,
                occurred_at=f"2026-07-12T00:{ordinal:02d}:00Z",
                core_subskill_ids=world.core_subskill_ids,
                curriculum_receipt=CampaignCatalog.packaged_v1().curriculum_receipt,
            )
        )

    def _append_content_observation(
        self,
        world_id: str,
        *,
        is_transfer: bool,
        question_id: str,
    ) -> ObservationEvent:
        ordinal = len(self.profiles.load_events(self.profile.profile_id)) + 1
        observation = event.correct(
            ordinal=ordinal,
            profile=self.profile.profile_id,
            session=self.session.session_id,
            world=world_id,
            battle=(
                "decimara_route_1"
                if self.profiles.load_state(self.profile.profile_id).active_world_id
                == "decimara"
                else "valuehold_route_1"
            ),
            batch=f"batch-catalog-hardening-{ordinal:03d}",
            skill="place_value" if world_id == "valuehold" else "decimal_add_sub",
            question=question_id,
            template=f"template-catalog-hardening-{ordinal:03d}",
            transfer=is_transfer,
            core_subskills=("legacy-observation-roster",),
        )
        self.profiles.append(observation)
        return observation

    def _append_gate_evidence(self, latest_ten_correct: int = 7) -> None:
        first_six = (True, True, False, True, False, True)
        latest = (True,) * latest_ten_correct + (False,) * (10 - latest_ten_correct)
        for index, correct in enumerate((*first_six, *latest), start=1):
            ordinal = len(self.profiles.load_events(self.profile.profile_id)) + 1
            skill = ("place_value", "mental_add_sub")[(index - 1) % 2]
            common = dict(
                ordinal=ordinal,
                profile=self.profile.profile_id,
                session=self.session.session_id,
                world="valuehold",
                battle="valuehold_elite",
                batch=f"completed-batch-{(index - 1) // 4 + 1:03d}",
                skill=skill,
                question=f"gate-question-{index:03d}",
                template=f"gate-template-{index:03d}",
                core_subskills=("place_value", "mental_add_sub"),
            )
            observation = (
                event.correct(**common)
                if correct
                else event.wrong(f"gate-error-{skill}", **common)
            )
            self.profiles.append(observation)

    def test_lead_in_wins_must_be_the_exact_authored_prefix(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        self._append_win("valuehold_route_2")

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare(
                self.request(
                    battleId="valuehold_route_2",
                    battleTier="route_2",
                )
            )

        self.assertEqual(raised.exception.code, "catalog_conflict")
        self.assertEqual(self.orchestrator.calls, [])

    def test_only_the_next_authored_world_battle_and_tier_are_accepted(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        forged_requests = (
            self.request(battleId="valuehold_route_2", battleTier="route_2"),
            self.request(worldId="decimara", battleId="decimara_route_1"),
            self.request(battleTier="route_2"),
            self.request(battleId="valuehold_seal", battleTier="seal_trial"),
        )
        for forged in forged_requests:
            with self.subTest(forged=forged):
                with self.assertRaises(BattlePreparationError) as raised:
                    self.prepare(forged)
                self.assertEqual(raised.exception.code, "catalog_conflict")
        self.assertEqual(self.orchestrator.calls, [])

    def test_next_authored_route_advances_after_an_exact_prefix_win(self) -> None:
        self._append_win("valuehold_route_1")
        self.orchestrator.output = _public_batch(
            "batch-valuehold-route-2-001",
            item_count=4,
        )

        result = self.prepare(
            self.request(
                requestId="prepare-request-route-2",
                battleId="valuehold_route_2",
                battleTier="route_2",
            )
        )

        self.assertEqual(result, self.orchestrator.output)
        state = self.orchestrator.calls[0]["learner_state"]
        self.assertEqual(
            state.world("valuehold").lead_in_battle_wins,
            ("valuehold_route_1",),
        )

    def test_boss_is_locked_until_the_deterministic_gate_is_met(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        for battle_id in (
            "valuehold_route_1",
            "valuehold_route_2",
            "valuehold_route_3",
            "valuehold_elite",
        ):
            self._append_win(battle_id)

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare(
                self.request(
                    battleId="valuehold_boss",
                    battleTier="world_boss",
                )
            )

        self.assertEqual(raised.exception.code, "boss_gate_locked")
        self.assertEqual(self.orchestrator.calls, [])

    def test_unlocked_boss_uses_the_authored_eight_item_contract(self) -> None:
        for battle_id in (
            "valuehold_route_1",
            "valuehold_route_2",
            "valuehold_route_3",
            "valuehold_elite",
        ):
            self._append_win(battle_id)
        self._append_gate_evidence()
        self.orchestrator.output = _public_batch(
            "batch-valuehold-boss-001",
            item_count=8,
        )

        result = self.prepare(
            self.request(
                requestId="prepare-request-boss-001",
                battleId="valuehold_boss",
                battleTier="world_boss",
            )
        )

        self.assertEqual(result.item_count, 8)
        self.assertEqual(len(self.orchestrator.calls), 1)

    def test_boss_outcome_makes_normal_battle_preparation_non_replayable(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        ordinal = len(self.profiles.load_events(self.profile.profile_id)) + 1
        self.profiles.append(
            BossOutcomeEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="boss-outcome-valuehold-001",
                idempotency_id="boss-request-valuehold-001",
                ordinal=ordinal,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_boss",
                combat_won=True,
                final_correct=6,
                item_count=8,
                is_campaign_finale=False,
                occurred_at="2026-07-11T22:00:00Z",
            )
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare()

        self.assertEqual(raised.exception.code, "catalog_conflict")

    def test_dedicated_seal_trial_preparation_requires_a_missed_boss_threshold(self) -> None:
        self.profiles.append(
            BossCompletionEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="boss-completion-before-seal",
                idempotency_id="boss-completion-before-seal-request",
                ordinal=2,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_boss",
                occurred_at="2026-07-12T17:00:00Z",
                combat_won=True,
                final_correct=5,
                item_count=8,
                is_campaign_finale=False,
                batch_id="batch-boss-before-seal",
            )
        )
        request = self.request(
            requestId="prepare-seal-trial-001",
            battleId="valuehold_seal_trial_1",
            battleTier="seal_trial",
        )
        self.orchestrator.output = _public_batch("batch-seal-trial-001")

        result = asyncio.run(
            self.service.prepare_seal_trial(
                request,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        )

        self.assertEqual(result.item_count, 3)
        self.assertEqual(self.orchestrator.calls[-1]["request"], request)

    def test_dedicated_second_wind_preparation_requires_a_durable_start(self) -> None:
        self.profiles.append(
            SecondWindStartedEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="second-wind-started-preparation",
                idempotency_id="second-wind-started-preparation-request",
                ordinal=2,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="valuehold",
                battle_id="valuehold_route_1",
                occurred_at="2026-07-12T17:00:00Z",
                second_wind_id="second-wind-attempt-001",
                combat_attempt_id="attempt-001",
                preparation_request_id="prepare-second-wind-001",
                quiz_battle_id="valuehold_route_1_second_wind",
            )
        )
        request = self.request(
            requestId="prepare-second-wind-001",
            battleId="valuehold_route_1_second_wind",
            battleTier="seal_trial",
        )
        self.orchestrator.output = _public_batch("batch-second-wind-001")

        result = asyncio.run(
            self.service.prepare_second_wind(
                request,
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        )

        self.assertEqual(result.item_count, 3)
        self.assertEqual(self.orchestrator.calls[-1]["request"], request)

    def test_activation_battle_identity_is_pinned_to_campaign_map(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        self._append_activation(1, battle_id="forged-campaign-source")

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare(
                self.request(
                    requestId="prepare-forged-activation-001",
                    worldId="decimara",
                    battleId="decimara_route_1",
                )
            )

        self.assertEqual(raised.exception.code, "catalog_conflict")
        self.assertEqual(self.orchestrator.calls, [])

    def test_non_transfer_observation_cannot_use_a_future_world(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        self._append_content_observation(
            "decimara",
            is_transfer=False,
            question_id="future-non-transfer-question",
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare()

        self.assertEqual(raised.exception.code, "catalog_conflict")
        self.assertEqual(self.orchestrator.calls, [])

    def test_transfer_flag_cannot_authorize_a_never_activated_future_world(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        self._append_content_observation(
            "decimara",
            is_transfer=True,
            question_id="future-transfer-question",
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare()

        self.assertEqual(raised.exception.code, "catalog_conflict")
        self.assertEqual(self.orchestrator.calls, [])

    def test_prior_activated_world_observation_requires_transfer_semantics(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        self._append_activation(1)
        self._append_content_observation(
            "valuehold",
            is_transfer=False,
            question_id="prior-world-non-transfer-question",
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare(
                self.request(
                    requestId="prepare-prior-non-transfer-001",
                    worldId="decimara",
                    battleId="decimara_route_1",
                )
            )

        self.assertEqual(raised.exception.code, "catalog_conflict")
        self.assertEqual(self.orchestrator.calls, [])

    def test_prior_activated_world_transfer_is_allowed_without_legacy_roster_authority(self) -> None:
        self._append_activation(1)
        observation = self._append_content_observation(
            "valuehold",
            is_transfer=True,
            question_id="prior-world-transfer-question",
        )
        self.assertEqual(
            observation.world_core_subskill_ids,
            ("legacy-observation-roster",),
        )

        result = self.prepare(
            self.request(
                requestId="prepare-prior-transfer-001",
                worldId="decimara",
                battleId="decimara_route_1",
            )
        )

        self.assertEqual(result, self.orchestrator.output)
        self.assertEqual(len(self.orchestrator.calls), 1)

    def test_non_observation_progression_event_must_use_the_then_active_world(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        ordinal = len(self.profiles.load_events(self.profile.profile_id)) + 1
        self.profiles.append(
            BattleOutcomeEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id="battle-outcome-wrong-world-001",
                idempotency_id="battle-outcome-wrong-world-request-001",
                ordinal=ordinal,
                profile_id=self.profile.profile_id,
                session_id=self.session.session_id,
                world_id="decimara",
                battle_id="decimara_route_1",
                won=False,
                is_lead_in=False,
                occurred_at="2026-07-12T00:30:00Z",
            )
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare()

        self.assertEqual(raised.exception.code, "catalog_conflict")
        self.assertEqual(self.orchestrator.calls, [])

    def test_orchestrator_output_count_must_match_the_authored_battle(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        self.orchestrator.output = _public_batch(
            "batch-wrong-authored-count-001",
            item_count=4,
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare()

        self.assertEqual(raised.exception.code, "integrity_failure")

    def test_evidence_drain_failure_is_non_sensitive_and_stops_before_planning(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
            BattlePreparationService,
        )

        service = BattlePreparationService(
            self.profiles,
            _DrainFailureQuizStore(),
            self.orchestrator,
        )

        with self.assertRaises(BattlePreparationError) as raised:
            asyncio.run(
                service.prepare(
                    self.request(),
                    profile_id=self.profile.profile_id,
                    current_session_id=self.session.session_id,
                )
            )

        self.assertEqual(raised.exception.code, "evidence_sync_unavailable")
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn(self.profile.profile_id, repr(raised.exception))
        self.assertEqual(self.orchestrator.calls, [])

    def test_missing_stale_and_cross_profile_sessions_share_one_public_failure(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        missing_request = self.request(sessionId="session-missing-001")
        with self.assertRaises(BattlePreparationError) as missing:
            asyncio.run(
                self.service.prepare(
                    missing_request,
                    profile_id=self.profile.profile_id,
                    current_session_id="session-missing-001",
                )
            )

        other_profile = self.profiles.create_profile(
            request_id="profile-request-other-001"
        )
        other_session = self.profiles.create_session(
            request_id="session-request-other-001",
            profile_id=other_profile.profile_id,
            client_build="mac-demo-0.1.0",
        )
        cross_request = self.request(sessionId=other_session.session_id)
        with self.assertRaises(BattlePreparationError) as cross:
            asyncio.run(
                self.service.prepare(
                    cross_request,
                    profile_id=self.profile.profile_id,
                    current_session_id=other_session.session_id,
                )
            )

        replacement = self.profiles.create_session(
            request_id="session-request-replacement-001",
            profile_id=self.profile.profile_id,
            client_build="mac-demo-0.1.1",
        )
        self.assertNotEqual(replacement.session_id, self.session.session_id)
        with self.assertRaises(BattlePreparationError) as stale:
            self.prepare()

        self.assertEqual(
            (missing.exception.code, cross.exception.code, stale.exception.code),
            ("session_not_current",) * 3,
        )
        for failure in (missing.exception, cross.exception, stale.exception):
            self.assertEqual(str(failure), "session_not_current")
            self.assertNotIn(self.profile.profile_id, repr(failure))
        self.assertEqual(self.orchestrator.calls, [])

    def test_request_and_transport_session_must_match_exactly(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )

        with self.assertRaises(BattlePreparationError) as raised:
            asyncio.run(
                self.service.prepare(
                    self.request(),
                    profile_id=self.profile.profile_id,
                    current_session_id="session-transport-forgery-001",
                )
            )

        self.assertEqual(raised.exception.code, "session_not_current")
        self.assertEqual(self.orchestrator.calls, [])

    def test_identity_corruption_is_not_misreported_as_a_missing_session(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
            BattlePreparationService,
        )
        from services.wayline_forge.app.profile_store import (
            IdentityStoreCorruptionError,
        )

        service = BattlePreparationService(
            _ProfileProxy(
                self.profiles,
                load_profile_error=IdentityStoreCorruptionError(
                    "sensitive corrupt identity row"
                ),
            ),
            self.quizzes,
            self.orchestrator,
        )

        with self.assertRaises(BattlePreparationError) as raised:
            asyncio.run(
                service.prepare(
                    self.request(),
                    profile_id=self.profile.profile_id,
                    current_session_id=self.session.session_id,
                )
            )

        self.assertEqual(raised.exception.code, "integrity_failure")
        self.assertNotIn("sensitive", repr(raised.exception))

    def test_generic_profile_store_auth_failure_is_integrity_not_busy(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
            BattlePreparationService,
        )
        from services.wayline_forge.app.profile_store import ProfileStoreError

        service = BattlePreparationService(
            _ProfileProxy(
                self.profiles,
                load_profile_error=ProfileStoreError(
                    "arbitrary profile damage"
                ),
            ),
            self.quizzes,
            self.orchestrator,
        )

        with self.assertRaises(BattlePreparationError) as raised:
            asyncio.run(
                service.prepare(
                    self.request(),
                    profile_id=self.profile.profile_id,
                    current_session_id=self.session.session_id,
                )
            )

        self.assertEqual(raised.exception.code, "integrity_failure")

    def test_generic_profile_store_state_failure_is_integrity_not_busy(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
            BattlePreparationService,
        )
        from services.wayline_forge.app.profile_store import ProfileStoreError

        profile_proxy = _ProfileProxy(
            self.profiles,
            load_state_error=ProfileStoreError("arbitrary projection damage"),
        )
        service = BattlePreparationService(
            profile_proxy,
            _StageFailureQuizStore("none", RuntimeError("unused")),
            self.orchestrator,
        )

        with self.assertRaises(BattlePreparationError) as raised:
            asyncio.run(
                service.prepare(
                    self.request(),
                    profile_id=self.profile.profile_id,
                    current_session_id=self.session.session_id,
                )
            )

        self.assertEqual(raised.exception.code, "integrity_failure")

    def test_quiz_store_busy_and_corruption_are_mapped_at_every_read_stage(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
            BattlePreparationService,
        )

        cases = (
            ("load", QuizStoreBusyError("sensitive busy details"), "storage_busy"),
            ("load", QuizStoreCorruptionError("sensitive row"), "integrity_failure"),
            ("drain", QuizStoreBusyError("sensitive busy details"), "storage_busy"),
            ("drain", QuizStoreCorruptionError("sensitive row"), "integrity_failure"),
            ("resumable", QuizStoreBusyError("sensitive busy details"), "storage_busy"),
            (
                "resumable",
                QuizStoreCorruptionError("sensitive row"),
                "integrity_failure",
            ),
        )
        for stage, error, expected_code in cases:
            with self.subTest(stage=stage, error=type(error).__name__):
                service = BattlePreparationService(
                    self.profiles,
                    _StageFailureQuizStore(stage, error),
                    self.orchestrator,
                )
                with self.assertRaises(BattlePreparationError) as raised:
                    asyncio.run(
                        service.prepare(
                            self.request(),
                            profile_id=self.profile.profile_id,
                            current_session_id=self.session.session_id,
                        )
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("sensitive", repr(raised.exception))

    def test_orchestrator_failures_use_only_the_stable_public_taxonomy(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
            BattlePreparationService,
        )
        from services.wayline_forge.app.orchestrator import BatchPreparationError
        from services.wayline_forge.app.quiz_machine import IdempotencyConflictError

        cases = (
            (
                BatchPreparationError("fallback_unavailable"),
                "safe_content_unavailable",
            ),
            (QuizStoreBusyError("sensitive busy details"), "storage_busy"),
            (QuizStoreCorruptionError("sensitive row"), "integrity_failure"),
            (
                QuizTransitionConflictError("sensitive live race"),
                "quiz_in_progress",
            ),
            (
                IdempotencyConflictError("sensitive request material"),
                "idempotency_conflict",
            ),
            (RuntimeError("sensitive programmer detail"), "integrity_failure"),
        )
        for error, expected_code in cases:
            with self.subTest(error=type(error).__name__):
                orchestrator = _FailingOrchestrator(error)
                service = BattlePreparationService(
                    self.profiles,
                    self.quizzes,
                    orchestrator,
                )
                with self.assertRaises(BattlePreparationError) as raised:
                    asyncio.run(
                        service.prepare(
                            self.request(),
                            profile_id=self.profile.profile_id,
                            current_session_id=self.session.session_id,
                        )
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("sensitive", repr(raised.exception))
                self.assertEqual(orchestrator.calls, 1)

    def test_existing_resumable_batch_stops_before_orchestration(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
        )
        from services.wayline_forge.app.quiz_machine import QuizItemLayout, new_quiz

        layouts = tuple(
            QuizItemLayout(
                item_id=f"existing-item-{index:03d}",
                option_ids=tuple(
                    f"existing-option-{index:03d}-{option}"
                    for option in range(4)
                ),
            )
            for index in range(3)
        )
        self.quizzes.create(
            new_quiz("batch-existing-live-001", layouts),
            profile_id=self.profile.profile_id,
        )

        with self.assertRaises(BattlePreparationError) as raised:
            self.prepare()

        self.assertEqual(raised.exception.code, "quiz_in_progress")
        self.assertEqual(self.orchestrator.calls, [])

    def test_seed_is_stable_for_canonical_identity_and_changes_with_request(self) -> None:
        request = self.request()
        self.prepare(request)
        self.prepare(request)
        changed = self.request(requestId="prepare-request-seed-other-001")
        self.prepare(changed)

        seeds = tuple(call["batch_seed"] for call in self.orchestrator.calls)
        self.assertEqual(seeds[0], seeds[1])
        self.assertNotEqual(seeds[0], seeds[2])
        self.assertEqual(
            {call["content_version_id"] for call in self.orchestrator.calls},
            {"wayline-launch-core-v1"},
        )

    def test_pending_outbox_is_drained_into_authoritative_state_before_planning(self) -> None:
        from services.wayline_forge.app.quiz_machine import (
            QuizSelection,
            QuizSubmission,
            close_quiz,
            submit_initial,
        )
        from services.wayline_forge.app.quiz_observations import (
            build_reveal_observations,
        )

        old_request = self.request(requestId="prepare-old-batch-001")
        material = self._material_for_request(old_request)
        prepared = self.quizzes.create_prepared(
            material,
            request=old_request,
            profile_id=self.profile.profile_id,
        )
        submission = QuizSubmission(
            schema_version="wayline.v1",
            request_id="initial-old-batch-001",
            batch_id=material.batch_id,
            item_count=len(material.items),
            selections=tuple(
                QuizSelection(
                    item_id=item.item_id,
                    option_id=next(
                        route.option_id
                        for route in item.routes
                        if route.procedure_id is None
                    ),
                    confidence="certain",
                )
                for item in material.items
            ),
        )
        reveal = submit_initial(
            prepared.machine,
            submission,
            material.sealed_quiz,
            expected_version=prepared.machine.version,
        )
        observations = build_reveal_observations(
            material,
            reveal.machine,
            reveal.receipt,
            profile_id=self.profile.profile_id,
            reveal_session_id=self.session.session_id,
            first_ordinal=self.quizzes.next_profile_ordinal(self.profile.profile_id),
            occurred_at="2026-07-11T23:00:00Z",
        )
        revealed = self.quizzes.save_transition(
            reveal.machine,
            profile_id=self.profile.profile_id,
            expected_version=prepared.machine.version,
            receipt=reveal.receipt,
            observation_events=observations,
            observation_session_id=self.session.session_id,
        )
        closed = close_quiz(
            revealed.machine,
            expected_version=revealed.machine.version,
        )
        self.quizzes.save_transition(
            closed,
            profile_id=self.profile.profile_id,
            expected_version=revealed.machine.version,
        )
        self.assertEqual(
            len(self.quizzes.pending_observations(self.profile.profile_id)),
            3,
        )
        self.assertEqual(
            self.profiles.load_state(self.profile.profile_id).answer_records,
            (),
        )

        result = self.prepare(
            self.request(requestId="prepare-after-evidence-sync-001")
        )

        self.assertEqual(result, self.orchestrator.output)
        planned_state = self.orchestrator.calls[0]["learner_state"]
        self.assertEqual(len(planned_state.answer_records), 3)
        self.assertEqual(
            self.quizzes.pending_observations(self.profile.profile_id),
            (),
        )

    def test_session_closing_during_await_denies_return_but_keeps_resumable_batch(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationError,
            BattlePreparationService,
        )

        request = self.request(requestId="prepare-closing-session-001")
        material = self._material_for_request(request)

        class PersistThenClose:
            async def prepare(inner_self, actual: BattleQuizRequest, **kwargs: object):
                persisted = self.quizzes.create_prepared(
                    material,
                    request=actual,
                    profile_id=self.profile.profile_id,
                )
                self.profiles.create_session(
                    request_id="session-request-during-prepare-001",
                    profile_id=self.profile.profile_id,
                    client_build="mac-demo-0.1.1",
                )
                return persisted

        service = BattlePreparationService(
            self.profiles,
            self.quizzes,
            PersistThenClose(),
        )

        with self.assertRaises(BattlePreparationError) as raised:
            asyncio.run(
                service.prepare(
                    request,
                    profile_id=self.profile.profile_id,
                    current_session_id=self.session.session_id,
                )
            )

        self.assertEqual(raised.exception.code, "session_not_current")
        replay = self.quizzes.load_preparation(
            request,
            profile_id=self.profile.profile_id,
        )
        self.assertIsNotNone(replay)
        self.assertEqual(
            self.quizzes.resumable_batch_id(self.profile.profile_id),
            material.batch_id,
        )

    def test_real_orchestrator_integration_persists_a_verified_public_batch(self) -> None:
        from services.wayline_forge.app.battle_preparation import (
            BattlePreparationService,
        )
        from services.wayline_forge.app.orchestrator import (
            BatchPreparationOrchestrator,
        )
        from services.wayline_forge.tests.test_orchestrator import (
            DeterministicIds,
            DynamicReviewedCache,
            FakeClock,
            FakeProvider,
        )

        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock)
        ids = DeterministicIds("b")
        orchestrator = BatchPreparationOrchestrator(
            store=self.quizzes,
            compiler=self.verifier.compiler,
            verifier=self.verifier,
            manifest=self.verifier.manifest,
            provider=provider,
            reviewed_cache=cache,
            monotonic=clock,
            sleeper=clock.sleep,
            batch_id_factory=ids.batch,
            item_id_factory=ids.item,
        )
        service = BattlePreparationService(
            self.profiles,
            self.quizzes,
            orchestrator,
        )

        result = asyncio.run(
            service.prepare(
                self.request(requestId="prepare-real-orchestrator-001"),
                profile_id=self.profile.profile_id,
                current_session_id=self.session.session_id,
            )
        )

        self.assertEqual(result.item_count, 3)
        self.assertGreater(len(provider.requests), 0)
        self.assertEqual(
            self.quizzes.resumable_batch_id(self.profile.profile_id),
            result.batch_id,
        )


if __name__ == "__main__":
    unittest.main()
