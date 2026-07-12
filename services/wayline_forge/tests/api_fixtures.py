"""Complete transport fixtures for the Wayline Forge FastAPI adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from services.wayline_forge.app.contracts import (
    AssistedRouteComplete,
    AssistedRouteCompleted,
    AssistedRoutePrepared,
    BattleQuizRequest,
    BossGateResult,
    FinalQuizResult,
    InitialSubmission,
    InitialSubmissionResult,
    ProfileCreate,
    ProfileCreated,
    ProfileExportV1,
    PublicQuizBatch,
    QuizSnapshot,
    RevisionSubmission,
    RuntimeState,
    SessionCreate,
    SessionCreated,
)
from services.wayline_forge.app.loopback_security import LaunchSecurityPolicy
from services.wayline_forge.app.progression import (
    AssistedRouteCompletionRequest,
    AssistedRouteCompletionResult,
    AssistedRoutePreparationRequest,
    AssistedRoutePreparationResult,
    BattleCompletionRequest,
    BattleCompletionResult,
    RevivedCombatCompletionRequest,
    RevivedCombatCompletionResult,
    SealTrialCompletionRequest,
    SealTrialCompletionResult,
    SealTrialPreparationRequest,
    SealTrialPreparationResult,
    SecondWindCompletionRequest,
    SecondWindCompletionResult,
    SecondWindStartRequest,
    SecondWindStartResult,
    WorldActivationRequest,
    WorldActivationResult,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FIXTURES = (
    SERVICE_ROOT.parents[1] / "contracts/wayline/v1/fixtures/valid"
)
TOKEN = "a" * 64
UNITY_ORIGIN = "http://127.0.0.1:49152"
PROFILE_ID = "profile-001"
SESSION_ID = "session-001"
BATCH_ID = "batch-001"


class FacadeFailure(RuntimeError):
    """A stable-code failure emitted by the injected test facade."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _load_contract(name: str, model_type: type[Any]) -> Any:
    return model_type.model_validate_json(
        (CONTRACT_FIXTURES / name).read_text(encoding="utf-8")
    )


def public_batch() -> PublicQuizBatch:
    return _load_contract("three-item-batch.json", PublicQuizBatch)


def final_result() -> FinalQuizResult:
    return _load_contract("final-result.json", FinalQuizResult)


def initial_result() -> InitialSubmissionResult:
    return InitialSubmissionResult(
        schemaVersion="wayline.v1",
        batchId=BATCH_ID,
        itemCount=3,
        wrongCount=2,
        revisionRequired=True,
        finalResult=None,
    )


def assisted_prepared() -> AssistedRoutePrepared:
    return _load_contract("assisted-route-prepared.json", AssistedRoutePrepared)


def assisted_complete() -> AssistedRouteComplete:
    return _load_contract("assisted-route-complete.json", AssistedRouteComplete)


def assisted_completed() -> AssistedRouteCompleted:
    return _load_contract("assisted-route-completed.json", AssistedRouteCompleted)


def quiz_snapshot() -> QuizSnapshot:
    return QuizSnapshot(
        schemaVersion="wayline.v1",
        batchId=BATCH_ID,
        quizState="ready",
        stateVersion=1,
        publicBatch=public_batch(),
        initialSubmission=None,
        initialResult=None,
        revisionSubmission=None,
        finalResult=None,
    )


def runtime_state() -> RuntimeState:
    return RuntimeState(
        schemaVersion="wayline.v1",
        profileId=PROFILE_ID,
        sessionId=SESSION_ID,
        activeWorldId="valuehold",
        campaignOrdinal=1,
        resumableBatchId=BATCH_ID,
        campaignCatalogSha256=(
            "5509097676eccc6c3848bfb64295ac931"
            "c73621a1120b9431af0ccc8e793d513"
        ),
    )


