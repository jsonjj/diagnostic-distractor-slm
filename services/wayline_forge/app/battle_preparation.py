"""Authenticated application service for one authored battle quiz."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Final

from services.wayline_forge.app.adaptive_planner import plan_assisted_slots
from services.wayline_forge.app.assisted_route_store import (
    AssistedRouteStore,
    AssistedRouteStoreError,
)
from services.wayline_forge.app.batch_material import BatchContext
from services.wayline_forge.app.boss_gate import (
    evaluate_boss_gate,
    evaluate_world_clear,
)
from services.wayline_forge.app.campaign_catalog import (
    CampaignCatalog,
    CampaignCatalogError,
    CampaignWorld,
)
from services.wayline_forge.app.contracts import (
    AssistedRouteBatch,
    BattleQuizRequest,
    PublicQuizBatch,
)
from services.wayline_forge.app.curriculum import (
    CURRICULUM_V1_SHA256,
    Curriculum,
    CurriculumError,
)
from services.wayline_forge.app.evidence_reducer import (
    EvidenceReplayError,
    LearnerState,
)
from services.wayline_forge.app.events import (
    BossOutcomeEvent,
    ObservationEvent,
    SecondWindCombatOutcomeEvent,
    SecondWindStartedEvent,
    WorldActivatedEvent,
)
from services.wayline_forge.app.orchestrator import (
    BatchPreparationError as OrchestratorPreparationError,
    BatchPreparationOrchestrator,
)
from services.wayline_forge.app.profile_store import (
    EventLogCorruptionError,
    IdentityStoreCorruptionError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    SessionNotFoundError,
)
from services.wayline_forge.app.quiz_machine import IdempotencyConflictError
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
    QuizStoreError,
    QuizTransitionConflictError,
)


class BattlePreparationError(RuntimeError):
    """Stable, non-sensitive failure at the battle-preparation boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "session_not_current",
            "idempotency_conflict",
            "quiz_in_progress",
            "catalog_conflict",
            "boss_gate_locked",
            "evidence_sync_unavailable",
            "safe_content_unavailable",
            "storage_busy",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown battle preparation error code")
        self.code = code
        super().__init__(code)


