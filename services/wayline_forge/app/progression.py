"""Transport-neutral, fail-closed commands for authoritative campaign outcomes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Callable, Final, Protocol

from services.wayline_forge.app.boss_gate import (
    evaluate_boss_gate,
    evaluate_world_clear,
)
from services.wayline_forge.app.campaign_catalog import (
    CampaignBattle,
    CampaignCatalog,
    CampaignWorld,
)
from services.wayline_forge.app.contracts import (
    AssistedItemResult,
    AssistedRouteBatch,
    AssistedSelection,
    BattleQuizRequest,
    PublicQuizBatch,
)
from services.wayline_forge.app.assisted_route_machine import (
    AssistedRouteMachineError,
    score_assisted_route,
)
from services.wayline_forge.app.assisted_route_store import (
    AssistedRouteStore,
    AssistedRouteStoreError,
)
from services.wayline_forge.app.events import (
    OUTCOME_EVENT_SCHEMA_VERSION,
    AssistedRouteCompletionEvent,
    BattleCompletionEvent,
    BossCompletionEvent,
    BossOutcomeEvent,
    SealTrialCompletionEvent,
    SealTrialOutcomeEvent,
    SecondWindCombatOutcomeEvent,
    SecondWindQuizCompletionEvent,
    SecondWindStartedEvent,
    WorldActivatedEvent,
    WorldProgressionActivatedEvent,
)
from services.wayline_forge.app.evidence_reducer import reduce_events
from services.wayline_forge.app.profile_store import (
    CampaignStateConflictError,
    EventLogCorruptionError,
    IdempotencyConflictError,
    IdentityStoreCorruptionError,
    LegacyOutcomeProfileError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    SemanticEventConflictError,
    SessionNotFoundError,
)
from services.wayline_forge.app.quiz_machine import QuizState


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}", re.ASCII)


def _identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a valid identifier")
    return value


@dataclass(frozen=True, slots=True)
class BattleCompletionRequest:
    request_id: str
    profile_id: str
    session_id: str
    world_id: str
    battle_id: str
    batch_id: str
    combat_won: bool

    def __post_init__(self) -> None:
        for name in (
            "request_id", "profile_id", "session_id", "world_id",
            "battle_id", "batch_id",
        ):
            _identifier(name, getattr(self, name))
        if type(self.combat_won) is not bool:
            raise ValueError("combat_won must be a boolean")


@dataclass(frozen=True, slots=True)
class BattleCompletionResult:
    request_id: str
    world_id: str
    battle_id: str
    batch_id: str
    final_correct: int
    item_count: int
    boss_battle: bool
    world_cleared: bool
    seal_trial_required: bool


@dataclass(frozen=True, slots=True)
class SealTrialPreparationRequest:
    request_id: str
    profile_id: str
    session_id: str
    world_id: str

    def __post_init__(self) -> None:
        for name in ("request_id", "profile_id", "session_id", "world_id"):
            _identifier(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class SealTrialPreparationResult:
    request_id: str
    world_id: str
    attempt_number: int
    battle_id: str
    batch: PublicQuizBatch


@dataclass(frozen=True, slots=True)
class SealTrialCompletionRequest:
    request_id: str
    profile_id: str
    session_id: str
    world_id: str
    batch_id: str

    def __post_init__(self) -> None:
        for name in (
            "request_id", "profile_id", "session_id", "world_id", "batch_id",
        ):
            _identifier(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class SealTrialCompletionResult:
    request_id: str
    world_id: str
    attempt_number: int
    batch_id: str
    final_correct: int
    item_count: int
    passed: bool
    world_cleared: bool
    assisted_route_unlocked: bool


@dataclass(frozen=True, slots=True)
class AssistedRoutePreparationRequest:
    request_id: str
    profile_id: str
    session_id: str
    world_id: str

    def __post_init__(self) -> None:
        for name in ("request_id", "profile_id", "session_id", "world_id"):
            _identifier(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class AssistedRoutePreparationResult:
    request_id: str
    batch: AssistedRouteBatch


@dataclass(frozen=True, slots=True)
class AssistedRouteCompletionRequest:
    request_id: str
    profile_id: str
    session_id: str
    world_id: str
    route_id: str
    selections: tuple[AssistedSelection, AssistedSelection]

    def __post_init__(self) -> None:
        for name in (
            "request_id", "profile_id", "session_id", "world_id", "route_id",
        ):
            _identifier(name, getattr(self, name))
        if (
            not isinstance(self.selections, tuple)
            or len(self.selections) != 2
            or any(
                not isinstance(selection, AssistedSelection)
                for selection in self.selections
            )
            or len({selection.item_id for selection in self.selections}) != 2
        ):
            raise ValueError("selections must contain exactly two supported MCQs")


@dataclass(frozen=True, slots=True)
class AssistedRouteCompletionResult:
    request_id: str
    world_id: str
    route_id: str
    worked_example_count: int
    supported_mcq_count: int
    final_correct: int
    world_cleared: bool
    items: tuple[AssistedItemResult, AssistedItemResult]


@dataclass(frozen=True, slots=True)
class SecondWindStartRequest:
    request_id: str
    preparation_request_id: str
    profile_id: str
    session_id: str
    world_id: str
    battle_id: str
    combat_attempt_id: str

    def __post_init__(self) -> None:
        for name in (
            "request_id", "preparation_request_id", "profile_id", "session_id",
            "world_id", "battle_id", "combat_attempt_id",
        ):
            _identifier(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class SecondWindStartResult:
    request_id: str
    second_wind_id: str
    world_id: str
    battle_id: str
    combat_attempt_id: str
    quiz_battle_id: str
    batch: PublicQuizBatch


@dataclass(frozen=True, slots=True)
class SecondWindCompletionRequest:
    request_id: str
    profile_id: str
    session_id: str
    second_wind_id: str
    batch_id: str

    def __post_init__(self) -> None:
        for name in (
            "request_id", "profile_id", "session_id", "second_wind_id", "batch_id",
        ):
            _identifier(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class SecondWindCompletionResult:
    request_id: str
    second_wind_id: str
    batch_id: str
    final_correct: int
    item_count: int
    revive_health_percent: int
    shield_percent: int
    revived_combat_pending: bool


@dataclass(frozen=True, slots=True)
class RevivedCombatCompletionRequest:
    request_id: str
    profile_id: str
    session_id: str
    second_wind_id: str
    combat_attempt_id: str
    combat_won: bool

    def __post_init__(self) -> None:
        for name in (
            "request_id", "profile_id", "session_id", "second_wind_id",
            "combat_attempt_id",
        ):
            _identifier(name, getattr(self, name))
        if type(self.combat_won) is not bool:
            raise ValueError("combat_won must be a boolean")


@dataclass(frozen=True, slots=True)
class RevivedCombatCompletionResult:
    request_id: str
    second_wind_id: str
    combat_attempt_id: str
    combat_won: bool
    battle_completed: bool
    second_wind_closed: bool


@dataclass(frozen=True, slots=True)
class WorldActivationRequest:
    request_id: str
    profile_id: str
    session_id: str
    completed_world_id: str
    next_world_id: str

    def __post_init__(self) -> None:
        for name in (
            "request_id", "profile_id", "session_id", "completed_world_id",
            "next_world_id",
        ):
            _identifier(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class WorldActivationResult:
    request_id: str
    completed_world_id: str
    active_world_id: str
    campaign_sequence: int


class _QuizAuthority(Protocol):
    def drain_observations(self, profile_id: str, *, profile_store: ProfileStore) -> int: ...
    def load(self, batch_id: str, *, profile_id: str) -> object: ...
    def load_batch_material(self, batch_id: str, *, profile_id: str) -> object: ...
    def close_revealed(self, batch_id: str, *, profile_id: str) -> object: ...


class _SpecialBattlePreparer(Protocol):
    async def prepare_seal_trial(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch: ...

    async def prepare_second_wind(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch: ...

    async def prepare_assisted_route(
        self,
        *,
        request_id: str,
        profile_id: str,
        current_session_id: str,
        world_id: str,
    ) -> AssistedRouteBatch: ...


class ProgressionCommandError(RuntimeError):
    _HTTP_STATUS: Final[dict[str, int]] = {
        "session_not_current": 409,
        "idempotency_conflict": 409,
        "target_already_completed": 409,
        "target_in_progress": 409,
        "invalid_transition": 409,
        "catalog_conflict": 409,
        "boss_gate_locked": 409,
        "quiz_not_revealed": 409,
        "quiz_context_mismatch": 409,
        "safe_content_unavailable": 503,
        "storage_busy": 503,
        "legacy_profile_blocked": 409,
        "integrity_failure": 500,
    }

    def __init__(self, code: str) -> None:
        if code not in self._HTTP_STATUS:
            raise ValueError("unknown progression command error code")
        self.code = code
        self.http_status = self._HTTP_STATUS[code]
        super().__init__(code)


class ProgressionCommandService:
    """Validate and append one authoritative outcome per progression command."""

    def __init__(
        self,
        profile_store: ProfileStore,
        quiz_authority: _QuizAuthority,
        special_preparer: _SpecialBattlePreparer,
        *,
        assisted_route_store: AssistedRouteStore | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._profiles = profile_store
        self._quizzes = quiz_authority
        self._preparer = special_preparer
        self._assisted_routes = assisted_route_store
        self._catalog = CampaignCatalog.packaged_v1()
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))

    def complete_battle(
        self,
        request: BattleCompletionRequest,
    ) -> BattleCompletionResult:
        self._authenticate(request.profile_id, request.session_id)
        events = self._events(request.profile_id)
        replay = self._battle_replay(events, request)
        if replay is not None:
            self._close_consumed_quiz(request.profile_id, request.batch_id)
            return replay
        state = self._state(request.profile_id)
        world, battle = self._current_battle(state, request.world_id, request.battle_id)
        if not request.combat_won:
            raise ProgressionCommandError("invalid_transition")
        machine, _material, final = self._finalized_quiz(
            request.profile_id,
            request.batch_id,
            session_id=request.session_id,
            world_id=request.world_id,
            battle_id=request.battle_id,
            battle_tier=battle.tier,
            item_count=battle.item_count,
        )
        ordinal = len(self._events(request.profile_id)) + 1
        common = dict(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id=f"completion-{request.request_id}",
            idempotency_id=request.request_id,
            ordinal=ordinal,
            profile_id=request.profile_id,
            session_id=request.session_id,
            world_id=request.world_id,
            battle_id=request.battle_id,
            occurred_at=self._timestamp(),
        )
        if battle.is_boss:
            event = BossCompletionEvent(
                **common,
                combat_won=True,
                final_correct=final.final_correct_count,
                item_count=final.item_count,
                is_campaign_finale=battle.tier == "campaign_finale",
                batch_id=request.batch_id,
            )
        else:
            event = BattleCompletionEvent(
                **common,
                won=True,
                is_lead_in=True,
                batch_id=request.batch_id,
                final_correct=final.final_correct_count,
                item_count=final.item_count,
            )
        self._append(event)
        self._close_consumed_quiz(request.profile_id, request.batch_id)
        clear = evaluate_world_clear(self._state(request.profile_id), request.world_id)
        return BattleCompletionResult(
            request_id=request.request_id,
            world_id=request.world_id,
            battle_id=request.battle_id,
            batch_id=request.batch_id,
            final_correct=final.final_correct_count,
            item_count=final.item_count,
            boss_battle=battle.is_boss,
            world_cleared=clear.cleared,
            seal_trial_required=clear.seal_trial_required,
        )

    async def prepare_seal_trial(
        self,
        request: SealTrialPreparationRequest,
    ) -> SealTrialPreparationResult:
        self._authenticate(request.profile_id, request.session_id)
        state = self._state(request.profile_id)
        if state.active_world_id != request.world_id:
            raise ProgressionCommandError("catalog_conflict")
        clear = evaluate_world_clear(state, request.world_id)
        if not clear.seal_trial_required:
            raise ProgressionCommandError(
                "target_already_completed" if clear.cleared else "invalid_transition"
            )
        attempt = clear.missed_seal_trials + 1
        battle_id = f"{request.world_id}_seal_trial_{attempt}"
        quiz_request = BattleQuizRequest.model_validate(
            {
                "schemaVersion": "wayline.v1",
                "requestId": request.request_id,
                "sessionId": request.session_id,
                "battleId": battle_id,
                "worldId": request.world_id,
                "battleTier": "seal_trial",
            }
        )
        try:
            batch = await self._preparer.prepare_seal_trial(
                quiz_request,
                profile_id=request.profile_id,
                current_session_id=request.session_id,
            )
        except ProgressionCommandError:
            raise
        except Exception:
            raise ProgressionCommandError("safe_content_unavailable") from None
        if type(batch) is not PublicQuizBatch or batch.item_count != 3:
            raise ProgressionCommandError("integrity_failure")
        return SealTrialPreparationResult(
            request.request_id,
            request.world_id,
            attempt,
            battle_id,
            batch,
        )

    def complete_seal_trial(
        self,
        request: SealTrialCompletionRequest,
    ) -> SealTrialCompletionResult:
        self._authenticate(request.profile_id, request.session_id)
        events = self._events(request.profile_id)
        replay = self._seal_replay(events, request)
        if replay is not None:
            self._close_consumed_quiz(request.profile_id, request.batch_id)
            return replay
        state = self._state(request.profile_id)
        clear_before = evaluate_world_clear(state, request.world_id)
        if not clear_before.seal_trial_required:
            raise ProgressionCommandError(
                "target_already_completed" if clear_before.cleared else "invalid_transition"
            )
        attempt = clear_before.missed_seal_trials + 1
        battle_id = f"{request.world_id}_seal_trial_{attempt}"
        _machine, _material, final = self._finalized_quiz(
            request.profile_id,
            request.batch_id,
            session_id=request.session_id,
            world_id=request.world_id,
            battle_id=battle_id,
            battle_tier="seal_trial",
            item_count=3,
        )
        passed = final.final_correct_count >= 2
        event = SealTrialCompletionEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id=f"seal-completion-{request.request_id}",
            idempotency_id=request.request_id,
            ordinal=len(self._events(request.profile_id)) + 1,
            profile_id=request.profile_id,
            session_id=request.session_id,
            world_id=request.world_id,
            battle_id=battle_id,
            occurred_at=self._timestamp(),
            attempt_number=attempt,
            passed=passed,
            final_correct=final.final_correct_count,
            item_count=3,
            batch_id=request.batch_id,
            gate_recheck_sha256=self._gate_recheck_sha256(state, request.world_id),
        )
        self._append(event)
        self._close_consumed_quiz(request.profile_id, request.batch_id)
        clear_after = evaluate_world_clear(self._state(request.profile_id), request.world_id)
        return SealTrialCompletionResult(
            request.request_id,
            request.world_id,
            attempt,
            request.batch_id,
            final.final_correct_count,
            3,
            passed,
            clear_after.cleared,
            clear_after.assisted_route_unlocked,
        )

    async def prepare_assisted_route(
        self,
        request: AssistedRoutePreparationRequest,
    ) -> AssistedRoutePreparationResult:
        self._authenticate(request.profile_id, request.session_id)
        route_store = self._assisted_routes
        if route_store is None:
            raise ProgressionCommandError("integrity_failure")
        preparation_payload_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "profileId": request.profile_id,
                    "requestId": request.request_id,
                    "schemaVersion": "wayline.v1",
                    "sessionId": request.session_id,
                    "worldId": request.world_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        try:
            exact = route_store.load_preparation(
                request.request_id,
                profile_id=request.profile_id,
                payload_sha256=preparation_payload_sha256,
            )
        except AssistedRouteStoreError as error:
            self._raise_assisted_store_error(error)
        if exact is not None:
            if exact.world_id != request.world_id:
                raise ProgressionCommandError("integrity_failure")
            return AssistedRoutePreparationResult(request.request_id, exact.batch)
        state = self._state(request.profile_id)
        clear = evaluate_world_clear(state, request.world_id)
        if clear.cleared:
            raise ProgressionCommandError("target_already_completed")
        if not clear.assisted_route_unlocked or clear.assisted_route_plan is None:
            raise ProgressionCommandError("invalid_transition")
        try:
            batch = await self._preparer.prepare_assisted_route(
                request_id=request.request_id,
                profile_id=request.profile_id,
                current_session_id=request.session_id,
                world_id=request.world_id,
            )
        except ProgressionCommandError:
            raise
        except Exception as error:
            self._raise_assisted_preparation_error(error)
        if (
            type(batch) is not AssistedRouteBatch
            or batch.world_id != request.world_id
            or len(batch.items) != 2
            or batch.worked_example.item_id
            in {item.item_id for item in batch.items}
        ):
            raise ProgressionCommandError("integrity_failure")
        try:
            stored = route_store.load(
                batch.route_id,
                profile_id=request.profile_id,
            )
        except AssistedRouteStoreError as error:
            self._raise_assisted_store_error(error)
        if (
            stored.profile_id != request.profile_id
            or stored.world_id != request.world_id
            or stored.batch != batch
        ):
            raise ProgressionCommandError("integrity_failure")
        return AssistedRoutePreparationResult(request.request_id, batch)

    def complete_assisted_route(
        self,
        request: AssistedRouteCompletionRequest,
    ) -> AssistedRouteCompletionResult:
        self._authenticate(request.profile_id, request.session_id)
        events = self._events(request.profile_id)
        replay = self._assisted_replay(events, request)
        if replay is not None:
            return replay
        state = self._state(request.profile_id)
        clear = evaluate_world_clear(state, request.world_id)
        if clear.cleared:
            raise ProgressionCommandError("target_already_completed")
        if not clear.assisted_route_unlocked or clear.assisted_route_plan is None:
            raise ProgressionCommandError("invalid_transition")
        route_store = self._assisted_routes
        if route_store is None:
            raise ProgressionCommandError("integrity_failure")
        try:
            stored = route_store.load(
                request.route_id,
                profile_id=request.profile_id,
            )
        except AssistedRouteStoreError as error:
            self._raise_assisted_store_error(error)
        if stored.world_id != request.world_id:
            raise ProgressionCommandError("quiz_context_mismatch")
        try:
            score = score_assisted_route(
                request.route_id,
                stored.material,
                request.selections,
            )
        except AssistedRouteMachineError as error:
            if error.code.startswith("selection_"):
                raise ProgressionCommandError("quiz_context_mismatch") from None
            raise ProgressionCommandError("integrity_failure") from None
        supported_question_ids = tuple(
            item.bundle.blueprint.question_id
            for item in stored.material.items[1:]
        )
        if len(supported_question_ids) != 2:
            raise ProgressionCommandError("integrity_failure")
        event = AssistedRouteCompletionEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id=f"assisted-completion-{request.request_id}",
            idempotency_id=request.request_id,
            ordinal=len(self._events(request.profile_id)) + 1,
            profile_id=request.profile_id,
            session_id=request.session_id,
            world_id=request.world_id,
            battle_id=f"{request.world_id}_assisted_route",
            occurred_at=self._timestamp(),
            route_revision="fresh-assisted-v1",
            route_id=stored.route_id,
            material_sha256=score.material_sha256,
            worked_example_item_id=stored.batch.worked_example.item_id,
            supported_item_ids=tuple(item.item_id for item in score.items),
            supported_question_ids=supported_question_ids,
            selected_option_ids=tuple(
                item.selected_option_id for item in score.items
            ),
            selected_answers=tuple(item.selected_answer for item in score.items),
            correct_option_ids=tuple(
                item.correct_option_id for item in score.items
            ),
            correct_answers=tuple(item.correct_answer for item in score.items),
            confidences=tuple(item.confidence.value for item in score.items),
            correctness=tuple(item.is_correct for item in score.items),
            selected_procedure_ids=score.selected_procedure_ids,
            possible_errors=tuple(item.possible_error for item in score.items),
            reliable_methods=tuple(item.reliable_method for item in score.items),
            trusted_steps=tuple(item.trusted_steps for item in score.items),
            canonical_feedback=tuple(
                item.canonical_feedback for item in score.items
            ),
            receipts=score.receipts,
            final_correct=score.final_correct,
            item_count=2,
        )
        try:
            authoritative = self._append(event)
        except ProgressionCommandError as error:
            if error.code not in {
                "idempotency_conflict",
                "target_already_completed",
            }:
                raise
            replay = self._assisted_replay(
                self._events(request.profile_id),
                request,
            )
            if replay is not None:
                return replay
            raise
        if not isinstance(authoritative, AssistedRouteCompletionEvent):
            raise ProgressionCommandError("integrity_failure")
        clear_after = evaluate_world_clear(
            self._state(request.profile_id),
            request.world_id,
        )
        if (
            not clear_after.cleared
            or not clear_after.combat_victory_preserved
            or clear_after.boss_replay_required
            or clear_after.assisted_route_plan is not None
        ):
            raise ProgressionCommandError("integrity_failure")
        return self._assisted_result(authoritative)

    async def start_second_wind(
        self,
        request: SecondWindStartRequest,
    ) -> SecondWindStartResult:
        self._authenticate(request.profile_id, request.session_id)
        events = self._events(request.profile_id)
        started = next(
            (
                event for event in events
                if isinstance(event, SecondWindStartedEvent)
                and event.idempotency_id == request.request_id
            ),
            None,
        )
        if started is not None:
            if (
                started.profile_id != request.profile_id
                or started.session_id != request.session_id
                or started.world_id != request.world_id
                or started.battle_id != request.battle_id
                or started.combat_attempt_id != request.combat_attempt_id
                or started.preparation_request_id != request.preparation_request_id
            ):
                raise ProgressionCommandError("idempotency_conflict")
            if any(
                isinstance(event, SecondWindCombatOutcomeEvent)
                and event.second_wind_id == started.second_wind_id
                for event in events
            ):
                raise ProgressionCommandError("target_already_completed")
        else:
            for event in events:
                if (
                    isinstance(event, SecondWindStartedEvent)
                    and event.world_id == request.world_id
                    and event.battle_id == request.battle_id
                    and event.combat_attempt_id == request.combat_attempt_id
                ):
                    raise ProgressionCommandError("target_in_progress")
            state = self._state(request.profile_id)
            _world, battle = self._current_battle(
                state, request.world_id, request.battle_id
            )
            if not battle.is_lead_in:
                raise ProgressionCommandError("invalid_transition")
            second_wind_id = f"second-wind-{request.combat_attempt_id}"
            quiz_battle_id = f"{request.battle_id}_second_wind"
            started = SecondWindStartedEvent(
                schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
                event_id=f"second-wind-start-{request.request_id}",
                idempotency_id=request.request_id,
                ordinal=len(events) + 1,
                profile_id=request.profile_id,
                session_id=request.session_id,
                world_id=request.world_id,
                battle_id=request.battle_id,
                occurred_at=self._timestamp(),
                second_wind_id=second_wind_id,
                combat_attempt_id=request.combat_attempt_id,
                preparation_request_id=request.preparation_request_id,
                quiz_battle_id=quiz_battle_id,
            )
            try:
                persisted = self._append(started)
            except ProgressionCommandError as error:
                if error.code != "target_already_completed":
                    raise
                concurrent_events = self._events(request.profile_id)
                concurrent = next(
                    (
                        event
                        for event in concurrent_events
                        if isinstance(event, SecondWindStartedEvent)
                        and event.world_id == request.world_id
                        and event.battle_id == request.battle_id
                        and event.combat_attempt_id == request.combat_attempt_id
                    ),
                    None,
                )
                if concurrent is None:
                    raise
                if any(
                    isinstance(event, SecondWindCombatOutcomeEvent)
                    and event.second_wind_id == concurrent.second_wind_id
                    for event in concurrent_events
                ):
                    raise ProgressionCommandError(
                        "target_already_completed"
                    ) from None
                raise ProgressionCommandError("target_in_progress") from None
            if not isinstance(persisted, SecondWindStartedEvent):
                raise ProgressionCommandError("integrity_failure")
            started = persisted
        quiz_request = BattleQuizRequest.model_validate(
            {
                "schemaVersion": "wayline.v1",
                "requestId": request.preparation_request_id,
                "sessionId": request.session_id,
                "battleId": started.quiz_battle_id,
                "worldId": request.world_id,
                "battleTier": "seal_trial",
            }
        )
        try:
            batch = await self._preparer.prepare_second_wind(
                quiz_request,
                profile_id=request.profile_id,
                current_session_id=request.session_id,
            )
        except Exception:
            raise ProgressionCommandError("safe_content_unavailable") from None
        if type(batch) is not PublicQuizBatch or batch.item_count != 3:
            raise ProgressionCommandError("integrity_failure")
        return SecondWindStartResult(
            request.request_id,
            started.second_wind_id,
            request.world_id,
            request.battle_id,
            request.combat_attempt_id,
            started.quiz_battle_id,
            batch,
        )

    def complete_second_wind(
        self,
        request: SecondWindCompletionRequest,
    ) -> SecondWindCompletionResult:
        self._authenticate(request.profile_id, request.session_id)
        events = self._events(request.profile_id)
        start = self._second_wind_start(events, request.second_wind_id)
        for event in events:
            if isinstance(event, SecondWindQuizCompletionEvent):
                if event.idempotency_id == request.request_id:
                    if (
                        event.second_wind_id != request.second_wind_id
                        or event.batch_id != request.batch_id
                    ):
                        raise ProgressionCommandError("idempotency_conflict")
                    self._close_consumed_quiz(
                        request.profile_id,
                        request.batch_id,
                    )
                    return self._second_wind_result(event)
                if event.second_wind_id == request.second_wind_id:
                    raise ProgressionCommandError("target_already_completed")
        _machine, _material, final = self._finalized_quiz(
            request.profile_id,
            request.batch_id,
            session_id=request.session_id,
            world_id=start.world_id,
            battle_id=start.quiz_battle_id,
            battle_tier="seal_trial",
            item_count=3,
        )
        event = SecondWindQuizCompletionEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id=f"second-wind-quiz-{request.request_id}",
            idempotency_id=request.request_id,
            ordinal=len(self._events(request.profile_id)) + 1,
            profile_id=request.profile_id,
            session_id=request.session_id,
            world_id=start.world_id,
            battle_id=start.battle_id,
            occurred_at=self._timestamp(),
            second_wind_id=start.second_wind_id,
            combat_attempt_id=start.combat_attempt_id,
            batch_id=request.batch_id,
            final_correct=final.final_correct_count,
            item_count=3,
            revive_health_percent=35,
            shield_percent=min(final.final_correct_count * 5, 15),
        )
        self._append(event)
        self._close_consumed_quiz(request.profile_id, request.batch_id)
        return self._second_wind_result(event)

    def complete_revived_combat(
        self,
        request: RevivedCombatCompletionRequest,
    ) -> RevivedCombatCompletionResult:
        self._authenticate(request.profile_id, request.session_id)
        events = self._events(request.profile_id)
        start = self._second_wind_start(events, request.second_wind_id)
        completion = next(
            (
                event for event in events
                if isinstance(event, SecondWindQuizCompletionEvent)
                and event.second_wind_id == request.second_wind_id
            ),
            None,
        )
        if completion is None:
            raise ProgressionCommandError("invalid_transition")
        if request.combat_attempt_id != start.combat_attempt_id:
            raise ProgressionCommandError("quiz_context_mismatch")
        for event in events:
            if isinstance(event, SecondWindCombatOutcomeEvent):
                if event.idempotency_id == request.request_id:
                    if (
                        event.second_wind_id != request.second_wind_id
                        or event.combat_attempt_id != request.combat_attempt_id
                        or event.won != request.combat_won
                    ):
                        raise ProgressionCommandError("idempotency_conflict")
                    return self._revived_result(event)
                if event.second_wind_id == request.second_wind_id:
                    raise ProgressionCommandError("target_already_completed")
        event = SecondWindCombatOutcomeEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id=f"second-wind-combat-{request.request_id}",
            idempotency_id=request.request_id,
            ordinal=len(self._events(request.profile_id)) + 1,
            profile_id=request.profile_id,
            session_id=request.session_id,
            world_id=start.world_id,
            battle_id=start.battle_id,
            occurred_at=self._timestamp(),
            won=request.combat_won,
            is_lead_in=True,
            second_wind_id=start.second_wind_id,
            combat_attempt_id=start.combat_attempt_id,
            batch_id=completion.batch_id,
            quiz_final_correct=completion.final_correct,
            quiz_item_count=completion.item_count,
        )
        self._append(event)
        return self._revived_result(event)

    def activate_world(self, request: WorldActivationRequest) -> WorldActivationResult:
        self._authenticate(request.profile_id, request.session_id)
        events = self._events(request.profile_id)
        for event in events:
            if isinstance(event, WorldActivatedEvent):
                if event.idempotency_id == request.request_id:
                    if (
                        not isinstance(event, WorldProgressionActivatedEvent)
                        or event.world_id != request.next_world_id
                        or event.completed_world_id
                        != request.completed_world_id
                    ):
                        raise ProgressionCommandError("idempotency_conflict")
                    world = self._world(request.next_world_id)
                    return WorldActivationResult(
                        request.request_id,
                        request.completed_world_id,
                        event.world_id,
                        world.sequence,
                    )
                if event.world_id == request.next_world_id:
                    raise ProgressionCommandError("target_already_completed")
        state = self._state(request.profile_id)
        if state.active_world_id != request.completed_world_id:
            raise ProgressionCommandError("catalog_conflict")
        if not evaluate_world_clear(state, request.completed_world_id).cleared:
            raise ProgressionCommandError("invalid_transition")
        completed = self._world(request.completed_world_id)
        if completed.sequence >= len(self._catalog.worlds):
            raise ProgressionCommandError("target_already_completed")
        next_world = self._catalog.worlds[completed.sequence]
        if next_world.world_id != request.next_world_id:
            raise ProgressionCommandError("catalog_conflict")
        event = WorldProgressionActivatedEvent(
            schema_version=OUTCOME_EVENT_SCHEMA_VERSION,
            event_id=f"world-activation-{request.request_id}",
            idempotency_id=request.request_id,
            ordinal=len(events) + 1,
            profile_id=request.profile_id,
            session_id=request.session_id,
            world_id=next_world.world_id,
            battle_id="campaign-map",
            occurred_at=self._timestamp(),
            core_subskill_ids=next_world.core_subskill_ids,
            curriculum_receipt=self._catalog.curriculum_receipt,
            completed_world_id=request.completed_world_id,
        )
        self._append(event)
        return WorldActivationResult(
            request.request_id,
            request.completed_world_id,
            next_world.world_id,
            next_world.sequence,
        )

    def _authenticate(self, profile_id: str, session_id: str) -> None:
        try:
            self._profiles.load_profile(profile_id)
            session = self._profiles.load_session(session_id)
            current = self._profiles.load_open_session(profile_id)
        except (ProfileNotFoundError, SessionNotFoundError, ValueError):
            raise ProgressionCommandError("session_not_current") from None
        except LegacyOutcomeProfileError:
            raise ProgressionCommandError("legacy_profile_blocked") from None
        except ProfileStoreError:
            raise ProgressionCommandError("integrity_failure") from None
        if (
            session.profile_id != profile_id
            or session.closed_at is not None
            or current is None
            or current.session_id != session_id
        ):
            raise ProgressionCommandError("session_not_current")

    def _events(self, profile_id: str) -> tuple[object, ...]:
        try:
            return self._profiles.load_events(profile_id)
        except ProfileStoreError:
            raise ProgressionCommandError("integrity_failure") from None

    def _state(self, profile_id: str):
        try:
            self._quizzes.drain_observations(
                profile_id,
                profile_store=self._profiles,
            )
            return self._profiles.load_state(profile_id)
        except LegacyOutcomeProfileError:
            raise ProgressionCommandError("legacy_profile_blocked") from None
        except ProfileStoreError:
            raise ProgressionCommandError("integrity_failure") from None
        except Exception:
            raise ProgressionCommandError("integrity_failure") from None

    def _append(self, event: object) -> object:
        try:
            return self._profiles.append_progression_event(event)  # type: ignore[arg-type]
        except (
            CampaignStateConflictError,
            ProfileNotFoundError,
            SessionNotFoundError,
        ):
            raise ProgressionCommandError("session_not_current") from None
        except IdempotencyConflictError:
            raise ProgressionCommandError("idempotency_conflict") from None
        except SemanticEventConflictError:
            raise ProgressionCommandError("target_already_completed") from None
        except LegacyOutcomeProfileError:
            raise ProgressionCommandError("legacy_profile_blocked") from None
        except ProfileStoreError as error:
            if isinstance(error.__cause__, sqlite3.OperationalError):
                raise ProgressionCommandError("storage_busy") from None
            raise ProgressionCommandError("integrity_failure") from None

    @staticmethod
    def _raise_assisted_preparation_error(error: Exception) -> None:
        code = getattr(error, "code", None)
        mapping = {
            "session_not_current": "session_not_current",
            "idempotency_conflict": "idempotency_conflict",
            "quiz_in_progress": "target_in_progress",
            "catalog_conflict": "invalid_transition",
            "safe_content_unavailable": "safe_content_unavailable",
            "target_in_progress": "target_in_progress",
            "storage_busy": "storage_busy",
            "integrity_failure": "integrity_failure",
        }
        raise ProgressionCommandError(
            mapping.get(code, "integrity_failure")
        ) from None

    @staticmethod
    def _raise_assisted_store_error(error: AssistedRouteStoreError) -> None:
        mapping = {
            "profile_not_found": "quiz_context_mismatch",
            "route_not_found": "quiz_context_mismatch",
            "storage_busy": "storage_busy",
            "idempotency_conflict": "idempotency_conflict",
            "stale_event_head": "target_in_progress",
            "activity_in_progress": "target_in_progress",
            "integrity_failure": "integrity_failure",
        }
        raise ProgressionCommandError(
            mapping.get(error.code, "integrity_failure")
        ) from None

    def _world(self, world_id: str) -> CampaignWorld:
        for world in self._catalog.worlds:
            if world.world_id == world_id:
                return world
        raise ProgressionCommandError("catalog_conflict")

    def _current_battle(
        self,
        state: object,
        world_id: str,
        battle_id: str,
    ) -> tuple[CampaignWorld, CampaignBattle]:
        if state.active_world_id != world_id:  # type: ignore[attr-defined]
            raise ProgressionCommandError("catalog_conflict")
        world = self._world(world_id)
        wins = state.world(world_id).lead_in_battle_wins  # type: ignore[attr-defined]
        expected_sequence = len(wins) + 1
        if not 1 <= expected_sequence <= len(world.battles):
            raise ProgressionCommandError("target_already_completed")
        battle = world.battles[expected_sequence - 1]
        if battle.battle_id != battle_id:
            if any(
                isinstance(event, (BattleCompletionEvent, BossCompletionEvent))
                and event.world_id == world_id
                and event.battle_id == battle_id
                for event in state.events  # type: ignore[attr-defined]
            ):
                raise ProgressionCommandError("target_already_completed")
            raise ProgressionCommandError("catalog_conflict")
        if battle.is_boss and not evaluate_boss_gate(state, world_id).unlocked:
            raise ProgressionCommandError("boss_gate_locked")
        return world, battle

    def _finalized_quiz(
        self,
        profile_id: str,
        batch_id: str,
        *,
        session_id: str,
        world_id: str,
        battle_id: str,
        battle_tier: str,
        item_count: int,
    ) -> tuple[object, object, object]:
        try:
            self._quizzes.drain_observations(
                profile_id,
                profile_store=self._profiles,
            )
            machine = self._quizzes.load(batch_id, profile_id=profile_id)
            material = self._quizzes.load_batch_material(
                batch_id,
                profile_id=profile_id,
            )
            final = machine.final_result
            context = material.context
        except Exception:
            raise ProgressionCommandError("quiz_not_revealed") from None
        if machine.state not in {QuizState.REVEALED, QuizState.CLOSED} or final is None:
            raise ProgressionCommandError("quiz_not_revealed")
        if (
            machine.batch_id != batch_id
            or final.batch_id != batch_id
            or context.profile_id != profile_id
            or context.session_id != session_id
            or context.world_id != world_id
            or context.battle_id != battle_id
            or context.battle_tier != battle_tier
            or final.item_count != item_count
            or len(final.items) != item_count
            or not isinstance(final.final_correct_count, int)
            or isinstance(final.final_correct_count, bool)
            or not 0 <= final.final_correct_count <= item_count
        ):
            raise ProgressionCommandError("quiz_context_mismatch")
        return machine, material, final

    def _close_consumed_quiz(self, profile_id: str, batch_id: str) -> None:
        try:
            machine = self._quizzes.close_revealed(
                batch_id,
                profile_id=profile_id,
            )
        except sqlite3.OperationalError:
            raise ProgressionCommandError("storage_busy") from None
        except Exception:
            raise ProgressionCommandError("integrity_failure") from None
        if getattr(machine, "state", None) is not QuizState.CLOSED:
            raise ProgressionCommandError("integrity_failure")

    def _battle_replay(
        self,
        events: tuple[object, ...],
        request: BattleCompletionRequest,
    ) -> BattleCompletionResult | None:
        for event in events:
            if isinstance(event, (BattleCompletionEvent, BossCompletionEvent)):
                if event.idempotency_id == request.request_id:
                    if (
                        event.profile_id != request.profile_id
                        or event.session_id != request.session_id
                        or event.world_id != request.world_id
                        or event.battle_id != request.battle_id
                        or event.batch_id != request.batch_id
                        or not request.combat_won
                    ):
                        raise ProgressionCommandError("idempotency_conflict")
                    replay_state = reduce_events(
                        item
                        for item in events
                        if item.ordinal <= event.ordinal
                    )
                    clear = evaluate_world_clear(replay_state, request.world_id)
                    return BattleCompletionResult(
                        request.request_id,
                        request.world_id,
                        request.battle_id,
                        request.batch_id,
                        event.final_correct,
                        event.item_count,
                        isinstance(event, BossCompletionEvent),
                        clear.cleared,
                        clear.seal_trial_required,
                    )
                if (
                    event.world_id == request.world_id
                    and event.battle_id == request.battle_id
                ):
                    raise ProgressionCommandError("target_already_completed")
        return None

    def _seal_replay(
        self,
        events: tuple[object, ...],
        request: SealTrialCompletionRequest,
    ) -> SealTrialCompletionResult | None:
        for event in events:
            if isinstance(event, SealTrialCompletionEvent):
                if event.idempotency_id == request.request_id:
                    if (
                        event.profile_id != request.profile_id
                        or event.session_id != request.session_id
                        or event.world_id != request.world_id
                        or event.batch_id != request.batch_id
                    ):
                        raise ProgressionCommandError("idempotency_conflict")
                    gate_state = reduce_events(
                        item
                        for item in events
                        if item.ordinal < event.ordinal
                    )
                    if event.gate_recheck_sha256 != self._gate_recheck_sha256(
                        gate_state,
                        request.world_id,
                    ):
                        raise ProgressionCommandError("integrity_failure")
                    replay_state = reduce_events(
                        item
                        for item in events
                        if item.ordinal <= event.ordinal
                    )
                    clear = evaluate_world_clear(replay_state, request.world_id)
                    return SealTrialCompletionResult(
                        request.request_id,
                        request.world_id,
                        event.attempt_number,
                        request.batch_id,
                        event.final_correct,
                        event.item_count,
                        event.passed,
                        clear.cleared,
                        clear.assisted_route_unlocked,
                    )
        return None

    def _assisted_replay(
        self,
        events: tuple[object, ...],
        request: AssistedRouteCompletionRequest,
    ) -> AssistedRouteCompletionResult | None:
        selected_item_ids = tuple(
            selection.item_id for selection in request.selections
        )
        selected_option_ids = tuple(
            selection.option_id for selection in request.selections
        )
        confidences = tuple(
            selection.confidence.value for selection in request.selections
        )
        for existing in events:
            if not isinstance(existing, AssistedRouteCompletionEvent):
                continue
            if existing.idempotency_id == request.request_id:
                if (
                    existing.profile_id != request.profile_id
                    or existing.world_id != request.world_id
                    or existing.route_id != request.route_id
                    or existing.supported_item_ids != selected_item_ids
                    or existing.selected_option_ids != selected_option_ids
                    or existing.confidences != confidences
                ):
                    raise ProgressionCommandError("idempotency_conflict")
                return self._assisted_result(existing)
            if existing.world_id == request.world_id:
                raise ProgressionCommandError("target_already_completed")
        return None

    @staticmethod
    def _assisted_result(
        event: AssistedRouteCompletionEvent,
    ) -> AssistedRouteCompletionResult:
        return AssistedRouteCompletionResult(
            event.idempotency_id,
            event.world_id,
            event.route_id,
            1,
            event.item_count,
            event.final_correct,
            True,
            tuple(
                AssistedItemResult(
                    itemId=item_id,
                    selectedOptionId=selected_option_id,
                    selectedAnswer=selected_answer,
                    confidence=confidence,
                    correctOptionId=correct_option_id,
                    correctAnswer=correct_answer,
                    isCorrect=is_correct,
                    possibleError=possible_error,
                    reliableMethod=reliable_method,
                    trustedSteps=trusted_steps,
                    canonicalFeedback=canonical_feedback,
                )
                for (
                    item_id,
                    selected_option_id,
                    selected_answer,
                    confidence,
                    correct_option_id,
                    correct_answer,
                    is_correct,
                    possible_error,
                    reliable_method,
                    trusted_steps,
                    canonical_feedback,
                ) in zip(
                    event.supported_item_ids,
                    event.selected_option_ids,
                    event.selected_answers,
                    event.confidences,
                    event.correct_option_ids,
                    event.correct_answers,
                    event.correctness,
                    event.possible_errors,
                    event.reliable_methods,
                    event.trusted_steps,
                    event.canonical_feedback,
                    strict=True,
                )
            ),
        )

    @staticmethod
    def _second_wind_start(
        events: tuple[object, ...],
        second_wind_id: str,
    ) -> SecondWindStartedEvent:
        matches = tuple(
            event for event in events
            if isinstance(event, SecondWindStartedEvent)
            and event.second_wind_id == second_wind_id
        )
        if len(matches) != 1:
            raise ProgressionCommandError("invalid_transition")
        return matches[0]

    @staticmethod
    def _second_wind_result(
        event: SecondWindQuizCompletionEvent,
    ) -> SecondWindCompletionResult:
        return SecondWindCompletionResult(
            event.idempotency_id,
            event.second_wind_id,
            event.batch_id,
            event.final_correct,
            event.item_count,
            event.revive_health_percent,
            event.shield_percent,
            True,
        )

    @staticmethod
    def _revived_result(
        event: SecondWindCombatOutcomeEvent,
    ) -> RevivedCombatCompletionResult:
        return RevivedCombatCompletionResult(
            event.idempotency_id,
            event.second_wind_id,
            event.combat_attempt_id,
            event.won,
            event.won,
            True,
        )

    def _gate_recheck_sha256(self, state: object, world_id: str) -> str:
        gate = evaluate_boss_gate(state, world_id)
        clear = evaluate_world_clear(state, world_id)
        payload = {
            "bossGate": gate.model_dump(by_alias=True, mode="json"),
            "worldClear": asdict(clear),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=lambda value: asdict(value),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _timestamp(self) -> str:
        value = self._utc_now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ProgressionCommandError("integrity_failure")
        canonical = value.astimezone(timezone.utc)
        if canonical.microsecond:
            return canonical.isoformat(timespec="microseconds").replace("+00:00", "Z")
        return canonical.isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "AssistedRouteCompletionRequest",
    "AssistedRouteCompletionResult",
    "AssistedRoutePreparationRequest",
    "AssistedRoutePreparationResult",
    "BattleCompletionRequest",
    "BattleCompletionResult",
    "ProgressionCommandError",
    "ProgressionCommandService",
    "RevivedCombatCompletionRequest",
    "RevivedCombatCompletionResult",
    "SealTrialCompletionRequest",
    "SealTrialCompletionResult",
    "SealTrialPreparationRequest",
    "SealTrialPreparationResult",
    "SecondWindCompletionRequest",
    "SecondWindCompletionResult",
    "SecondWindStartRequest",
    "SecondWindStartResult",
    "WorldActivationRequest",
    "WorldActivationResult",
]
