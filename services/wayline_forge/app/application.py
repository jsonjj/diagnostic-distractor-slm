"""Transport-neutral composition root for the approved Wayline runtime slice.

This facade delegates strict public contracts to existing application services.
It owns no stores, answer keys, model generations, learner evidence, or
transport behavior; progression remains owned by ProgressionCommandService.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from services.wayline_forge.app.battle_preparation import (
    BattlePreparationService,
)
from services.wayline_forge.app.assisted_route_store import AssistedRouteStore
from services.wayline_forge.app.contracts import (
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
from services.wayline_forge.app.gate_query import BossGateQueryService
from services.wayline_forge.app.identity_lifecycle import (
    IdentityLifecycleService,
)
from services.wayline_forge.app.orchestrator import (
    BatchPreparationOrchestrator,
)
from services.wayline_forge.app.profile_store import ProfileStore
from services.wayline_forge.app.profile_deletion import ProfileDeletionService
from services.wayline_forge.app.profile_export import ProfileExportService
from services.wayline_forge.app.quiz_snapshot import QuizSnapshotService
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.app.quiz_submissions import QuizSubmissionService
from services.wayline_forge.app.progression import (
    AssistedRouteCompletionRequest,
    AssistedRouteCompletionResult,
    AssistedRoutePreparationRequest,
    AssistedRoutePreparationResult,
    BattleCompletionRequest,
    BattleCompletionResult,
    ProgressionCommandService,
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
from services.wayline_forge.app.runtime_state import RuntimeStateService


class WaylineApplication:
    """Delegate the approved learning and progression loop without new policy."""

    __slots__ = (
        "_battle_preparation",
        "_boss_gate",
        "_deletion",
        "_export",
        "_identity",
        "_progression",
        "_runtime_state",
        "_snapshot",
        "_submissions",
    )

    def __init__(
        self,
        *,
        profile_store: ProfileStore,
        quiz_store: QuizStore,
        orchestrator: BatchPreparationOrchestrator,
        assisted_route_store: AssistedRouteStore,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if type(profile_store) is not ProfileStore:
            raise TypeError("profile_store must be a ProfileStore")
        if type(quiz_store) is not QuizStore:
            raise TypeError("quiz_store must be a QuizStore")
        if type(orchestrator) is not BatchPreparationOrchestrator:
            raise TypeError(
                "orchestrator must be a BatchPreparationOrchestrator"
            )
        if type(assisted_route_store) is not AssistedRouteStore:
            raise TypeError("assisted_route_store must be an AssistedRouteStore")
        self._identity = IdentityLifecycleService(profile_store)
        self._deletion = ProfileDeletionService(profile_store)
        self._export = ProfileExportService(profile_store)
        self._battle_preparation = BattlePreparationService(
            profile_store,
            quiz_store,
            orchestrator,
            assisted_route_store=assisted_route_store,
        )
        self._progression = ProgressionCommandService(
            profile_store,
            quiz_store,
            self._battle_preparation,
            assisted_route_store=assisted_route_store,
            utc_now=utc_now,
        )
        if utc_now is None:
            self._submissions = QuizSubmissionService(
                profile_store,
                quiz_store,
            )
        else:
            if not callable(utc_now):
                raise TypeError("utc_now must be callable")
            self._submissions = QuizSubmissionService(
                profile_store,
                quiz_store,
                utc_now=utc_now,
            )
        self._runtime_state = RuntimeStateService(profile_store, quiz_store)
        self._snapshot = QuizSnapshotService(profile_store, quiz_store)
        self._boss_gate = BossGateQueryService(profile_store, quiz_store)

    def create_profile(self, request: ProfileCreate) -> ProfileCreated:
        return self._identity.create_profile(request)

    def create_session(self, request: SessionCreate) -> SessionCreated:
        return self._identity.create_session(request)

    async def prepare_battle(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch:
        return await self._battle_preparation.prepare(
            request,
            profile_id=profile_id,
            current_session_id=current_session_id,
        )

    async def prepare_seal_trial(
        self,
        request: SealTrialPreparationRequest,
    ) -> SealTrialPreparationResult:
        return await self._progression.prepare_seal_trial(request)

    def complete_battle(
        self,
        request: BattleCompletionRequest,
    ) -> BattleCompletionResult:
        return self._progression.complete_battle(request)

    def complete_seal_trial(
        self,
        request: SealTrialCompletionRequest,
    ) -> SealTrialCompletionResult:
        return self._progression.complete_seal_trial(request)

    async def prepare_assisted_route(
        self,
        request: AssistedRoutePreparationRequest,
    ) -> AssistedRoutePreparationResult:
        return await self._progression.prepare_assisted_route(request)

    def complete_assisted_route(
        self,
        request: AssistedRouteCompletionRequest,
    ) -> AssistedRouteCompletionResult:
        return self._progression.complete_assisted_route(request)

    async def start_second_wind(
        self,
        request: SecondWindStartRequest,
    ) -> SecondWindStartResult:
        return await self._progression.start_second_wind(request)

    def complete_second_wind(
        self,
        request: SecondWindCompletionRequest,
    ) -> SecondWindCompletionResult:
        return self._progression.complete_second_wind(request)

    def complete_revived_combat(
        self,
        request: RevivedCombatCompletionRequest,
    ) -> RevivedCombatCompletionResult:
        return self._progression.complete_revived_combat(request)

    def activate_world(
        self,
        request: WorldActivationRequest,
    ) -> WorldActivationResult:
        return self._progression.activate_world(request)

    def submit_initial(
        self,
        submission: InitialSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> InitialSubmissionResult:
        return self._submissions.submit_initial(
            submission,
            profile_id=profile_id,
            current_session_id=current_session_id,
        )

    def submit_revision(
        self,
        submission: RevisionSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> FinalQuizResult:
        return self._submissions.submit_revision(
            submission,
            profile_id=profile_id,
            current_session_id=current_session_id,
        )

    def get_runtime_state(
        self,
        profile_id: str,
        session_id: str,
    ) -> RuntimeState:
        return self._runtime_state.get(profile_id, session_id)

    def get_quiz_snapshot(
        self,
        batch_id: str,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> QuizSnapshot:
        return self._snapshot.get(
            profile_id,
            current_session_id,
            batch_id,
        )

    def delete_profile(
        self,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> None:
        self._deletion.delete(profile_id, current_session_id)

    def export_profile(
        self,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> ProfileExportV1:
        return self._export.export(profile_id, current_session_id)

    def get_boss_gate(
        self,
        *,
        profile_id: str,
        current_session_id: str,
        world_id: str,
    ) -> BossGateResult:
        return self._boss_gate.get(
            profile_id=profile_id,
            current_session_id=current_session_id,
            world_id=world_id,
        )


__all__ = ["WaylineApplication"]