class BattlePreparationService:
    """Authorize, derive, and prepare one server-authored campaign battle."""

    def __init__(
        self,
        profile_store: ProfileStore,
        quiz_store: QuizStore,
        orchestrator: BatchPreparationOrchestrator,
        *,
        assisted_route_store: AssistedRouteStore | None = None,
    ) -> None:
        self._profile_store = profile_store
        self._quiz_store = quiz_store
        self._orchestrator = orchestrator
        self._assisted_routes = assisted_route_store
        try:
            self._catalog = CampaignCatalog.packaged_v1()
            self._curriculum = Curriculum.packaged_v1()
        except (CampaignCatalogError, CurriculumError):
            raise BattlePreparationError("integrity_failure") from None

    async def prepare(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch:
        """Return one public batch or a stable non-sensitive typed failure."""

        try:
            return await self._prepare(
                request,
                profile_id=profile_id,
                current_session_id=current_session_id,
            )
        except BattlePreparationError:
            raise
        except Exception:
            raise BattlePreparationError("integrity_failure") from None

    async def prepare_seal_trial(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch:
        """Prepare only the next server-authorized three-item Seal Trial."""

        try:
            return await self._prepare_special(
                request,
                profile_id=profile_id,
                current_session_id=current_session_id,
                kind="seal_trial",
            )
        except BattlePreparationError:
            raise
        except Exception:
            raise BattlePreparationError("integrity_failure") from None

    async def prepare_second_wind(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch:
        """Prepare only a durably started, still-resumable Second Wind batch."""

        try:
            return await self._prepare_special(
                request,
                profile_id=profile_id,
                current_session_id=current_session_id,
                kind="second_wind",
            )
        except BattlePreparationError:
            raise
        except Exception:
            raise BattlePreparationError("integrity_failure") from None

    async def prepare_assisted_route(
        self,
        *,
        request_id: str,
        profile_id: str,
        current_session_id: str,
        world_id: str,
    ) -> AssistedRouteBatch:
        """Prepare or recover one fresh verifier-sealed assisted route."""

        try:
            return await self._prepare_assisted_route(
                request_id=request_id,
                profile_id=profile_id,
                current_session_id=current_session_id,
                world_id=world_id,
            )
        except BattlePreparationError:
            raise
        except Exception:
            raise BattlePreparationError("integrity_failure") from None

    async def _prepare_assisted_route(
        self,
        *,
        request_id: str,
        profile_id: str,
        current_session_id: str,
        world_id: str,
    ) -> AssistedRouteBatch:
        route_store = self._assisted_routes
        if route_store is None:
            raise BattlePreparationError("integrity_failure")
        self._authenticate_current(profile_id, current_session_id)
        payload = {
            "profileId": profile_id,
            "requestId": request_id,
            "schemaVersion": "wayline.v1",
            "sessionId": current_session_id,
            "worldId": world_id,
        }
        payload_sha256 = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        try:
            replay = route_store.load_preparation(
                request_id,
                profile_id=profile_id,
                payload_sha256=payload_sha256,
            )
        except AssistedRouteStoreError as error:
            self._raise_assisted_store_error(error)
        if replay is not None:
            return replay.batch

        try:
            self._quiz_store.drain_observations(
                profile_id,
                profile_store=self._profile_store,
            )
            state = self._profile_store.load_state(profile_id)
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except (QuizStoreError, ProfileStoreError):
            raise BattlePreparationError("integrity_failure") from None
        active_world, _sequence = self._validated_active_world(state, profile_id)
        clear = evaluate_world_clear(state, world_id)
        if (
            active_world.world_id != world_id
            or clear.cleared
            or not clear.assisted_route_unlocked
            or clear.assisted_route_plan is None
        ):
            raise BattlePreparationError("catalog_conflict")

        event_head = self._profile_store.event_head(profile_id)
        route_plan_payload = asdict(clear.assisted_route_plan)
        route_plan_sha256 = hashlib.sha256(
            json.dumps(
                route_plan_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        try:
            active = route_store.load_active(profile_id)
            if active is not None:
                aliased = route_store.create_prepared(
                    route_id=active.route_id,
                    profile_id=profile_id,
                    source_session_id=active.source_session_id,
                    world_id=world_id,
                    preparation_request_id=request_id,
                    preparation_payload_sha256=payload_sha256,
                    event_head_ordinal=event_head[0],
                    event_head_hash=event_head[1],
                    route_plan_sha256=route_plan_sha256,
                    material=active.material,
                )
                return aliased.batch
        except AssistedRouteStoreError as error:
            self._raise_assisted_store_error(error)

        identity = hashlib.sha256(
            json.dumps(
                {
                    "campaignCatalogReceipt": self._catalog.curriculum_receipt,
                    "curriculumReceipt": CURRICULUM_V1_SHA256,
                    "eventHeadHash": event_head[1],
                    "profileId": profile_id,
                    "requestPayloadSha256": payload_sha256,
                    "routePlanSha256": route_plan_sha256,
                    "worldId": world_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        batch_seed = int(identity[:16], 16) & (2**63 - 1)
        intents = plan_assisted_slots(state, clear.assisted_route_plan)
        context = BatchContext(
            profile_id=profile_id,
            session_id=current_session_id,
            world_id=world_id,
            battle_id=f"{world_id}_assisted_route",
            core_subskill_ids=state.world(world_id).core_subskill_ids,
            content_version_id=self._curriculum.curriculum_id,
            battle_tier="assisted_route",
        )
        try:
            material = await self._orchestrator.build_verified_material(
                context=context,
                intents=intents,
                batch_seed=batch_seed,
                batch_id=f"batch_assisted_{identity[:24]}",
            )
        except OrchestratorPreparationError:
            raise BattlePreparationError("safe_content_unavailable") from None

        self._authenticate_current(profile_id, current_session_id)
        try:
            self._quiz_store.drain_observations(
                profile_id,
                profile_store=self._profile_store,
            )
            current_state = self._profile_store.load_state(profile_id)
            current_head = self._profile_store.event_head(profile_id)
        except (QuizStoreError, ProfileStoreError):
            raise BattlePreparationError("integrity_failure") from None
        current_clear = evaluate_world_clear(current_state, world_id)
        if (
            current_head != event_head
            or current_clear.cleared
            or current_clear.assisted_route_plan is None
            or hashlib.sha256(
                json.dumps(
                    asdict(current_clear.assisted_route_plan),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            != route_plan_sha256
        ):
            raise BattlePreparationError("quiz_in_progress")
        try:
            stored = route_store.create_prepared(
                route_id=f"assisted-{identity[:24]}",
                profile_id=profile_id,
                source_session_id=current_session_id,
                world_id=world_id,
                preparation_request_id=request_id,
                preparation_payload_sha256=payload_sha256,
                event_head_ordinal=event_head[0],
                event_head_hash=event_head[1],
                route_plan_sha256=route_plan_sha256,
                material=material,
            )
        except AssistedRouteStoreError as error:
            self._raise_assisted_store_error(error)
        return stored.batch

    @staticmethod
    def _raise_assisted_store_error(error: AssistedRouteStoreError) -> None:
        mapping = {
            "activity_in_progress": "quiz_in_progress",
            "idempotency_conflict": "idempotency_conflict",
            "profile_not_found": "session_not_current",
            "stale_event_head": "quiz_in_progress",
            "storage_busy": "storage_busy",
        }
        raise BattlePreparationError(
            mapping.get(error.code, "integrity_failure")
        ) from None

    async def _prepare_special(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
        kind: str,
    ) -> PublicQuizBatch:
        if kind not in {"seal_trial", "second_wind"}:
            raise BattlePreparationError("integrity_failure")
        self._authenticate(request, profile_id, current_session_id)
        if request.battle_tier.value != "seal_trial":
            raise BattlePreparationError("catalog_conflict")

        try:
            replay = self._quiz_store.load_preparation(
                request,
                profile_id=profile_id,
            )
        except IdempotencyConflictError:
            raise BattlePreparationError("idempotency_conflict") from None
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except QuizStoreError:
            raise BattlePreparationError("integrity_failure") from None
        if replay is not None:
            output = replay.public_output
            if type(output) is not PublicQuizBatch or output.item_count != 3:
                raise BattlePreparationError("integrity_failure")
            return output

        try:
            self._quiz_store.drain_observations(
                profile_id,
                profile_store=self._profile_store,
            )
            state = self._profile_store.load_state(profile_id)
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except (QuizStoreError, ProfileStoreError):
            raise BattlePreparationError("integrity_failure") from None
        active_world, _world_sequence = self._validated_active_world(
            state,
            profile_id,
        )
        if request.world_id != active_world.world_id:
            raise BattlePreparationError("catalog_conflict")

        if kind == "seal_trial":
            clear = evaluate_world_clear(state, active_world.world_id)
            if not clear.seal_trial_required:
                raise BattlePreparationError("catalog_conflict")
            expected_attempt = clear.missed_seal_trials + 1
            expected_battle_id = (
                f"{active_world.world_id}_seal_trial_{expected_attempt}"
            )
            if request.battle_id != expected_battle_id:
                raise BattlePreparationError("catalog_conflict")
        else:
            starts = tuple(
                event
                for event in state.events
                if isinstance(event, SecondWindStartedEvent)
                and event.profile_id == profile_id
                and event.session_id == current_session_id
                and event.world_id == active_world.world_id
                and event.preparation_request_id == request.request_id
                and event.quiz_battle_id == request.battle_id
            )
            if len(starts) != 1:
                raise BattlePreparationError("catalog_conflict")
            start = starts[0]
            if any(
                isinstance(event, SecondWindCombatOutcomeEvent)
                and event.second_wind_id == start.second_wind_id
                for event in state.events
            ):
                raise BattlePreparationError("catalog_conflict")

        try:
            resumable_batch_id = self._quiz_store.resumable_batch_id(profile_id)
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except QuizStoreError:
            raise BattlePreparationError("integrity_failure") from None
        if resumable_batch_id is not None:
            raise BattlePreparationError("quiz_in_progress")
        try:
            prepared = await self._orchestrator.prepare(
                request,
                profile_id=profile_id,
                learner_state=state,
                content_version_id=self._curriculum.curriculum_id,
                batch_seed=self._batch_seed(request, profile_id),
            )
        except OrchestratorPreparationError:
            raise BattlePreparationError("safe_content_unavailable") from None
        except IdempotencyConflictError:
            raise BattlePreparationError("idempotency_conflict") from None
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except QuizTransitionConflictError:
            raise BattlePreparationError("quiz_in_progress") from None
        except QuizStoreError:
            raise BattlePreparationError("integrity_failure") from None
        self._authenticate(request, profile_id, current_session_id)
        output = prepared.public_output
        if type(output) is not PublicQuizBatch or output.item_count != 3:
            raise BattlePreparationError("integrity_failure")
        return output

    async def _prepare(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> PublicQuizBatch:
        self._authenticate(request, profile_id, current_session_id)

        try:
            replay = self._quiz_store.load_preparation(request, profile_id=profile_id)
        except IdempotencyConflictError:
            raise BattlePreparationError("idempotency_conflict") from None
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except QuizStoreCorruptionError:
            raise BattlePreparationError("integrity_failure") from None
        except QuizStoreError:
            raise BattlePreparationError("integrity_failure") from None
        if replay is not None:
            try:
                output = replay.public_output
            except (AttributeError, TypeError, ValueError):
                raise BattlePreparationError("integrity_failure") from None
            if type(output) is not PublicQuizBatch:
                raise BattlePreparationError("integrity_failure")
            return output

        try:
            self._quiz_store.drain_observations(
                profile_id,
                profile_store=self._profile_store,
            )
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except QuizStoreCorruptionError:
            raise BattlePreparationError("integrity_failure") from None
        except QuizStoreError:
            raise BattlePreparationError("evidence_sync_unavailable") from None
        except (EventLogCorruptionError, IdentityStoreCorruptionError, EvidenceReplayError):
            raise BattlePreparationError("integrity_failure") from None
        except ProfileStoreError:
            raise BattlePreparationError("evidence_sync_unavailable") from None
        try:
            state = self._profile_store.load_state(profile_id)
        except (EventLogCorruptionError, IdentityStoreCorruptionError, EvidenceReplayError):
            raise BattlePreparationError("integrity_failure") from None
        except ProfileStoreError:
            raise BattlePreparationError("integrity_failure") from None
        active_world, world_sequence = self._validated_active_world(state, profile_id)
        wins = state.world(active_world.world_id).lead_in_battle_wins
        expected_prefix = tuple(
            battle.battle_id for battle in active_world.battles[: len(wins)]
        )
        if len(wins) > 4 or wins != expected_prefix:
            raise BattlePreparationError("catalog_conflict")
        battle_sequence = len(wins) + 1
        try:
            battle = self._catalog.require_battle(
                world_id=request.world_id,
                battle_id=request.battle_id,
                battle_tier=request.battle_tier.value,
                expected_world_sequence=world_sequence,
                expected_battle_sequence=battle_sequence,
            )
        except ValueError:
            raise BattlePreparationError("catalog_conflict") from None
        if any(
            isinstance(event, BossOutcomeEvent)
            and event.world_id == active_world.world_id
            for event in state.events
        ):
            raise BattlePreparationError("catalog_conflict")
        if battle.is_boss and not evaluate_boss_gate(state, active_world.world_id).unlocked:
            raise BattlePreparationError("boss_gate_locked")
        try:
            resumable_batch_id = self._quiz_store.resumable_batch_id(profile_id)
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except QuizStoreCorruptionError:
            raise BattlePreparationError("integrity_failure") from None
        except QuizStoreError:
            raise BattlePreparationError("integrity_failure") from None
        if resumable_batch_id is not None:
            raise BattlePreparationError("quiz_in_progress")

        try:
            prepared = await self._orchestrator.prepare(
                request,
                profile_id=profile_id,
                learner_state=state,
                content_version_id=self._curriculum.curriculum_id,
                batch_seed=self._batch_seed(request, profile_id),
            )
        except OrchestratorPreparationError:
            raise BattlePreparationError("safe_content_unavailable") from None
        except IdempotencyConflictError:
            raise BattlePreparationError("idempotency_conflict") from None
        except QuizStoreBusyError:
            raise BattlePreparationError("storage_busy") from None
        except QuizStoreCorruptionError:
            raise BattlePreparationError("integrity_failure") from None
        except QuizTransitionConflictError:
            raise BattlePreparationError("quiz_in_progress") from None
        except QuizStoreError:
            raise BattlePreparationError("integrity_failure") from None
        except Exception:
            raise BattlePreparationError("integrity_failure") from None
        self._authenticate(request, profile_id, current_session_id)
        try:
            output = prepared.public_output
        except (AttributeError, TypeError, ValueError):
            raise BattlePreparationError("integrity_failure") from None
        if type(output) is not PublicQuizBatch or output.item_count != battle.item_count:
            raise BattlePreparationError("integrity_failure")
        return output

    def _authenticate(
        self,
        request: BattleQuizRequest,
        profile_id: str,
        current_session_id: str,
    ) -> None:
        if request.session_id != current_session_id:
            raise BattlePreparationError("session_not_current")
        self._authenticate_current(profile_id, current_session_id)

    def _authenticate_current(
        self,
        profile_id: str,
        current_session_id: str,
    ) -> None:
        try:
            self._profile_store.load_profile(profile_id)
            session = self._profile_store.load_session(current_session_id)
            current = self._profile_store.load_open_session(profile_id)
        except (ProfileNotFoundError, SessionNotFoundError, ValueError):
            raise BattlePreparationError("session_not_current") from None
        except IdentityStoreCorruptionError:
            raise BattlePreparationError("integrity_failure") from None
        except ProfileStoreError:
            raise BattlePreparationError("integrity_failure") from None
        if (
            session.profile_id != profile_id
            or session.closed_at is not None
            or current is None
            or current.session_id != current_session_id
        ):
            raise BattlePreparationError("session_not_current")

    def _validated_active_world(
        self,
        state: LearnerState,
        profile_id: str,
    ) -> tuple[CampaignWorld, int]:
        if state.profile_id != profile_id:
            raise BattlePreparationError("catalog_conflict")

        active: CampaignWorld | None = None
        activated_world_ids: list[str] = []
        for event in state.events:
            if event.profile_id != profile_id:
                raise BattlePreparationError("catalog_conflict")
            if isinstance(event, WorldActivatedEvent):
                sequence = len(activated_world_ids) + 1
                if sequence > len(self._catalog.worlds):
                    raise BattlePreparationError("catalog_conflict")
                expected = self._catalog.worlds[sequence - 1]
                if (
                    event.world_id != expected.world_id
                    or event.battle_id != "campaign-map"
                    or event.core_subskill_ids != expected.core_subskill_ids
                    or event.curriculum_receipt
                    != self._catalog.curriculum_receipt
                ):
                    raise BattlePreparationError("catalog_conflict")
                active = expected
                activated_world_ids.append(expected.world_id)
                continue

            if active is None:
                raise BattlePreparationError("catalog_conflict")
            if isinstance(event, ObservationEvent):
                if event.world_id != active.world_id and (
                    not event.is_transfer
                    or event.world_id not in activated_world_ids
                ):
                    raise BattlePreparationError("catalog_conflict")
            elif event.world_id != active.world_id:
                raise BattlePreparationError("catalog_conflict")

        if active is None or state.active_world_id != active.world_id:
            raise BattlePreparationError("catalog_conflict")

        for sequence, activated_world_id in enumerate(
            activated_world_ids,
            start=1,
        ):
            expected = self._catalog.worlds[sequence - 1]
            evidence = state.world(activated_world_id)
            if (
                activated_world_id != expected.world_id
                or evidence.activated_ordinal < 1
                or evidence.core_subskill_ids != expected.core_subskill_ids
                or evidence.curriculum_receipt
                != self._catalog.curriculum_receipt
            ):
                raise BattlePreparationError("catalog_conflict")
        return active, active.sequence

    def _batch_seed(self, request: BattleQuizRequest, profile_id: str) -> int:
        identity = {
            "campaignCatalogId": self._catalog.catalog_id,
            "campaignCatalogReceipt": self._catalog.curriculum_receipt,
            "curriculumId": self._curriculum.curriculum_id,
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
        return int.from_bytes(hashlib.sha256(canonical).digest()[:8], "big") & (
            2**63 - 1
        )


__all__ = ["BattlePreparationError", "BattlePreparationService"]
