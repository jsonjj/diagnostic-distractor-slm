"""Bounded, fail-closed preparation of one complete Wayline quiz batch.

The orchestrator is deliberately the only runtime seam that coordinates the
planner, deterministic compiler, local SLM, verifier, reviewed fallback cache,
and atomic quiz store.  It retains all candidate material in memory until a
complete verified batch can be committed in one store transaction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import Final
import uuid

from .adaptive_planner import SlotIntent, plan_slots
from .batch_material import (
    BatchContext,
    BatchMaterialBuilder,
    BatchMaterialError,
    RetryableBatchMaterialRejection,
    VerifiedBatchMaterial,
)
from .contracts import BattleQuizRequest
from .distractor_verifier import DistractorVerifier
from .evidence_reducer import LearnerState
from .providers.distractor import (
    DistractorProvider,
    PinnedSlmManifest,
    ProviderError,
    RawSlmGeneration,
    SlmRequest,
)
from .question_kernel import QuestionCompiler
from .quiz_store import QuizStore, StoredPreparation
from .reviewed_cache import ReviewedCache, ReviewedCacheError
from .slm_prompt import build_slm_request
from .slot_materializer import (
    SlotMaterializationError,
    materialize_live_candidate,
    materialize_slots,
)
from .verified_question import (
    VerifiedQuestionBundle,
    VerifiedQuestionError,
    mint_item_instance_id,
)


LIVE_WINDOW_SECONDS: Final[float] = 8.0
PREPARATION_WINDOW_SECONDS: Final[float] = 10.0
_CANCELLATION_ACK_TURNS: Final[int] = 3
_LATE_RESULT: Final[object] = object()
_HARD_PROVIDER_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        "managed_worker_required",
        "manifest_worker_mismatch",
        "worker_quarantined",
        "worker_unsafe_state",
    }
)


def _mint_batch_id() -> str:
    return "batch_" + uuid.uuid4().hex


class BatchPreparationError(RuntimeError):
    """Stable, non-sensitive failure at the batch-preparation boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "slot_materialization_failed",
            "batch_material_failed",
            "fallback_cache_error",
            "fallback_unavailable",
            "preparation_deadline_exceeded",
            "cancellation_failed",
            *_HARD_PROVIDER_FAILURE_CODES,
        }
    )

    def __init__(self, code: str):
        if code not in self._CODES:
            raise ValueError("unknown batch preparation error code")
        self.code = code
        super().__init__(code)