def boss_gate() -> BossGateResult:
    return BossGateResult(
        schemaVersion="wayline.v1",
        worldId="valuehold",
        unlocked=False,
        leadInWins=3,
        requiredLeadInWins=4,
        validWorldItems=15,
        requiredValidWorldItems=16,
        latestTenItemCount=10,
        latestTenCorrectCount=7,
        requiredLatestTenCorrectCount=7,
        coreSubskillCount=3,
        readyCoreSubskillCount=2,
        unmetRequirements=[
            "lead_in_wins",
            "valid_world_items",
            "core_subskill_coverage",
        ],
    )


def profile_export() -> ProfileExportV1:
    return ProfileExportV1(
        schemaVersion="wayline.profile-export.v1",
        profileId=PROFILE_ID,
        createdAtUtc="2026-07-12T12:00:00.000000Z",
        campaignCatalogSha256=(
            "5509097676eccc6c3848bfb64295ac931"
            "c73621a1120b9431af0ccc8e793d513"
        ),
        activeWorldId=None,
        campaignOrdinal=None,
        sessions=[],
        events=[],
        terminalEventChainSha256="0" * 64,
    )


class RecordingFacade:
    """A complete facade double whose return values are strict contracts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.failure_by_method: dict[str, FacadeFailure] = {}
        self._assisted_completion: tuple[
            str,
            tuple[tuple[str, str, str], ...],
        ] | None = None

    def _record(
        self,
        method: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        failure = self.failure_by_method.get(method)
        if failure is not None:
            raise failure
        self.calls.append((method, args, kwargs))

    def create_profile(self, request: ProfileCreate) -> ProfileCreated:
        self._record("create_profile", request)
        return ProfileCreated(
            schemaVersion="wayline.v1",
            profileId=PROFILE_ID,
            createdAtUtc="2026-07-12T12:00:00.000000Z",
        )

    def create_session(self, request: SessionCreate) -> SessionCreated:
        self._record("create_session", request)
        return SessionCreated(
            schemaVersion="wayline.v1",
            profileId=PROFILE_ID,
            sessionId=SESSION_ID,
            createdAtUtc="2026-07-12T12:01:00.000000Z",
            activeWorldId="valuehold",
            campaignCatalogSha256=(
                "5509097676eccc6c3848bfb64295ac931"
                "c73621a1120b9431af0ccc8e793d513"
            ),
        )

    async def prepare_battle(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch:
        self._record(
            "prepare_battle",
            request,
            profile_id=profile_id,
            current_session_id=current_session_id,
        )
        return public_batch()

    async def prepare_seal_trial(
        self,
        request: SealTrialPreparationRequest,
    ) -> SealTrialPreparationResult:
        self._record("prepare_seal_trial", request)
        return SealTrialPreparationResult(
            request_id=request.request_id,
            world_id=request.world_id,
            attempt_number=1,
            battle_id=f"{request.world_id}_seal_trial_1",
            batch=public_batch(),
        )

    def complete_battle(
        self,
        request: BattleCompletionRequest,
    ) -> BattleCompletionResult:
        self._record("complete_battle", request)
        return BattleCompletionResult(
            request.request_id,
            request.world_id,
            request.battle_id,
            request.batch_id,
            3,
            3,
            False,
            False,
            False,
        )

    def complete_seal_trial(
        self,
        request: SealTrialCompletionRequest,
    ) -> SealTrialCompletionResult:
        self._record("complete_seal_trial", request)
        return SealTrialCompletionResult(
            request.request_id,
            request.world_id,
            1,
            request.batch_id,
            2,
            3,
            True,
            True,
            False,
        )

    async def prepare_assisted_route(
        self,
        request: AssistedRoutePreparationRequest,
    ) -> AssistedRoutePreparationResult:
        self._record("prepare_assisted_route", request)
        prepared = assisted_prepared()
        return AssistedRoutePreparationResult(
            request_id=request.request_id,
            batch=prepared.batch,
        )

    def complete_assisted_route(
        self,
        request: AssistedRouteCompletionRequest,
    ) -> AssistedRouteCompletionResult:
        self._record("complete_assisted_route", request)
        prepared = assisted_prepared()
        expected = assisted_complete()
        selection_payload = tuple(
            (
                selection.item_id,
                selection.option_id,
                selection.confidence.value,
            )
            for selection in request.selections
        )
        expected_payload = tuple(
            (
                selection.item_id,
                selection.option_id,
                selection.confidence.value,
            )
            for selection in expected.selections
        )
        if request.route_id != prepared.batch.route_id or selection_payload != expected_payload:
            raise FacadeFailure("quiz_context_mismatch")
        identity = (request.request_id, selection_payload)
        if self._assisted_completion is None:
            self._assisted_completion = identity
        elif self._assisted_completion != identity:
            if self._assisted_completion[0] == request.request_id:
                raise FacadeFailure("idempotency_conflict")
            raise FacadeFailure("target_already_completed")
        completed = assisted_completed()
        return AssistedRouteCompletionResult(
            request_id=request.request_id,
            world_id=request.world_id,
            route_id=request.route_id,
            worked_example_count=completed.worked_example_count,
            supported_mcq_count=completed.supported_mcq_count,
            final_correct=completed.final_correct,
            world_cleared=completed.world_cleared,
            items=completed.items,
        )

    async def start_second_wind(
        self,
        request: SecondWindStartRequest,
    ) -> SecondWindStartResult:
        self._record("start_second_wind", request)
        return SecondWindStartResult(
            request.request_id,
            f"second-wind-{request.combat_attempt_id}",
            request.world_id,
            request.battle_id,
            request.combat_attempt_id,
            f"{request.battle_id}_second_wind",
            public_batch(),
        )

    def complete_second_wind(
        self,
        request: SecondWindCompletionRequest,
    ) -> SecondWindCompletionResult:
        self._record("complete_second_wind", request)
        return SecondWindCompletionResult(
            request.request_id,
            request.second_wind_id,
            request.batch_id,
            3,
            3,
            35,
            15,
            True,
        )

    def complete_revived_combat(
        self,
        request: RevivedCombatCompletionRequest,
    ) -> RevivedCombatCompletionResult:
        self._record("complete_revived_combat", request)
        return RevivedCombatCompletionResult(
            request.request_id,
            request.second_wind_id,
            request.combat_attempt_id,
            request.combat_won,
            request.combat_won,
            True,
        )

    def activate_world(
        self,
        request: WorldActivationRequest,
    ) -> WorldActivationResult:
        self._record("activate_world", request)
        return WorldActivationResult(
            request.request_id,
            request.completed_world_id,
            request.next_world_id,
            2,
        )

    def submit_initial(
        self,
        submission: InitialSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> InitialSubmissionResult:
        self._record(
            "submit_initial",
            submission,
            profile_id=profile_id,
            current_session_id=current_session_id,
        )
        return initial_result()

    def submit_revision(
        self,
        submission: RevisionSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> FinalQuizResult:
        self._record(
            "submit_revision",
            submission,
            profile_id=profile_id,
            current_session_id=current_session_id,
        )
        return final_result()

    def get_quiz_snapshot(
        self,
        batch_id: str,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> QuizSnapshot:
        if batch_id == assisted_prepared().batch.route_id:
            raise FacadeFailure("batch_unavailable")
        self._record(
            "get_quiz_snapshot",
            batch_id,
            profile_id=profile_id,
            current_session_id=current_session_id,
        )
        return quiz_snapshot()

    def get_runtime_state(
        self,
        profile_id: str,
        session_id: str,
    ) -> RuntimeState:
        self._record("get_runtime_state", profile_id, session_id)
        return runtime_state()

    def get_boss_gate(
        self,
        *,
        profile_id: str,
        current_session_id: str,
        world_id: str,
    ) -> BossGateResult:
        self._record(
            "get_boss_gate",
            profile_id=profile_id,
            current_session_id=current_session_id,
            world_id=world_id,
        )
        return boss_gate()

    def delete_profile(
        self,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> None:
        self._record(
            "delete_profile",
            profile_id=profile_id,
            current_session_id=current_session_id,
        )

    def export_profile(
        self,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> ProfileExportV1:
        self._record(
            "export_profile",
            profile_id=profile_id,
            current_session_id=current_session_id,
        )
        return profile_export()


class ApiFixture:
    """Build one authenticated ASGI client and deterministic facade."""

    def __init__(self) -> None:
        from services.wayline_forge.app.api import create_api

        self.facade = RecordingFacade()
        self.resolved_sessions: list[str] = []
        self.security = LaunchSecurityPolicy(
            unity_origin=UNITY_ORIGIN,
            launch_token=TOKEN,
        )
        self.api = create_api(
            self.facade,
            security=self.security,
            resolve_profile_id=self.resolve_profile_id,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=self.api,
                raise_app_exceptions=False,
            ),
            base_url=UNITY_ORIGIN,
        )

    def resolve_profile_id(self, session_id: str) -> str:
        self.resolved_sessions.append(session_id)
        if session_id != SESSION_ID:
            raise LookupError("not current")
        return PROFILE_ID

    @staticmethod
    def public_headers(*, session: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Origin": UNITY_ORIGIN,
        }
        if session:
            headers["X-Wayline-Session-Id"] = SESSION_ID
        return headers

    @staticmethod
    def profile_payload() -> dict[str, Any]:
        return {
            "schemaVersion": "wayline.v1",
            "requestId": "profile-request-001",
        }

    @staticmethod
    def session_payload() -> dict[str, Any]:
        return {
            "schemaVersion": "wayline.v1",
            "requestId": "session-request-001",
            "profileId": PROFILE_ID,
            "clientBuild": "mac-demo-0.1.0",
        }

    @staticmethod
    def battle_payload() -> dict[str, Any]:
        return {
            "schemaVersion": "wayline.v1",
            "requestId": "prepare-request-001",
            "sessionId": SESSION_ID,
            "battleId": "valuehold_route_1",
            "worldId": "valuehold",
            "battleTier": "route_1",
        }

    @staticmethod
    def seal_trial_payload() -> dict[str, Any]:
        return {
            "schemaVersion": "wayline.v1",
            "requestId": "seal-request-001",
            "sessionId": SESSION_ID,
        }

    @staticmethod
    def public_batch_payload() -> dict[str, Any]:
        return public_batch().model_dump(mode="json", by_alias=True)

    @staticmethod
    def initial_payload() -> dict[str, Any]:
        return {
            "schemaVersion": "wayline.v1",
            "requestId": "initial-request-001",
            "batchId": BATCH_ID,
            "itemCount": 3,
            "selections": [
                {
                    "itemId": "item-001",
                    "optionId": "opt-001-b",
                    "confidence": "leaning",
                },
                {
                    "itemId": "item-002",
                    "optionId": "opt-002-a",
                    "confidence": "certain",
                },
                {
                    "itemId": "item-003",
                    "optionId": "opt-003-b",
                    "confidence": "guessing",
                },
            ],
        }

    @classmethod
    def revision_payload(cls) -> dict[str, Any]:
        payload = cls.initial_payload()
        payload["requestId"] = "revision-request-001"
        payload["selections"] = [
            {
                "itemId": "item-001",
                "optionId": "opt-001-a",
                "confidence": "certain",
            },
            {
                "itemId": "item-002",
                "optionId": "opt-002-a",
                "confidence": "certain",
            },
            {
                "itemId": "item-003",
                "optionId": "opt-003-a",
                "confidence": "leaning",
            },
        ]
        return payload

    @staticmethod
    def duplicate_profile_json() -> bytes:
        return (
            b'{"schemaVersion":"wayline.v1",'
            b'"requestId":"profile-request-001",'
            b'"requestId":"profile-request-002"}'
        )

    async def close(self) -> None:
        await self.client.aclose()


def compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