class BatchPreparationOrchestrator:
    """Prepare and atomically persist one complete verified quiz batch."""

    def __init__(
        self,
        *,
        store: QuizStore,
        compiler: QuestionCompiler,
        verifier: DistractorVerifier,
        manifest: PinnedSlmManifest,
        provider: DistractorProvider,
        reviewed_cache: ReviewedCache,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        batch_id_factory: Callable[[], str] = _mint_batch_id,
        item_id_factory: Callable[[], str] = mint_item_instance_id,
    ) -> None:
        # Dependency validation is intentionally deferred until after the exact
        # store replay check.  A restart replay must not touch planner, model,
        # cache, clock, or ID-generation dependencies.
        self._store = store
        self._compiler = compiler
        self._verifier = verifier
        self._manifest = manifest
        self._provider = provider
        self._reviewed_cache = reviewed_cache
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._batch_id_factory = batch_id_factory
        self._item_id_factory = item_id_factory
        self._retained_tasks: set[asyncio.Task[object]] = set()

    async def prepare(
        self,
        request: BattleQuizRequest,
        *,
        profile_id: str,
        learner_state: LearnerState,
        content_version_id: str,
        batch_seed: int,
    ) -> StoredPreparation:
        """Prepare a complete batch, or fail without writing any quiz rows."""

        replay = self._store.load_preparation(request, profile_id=profile_id)
        if replay is not None:
            return replay

        started_at = self._monotonic()
        live_deadline = started_at + LIVE_WINDOW_SECONDS
        preparation_deadline = started_at + PREPARATION_WINDOW_SECONDS

        intents = plan_slots(learner_state, request.battle_tier)
        try:
            planned_slots = materialize_slots(
                intents,
                request.battle_tier,
                batch_seed,
                self._compiler,
            )
        except SlotMaterializationError:
            raise BatchPreparationError("slot_materialization_failed") from None

        active_world_id = learner_state.active_world_id
        if active_world_id is None:
            # ``plan_slots`` normally rejects this first.  Keep this guard free
            # of invented recovery behavior if a custom planner is injected.
            raise ValueError("learner state has no active world")
        core_subskill_ids = learner_state.world(
            active_world_id
        ).core_subskill_ids
        context = BatchContext(
            profile_id=profile_id,
            session_id=request.session_id,
            world_id=request.world_id,
            battle_id=request.battle_id,
            core_subskill_ids=core_subskill_ids,
            content_version_id=content_version_id,
            battle_tier=request.battle_tier.value,
        )
        try:
            builder = BatchMaterialBuilder(
                batch_id=self._batch_id_factory(),
                context=context,
                planned_slots=planned_slots,
                item_id_factory=self._item_id_factory,
            )
        except BatchMaterialError:
            raise BatchPreparationError("batch_material_failed") from None

        live_provider_ready = await self._begin_provider_preparation(live_deadline)
        if live_provider_ready:
            await self._fill_live(
                builder,
                batch_seed,
                live_deadline,
                preparation_deadline,
            )

        if builder.next_slot is not None:
            while self._monotonic() < live_deadline:
                await self._sleeper(live_deadline - self._monotonic())
            await self._fill_reviewed_cache(builder, preparation_deadline)

        self._require_before(preparation_deadline)
        try:
            material = builder.finalize()
        except BatchMaterialError:
            raise BatchPreparationError("batch_material_failed") from None

        # This check is immediately adjacent to the only persistent operation.
        # QuizStore.create_prepared owns the single atomic write transaction.
        self._require_before(preparation_deadline)
        return self._store.create_prepared(
            material,
            request=request,
            profile_id=profile_id,
        )

    async def build_verified_material(
        self,
        *,
        context: BatchContext,
        intents: tuple[SlotIntent, ...],
        batch_seed: int,
        batch_id: str,
    ) -> VerifiedBatchMaterial:
        """Build one complete verified material entirely in memory."""

        started_at = self._monotonic()
        live_deadline = started_at + LIVE_WINDOW_SECONDS
        preparation_deadline = started_at + PREPARATION_WINDOW_SECONDS
        try:
            planned_slots = materialize_slots(
                intents,
                context.battle_tier,
                batch_seed,
                self._compiler,
            )
        except SlotMaterializationError:
            raise BatchPreparationError("slot_materialization_failed") from None
        try:
            builder = BatchMaterialBuilder(
                batch_id=batch_id,
                context=context,
                planned_slots=planned_slots,
                item_id_factory=self._item_id_factory,
            )
        except BatchMaterialError:
            raise BatchPreparationError("batch_material_failed") from None

        if await self._begin_provider_preparation(live_deadline):
            await self._fill_live(
                builder,
                batch_seed,
                live_deadline,
                preparation_deadline,
            )
        if builder.next_slot is not None:
            while self._monotonic() < live_deadline:
                await self._sleeper(live_deadline - self._monotonic())
            await self._fill_reviewed_cache(builder, preparation_deadline)
        self._require_before(preparation_deadline)
        try:
            return builder.finalize()
        except BatchMaterialError:
            raise BatchPreparationError("batch_material_failed") from None

    async def _fill_live(
        self,
        builder: BatchMaterialBuilder,
        batch_seed: int,
        live_deadline: float,
        preparation_deadline: float,
    ) -> None:
        while builder.next_slot is not None and self._monotonic() < live_deadline:
            slot = builder.next_slot
            assert slot is not None
            attempted_semantics: tuple[str, ...] = ()
            accepted = False
            for live_attempt in (1, 2):
                if self._monotonic() >= live_deadline:
                    break
                try:
                    candidate = materialize_live_candidate(
                        slot,
                        batch_seed=batch_seed,
                        live_attempt=live_attempt,
                        compiler=self._compiler,
                        exclusions=builder.selection_exclusions,
                        attempted_semantic_sha256s=attempted_semantics,
                    )
                except SlotMaterializationError:
                    # Attempt two cannot be authenticated without attempt one's
                    # semantic receipt, so an attempt-one materialization miss
                    # ends this slot's live path.
                    if live_attempt == 1:
                        break
                    continue
                attempted_semantics = (
                    *attempted_semantics,
                    candidate.question_semantic_sha256,
                )
                slm_request = build_slm_request(candidate.blueprint)
                hard_provider_failure: str | None = None
                try:
                    generation = await self._generate_before(
                        slm_request,
                        live_deadline,
                        preparation_deadline,
                    )
                except ProviderError as error:
                    if error.code in _HARD_PROVIDER_FAILURE_CODES:
                        hard_provider_failure = error.code
                    else:
                        continue
                if hard_provider_failure is not None:
                    raise BatchPreparationError(hard_provider_failure) from None
                if generation is _LATE_RESULT or self._monotonic() >= live_deadline:
                    break
                assert isinstance(generation, RawSlmGeneration)

                verification = self._verifier.verify_generation(
                    candidate.blueprint,
                    generation,
                )
                if not verification.accepted or verification.value is None:
                    continue
                if self._monotonic() >= live_deadline:
                    break
                try:
                    bundle = VerifiedQuestionBundle.from_verified(
                        compiler=self._compiler,
                        request=candidate.request,
                        blueprint=candidate.blueprint,
                        verified=verification.value,
                        generation=generation,
                        manifest=self._manifest,
                    )
                except VerifiedQuestionError:
                    continue
                if self._monotonic() >= live_deadline:
                    break
                try:
                    builder.accept_live(bundle)
                except RetryableBatchMaterialRejection:
                    continue
                except BatchMaterialError:
                    raise BatchPreparationError("batch_material_failed") from None
                accepted = True
                break

            if not accepted:
                # Slots are bound sequentially because actual accepted content
                # determines the next slot's exclusions.  Leave this slot for
                # the reviewed cache at the fixed fallback boundary.
                return

    async def _fill_reviewed_cache(
        self,
        builder: BatchMaterialBuilder,
        preparation_deadline: float,
    ) -> None:
        while builder.next_slot is not None:
            self._require_before(preparation_deadline)
            try:
                key = builder.next_fallback_cache_key()
                self._require_before(preparation_deadline)
                hit = self._reviewed_cache.lookup_reviewed(key)
            except ReviewedCacheError:
                raise BatchPreparationError("fallback_cache_error") from None
            except BatchMaterialError:
                raise BatchPreparationError("batch_material_failed") from None

            self._require_before(preparation_deadline)
            if hit is None:
                raise BatchPreparationError("fallback_unavailable")
            try:
                builder.accept_reviewed_hit(hit)
            except BatchMaterialError:
                raise BatchPreparationError("fallback_unavailable") from None

    async def _begin_provider_preparation(self, deadline: float) -> bool:
        """Start managed providers only at an explicit preparation boundary."""

        begin_preparation = getattr(self._provider, "begin_preparation", None)
        if not callable(begin_preparation):
            return True
        if self._monotonic() >= deadline:
            return False
        hard_provider_failure: str | None = None
        try:
            await begin_preparation(deadline=deadline)
        except ProviderError as error:
            if error.code in _HARD_PROVIDER_FAILURE_CODES:
                hard_provider_failure = error.code
            else:
                return False
        if hard_provider_failure is not None:
            raise BatchPreparationError(hard_provider_failure) from None
        return self._monotonic() < deadline

    async def _generate_before(
        self,
        request: SlmRequest,
        deadline: float,
        cancellation_deadline: float,
    ) -> RawSlmGeneration | object:
        if self._monotonic() >= deadline:
            return _LATE_RESULT

        async def invoke_provider() -> RawSlmGeneration | object:
            # The task may not be scheduled until after the caller's check.
            # Recheck inside the task so a provider call cannot *start* at the
            # boundary merely because the event loop was busy.
            if self._monotonic() >= deadline:
                return _LATE_RESULT
            generate_before = getattr(self._provider, "generate_before", None)
            if callable(generate_before):
                return await generate_before(
                    request,
                    ready_deadline=deadline,
                    cancellation_deadline=cancellation_deadline,
                )
            return await self._provider.generate(request)

        provider_task = asyncio.create_task(invoke_provider())
        deadline_task = asyncio.create_task(
            self._sleeper(max(0.0, deadline - self._monotonic()))
        )
        try:
            done, _pending = await asyncio.wait(
                {provider_task, deadline_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            await self._cancel_and_ack(provider_task, deadline_task)
            raise

        if provider_task in done and self._monotonic() < deadline:
            if not await self._cancel_and_ack(deadline_task):
                raise BatchPreparationError("cancellation_failed")
            return await provider_task

        if not await self._cancel_and_ack(
            provider_task,
            deadline_task,
            deadline=cancellation_deadline,
            require_cancelled=(provider_task,),
        ):
            raise BatchPreparationError("cancellation_failed")
        return _LATE_RESULT

    async def _cancel_and_ack(
        self,
        *tasks: asyncio.Task[object],
        deadline: float | None = None,
        require_cancelled: tuple[asyncio.Task[object], ...] = (),
    ) -> bool:
        already_done = frozenset(task for task in tasks if task.done())
        for task in tasks:
            if not task.done():
                task.cancel()
                self._retain_task(task)
        for _turn in range(_CANCELLATION_ACK_TURNS):
            if all(task.done() for task in tasks):
                break
            await asyncio.sleep(0)
        for task in tasks:
            if task.done():
                self._consume_task_result(task)
        if all(task.done() for task in tasks):
            return self._tasks_acknowledged(
                tasks,
                require_cancelled=require_cancelled,
                already_done=already_done,
            )
        if deadline is None:
            return False

        while self._monotonic() < deadline:
            timeout_task = asyncio.create_task(
                self._sleeper(max(0.0, deadline - self._monotonic()))
            )
            pending_tasks = {task for task in tasks if not task.done()}
            try:
                await asyncio.wait(
                    {*pending_tasks, timeout_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except BaseException:
                await self._cancel_and_ack(timeout_task)
                raise

            if all(task.done() for task in tasks):
                if not await self._cancel_and_ack(timeout_task):
                    return False
                for task in tasks:
                    self._consume_task_result(task)
                return self._tasks_acknowledged(
                    tasks,
                    require_cancelled=require_cancelled,
                    already_done=already_done,
                )

            if timeout_task.done():
                self._consume_task_result(timeout_task)
                if self._monotonic() >= deadline:
                    return False
                continue

            if not await self._cancel_and_ack(timeout_task):
                return False
        return False

    @staticmethod
    def _tasks_acknowledged(
        tasks: tuple[asyncio.Task[object], ...],
        *,
        require_cancelled: tuple[asyncio.Task[object], ...],
        already_done: frozenset[asyncio.Task[object]],
    ) -> bool:
        if not all(task.done() for task in tasks):
            return False
        return all(
            task in already_done or task.cancelled()
            for task in require_cancelled
        )

    def _retain_task(self, task: asyncio.Task[object]) -> None:
        self._retained_tasks.add(task)
        task.add_done_callback(self._retained_task_finished)

    def _retained_task_finished(self, task: asyncio.Task[object]) -> None:
        self._retained_tasks.discard(task)
        self._consume_task_result(task)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[object]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    def _require_before(self, deadline: float) -> None:
        if self._monotonic() >= deadline:
            raise BatchPreparationError("preparation_deadline_exceeded")


__all__ = [
    "BatchPreparationError",
    "BatchPreparationOrchestrator",
    "LIVE_WINDOW_SECONDS",
    "PREPARATION_WINDOW_SECONDS",
]
