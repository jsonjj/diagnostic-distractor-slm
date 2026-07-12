from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app import orchestrator as orchestrator_module
from services.wayline_forge.app.adaptive_planner import SlotIntent
from services.wayline_forge.app.batch_material import (
    BatchContext,
    BatchMaterialBuilder,
)
from services.wayline_forge.app.contracts import BattleQuizRequest
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.evidence_reducer import reduce_events
from services.wayline_forge.app.orchestrator import (
    BatchPreparationError,
    BatchPreparationOrchestrator,
)
from services.wayline_forge.app.providers.distractor import ProviderError
from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.quiz_store import QuizStore
from services.wayline_forge.app.reviewed_cache import (
    CacheCorruptionError,
    ReviewReceipt,
    ReviewedCacheHit,
)
from services.wayline_forge.app.slot_materializer import question_semantic_sha256
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle
from services.wayline_forge.tests.fixtures import event


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _blueprint_for_slm_request(verifier, request):
    for family in verifier.compiler.curriculum.families.values():
        prefix = family.family_id.replace("_", "-") + "-"
        if not request.question_id.startswith(prefix):
            continue
        suffix = request.question_id[len(prefix):]
        seed_text, separator, _digest = suffix.partition("-")
        if not separator:
            continue
        try:
            seed = int(seed_text)
        except ValueError:
            continue
        for difficulty in (1, 2, 3):
            compile_request = CompileRequest(
                world_id=family.world_id,
                skill_id=family.skill_id,
                family_id=family.family_id,
                difficulty=difficulty,
                seed=seed,
            )
            blueprint = verifier.compiler.compile(compile_request)
            if blueprint.question_id == request.question_id:
                return compile_request, blueprint
    raise AssertionError("provider could not resolve the compiler blueprint")


def _generation_for(
    verifier,
    blueprint,
    *,
    required_procedure_ids=(),
    accepted=True,
    generated_at_utc="2026-07-11T18:00:00Z",
):
    selected = list(required_procedure_ids)
    selected.extend(
        procedure_id
        for procedure_id in blueprint.allowed_procedure_ids
        if procedure_id not in selected
    )
    selected = selected[:3]
    if len(selected) != 3:
        raise AssertionError("fixture blueprint does not have three routes")
    distractors = [
        {
            "misconception": verifier.registry.canonical_label(procedure_id),
            "computation": verifier.registry.canonical_computation(
                procedure_id,
                blueprint,
            ),
            "answer": verifier.registry.evaluate(procedure_id, blueprint).display,
        }
        for procedure_id in selected
    ]
    text = _canonical_json({"distractors": distractors})
    if not accepted:
        text = _canonical_json({"distractors": []})
    return replace(
        verifier.fixture_generation(blueprint, "accepted.json"),
        text=text,
        generated_at_utc=generated_at_utc,
    )


def _bundle_for(
    verifier,
    compile_request,
    *,
    required_procedure_ids=(),
    generated_at_utc="2026-07-11T19:00:00Z",
):
    blueprint = verifier.compiler.compile(compile_request)
    generation = _generation_for(
        verifier,
        blueprint,
        required_procedure_ids=required_procedure_ids,
        generated_at_utc=generated_at_utc,
    )
    result = verifier.verify_generation(blueprint, generation)
    if not result.accepted or result.value is None:
        raise AssertionError(f"fixture generation was rejected: {result.code}")
    return VerifiedQuestionBundle.from_verified(
        compiler=verifier.compiler,
        request=compile_request,
        blueprint=blueprint,
        verified=result.value,
        generation=generation,
        manifest=verifier.manifest,
    )


class FakeClock:
    def __init__(self, now=0.0):
        self.now = float(now)
        self.sleep_calls = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        # This tiny real yield lets an immediate provider win while keeping all
        # asserted deadlines controlled exclusively by the fake clock.
        await asyncio.sleep(0.001)
        self.now += max(0.0, float(seconds))


class SequenceClock(FakeClock):
    def __init__(self, values):
        super().__init__(values[0])
        self.values = list(values)

    def __call__(self):
        if self.values:
            self.now = float(self.values.pop(0))
        return self.now


class EarlyWakeClock(FakeClock):
    def __init__(self):
        super().__init__()
        self.completed_sleeps = 0

    async def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        await asyncio.sleep(0.001)
        self.completed_sleeps += 1
        if self.completed_sleeps > 1:
            self.now += max(0.0, float(seconds))


class DeterministicIds:
    def __init__(self, namespace="a"):
        self.namespace = namespace
        self.batch_count = 0
        self.item_count = 0

    def batch(self):
        self.batch_count += 1
        return f"batch-{self.namespace}-{self.batch_count:08d}"

    def item(self):
        self.item_count += 1
        return f"item_{self.namespace}{self.item_count:031x}"


class FakeProvider:
    def __init__(self, verifier, clock, outcomes=None):
        self.verifier = verifier
        self.clock = clock
        self.outcomes = list(outcomes or ())
        self.requests = []
        self.start_times = []
        self.active = 0
        self.max_active = 0
        self.cancelled = 0

    async def generate(self, request):
        self.requests.append(request)
        self.start_times.append(self.clock())
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        outcome = self.outcomes.pop(0) if self.outcomes else "valid"
        try:
            if outcome == "late":
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
                outcome = "valid"
            if outcome == "typed_error":
                raise ProviderError("test_provider_unavailable")
            if outcome == "programmer_error":
                raise RuntimeError("programmer bug must propagate")
            _compile_request, blueprint = _blueprint_for_slm_request(
                self.verifier,
                request,
            )
            return _generation_for(
                self.verifier,
                blueprint,
                accepted=outcome == "valid",
            )
        finally:
            self.active -= 1


class CancellationResistantProvider:
    def __init__(self, clock):
        self.clock = clock
        self.requests = []
        self.started = asyncio.Event()
        self.resisted = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def generate(self, request):
        self.requests.append(request)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.resisted.set()
            await self.release.wait()
            return object()
        finally:
            self.finished.set()


class CancellationResistantSleeper:
    def __init__(self):
        self.calls = []
        self.resisted = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def __call__(self, seconds):
        self.calls.append(seconds)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.resisted.set()
            await self.release.wait()
        finally:
            self.finished.set()


class CleanupWindowClock(FakeClock):
    def __init__(self):
        super().__init__()
        self.cleanup_wait_started = asyncio.Event()
        self.reach_hard_deadline = asyncio.Event()

    async def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        seconds = max(0.0, float(seconds))
        if self.now < 8.0:
            await asyncio.sleep(0.001)
            self.now += seconds
            return
        self.cleanup_wait_started.set()
        await self.reach_hard_deadline.wait()
        self.now += seconds


class ConfirmedClosingProvider:
    def __init__(self, clock):
        self.clock = clock
        self.requests = []
        self.started = asyncio.Event()
        self.cancellation_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.closed = asyncio.Event()

    async def generate(self, request):
        self.requests.append(request)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellation_started.set()
            await self.close_release.wait()
            self.closed.set()
            raise


class DeadlineAwareManagedProvider:
    def __init__(self, *, cleanup_confirmed=True):
        self.cleanup_confirmed = cleanup_confirmed
        self.begin_deadlines = []
        self.generation_deadlines = []
        self.started = asyncio.Event()
        self.cancellation_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.reaped = asyncio.Event()
        self.stopped = True
        self.quarantined = False

    async def begin_preparation(self, *, deadline):
        self.begin_deadlines.append(deadline)
        if self.quarantined:
            raise ProviderError("worker_quarantined")
        if self.stopped:
            self.stopped = False

    async def generate_before(
        self,
        request,
        *,
        ready_deadline,
        cancellation_deadline,
    ):
        self.generation_deadlines.append(
            (ready_deadline, cancellation_deadline)
        )
        if self.stopped:
            raise ProviderError("worker_not_ready")
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellation_started.set()
            await self.cleanup_release.wait()
            if not self.cleanup_confirmed:
                self.quarantined = True
                raise ProviderError("worker_quarantined")
            self.stopped = True
            self.reaped.set()
            raise

    async def generate(self, request):
        raise AssertionError("deadline-aware provider seam was bypassed")


class RestartBoundaryProvider:
    def __init__(self):
        self.begin_deadlines = []
        self.epochs = 0
        self.posts = 0
        self.stopped = True

    async def begin_preparation(self, *, deadline):
        self.begin_deadlines.append(deadline)
        if self.stopped:
            self.epochs += 1
            self.stopped = False

    async def generate_before(
        self,
        request,
        *,
        ready_deadline,
        cancellation_deadline,
    ):
        if self.stopped:
            raise ProviderError("worker_not_ready")
        self.posts += 1
        self.stopped = True
        raise ProviderError("transport_error")

    async def generate(self, request):
        raise AssertionError("deadline-aware provider seam was bypassed")


class PreparationFailureProvider:
    def __init__(self, code):
        self.code = code
        self.begin_deadlines = []
        self.generate_calls = 0

    async def begin_preparation(self, *, deadline):
        self.begin_deadlines.append(deadline)
        raise ProviderError(self.code)

    async def generate(self, request):
        self.generate_calls += 1
        raise AssertionError("failed preparation must not generate")


class GenerationFailureProvider:
    def __init__(self, code):
        self.code = code
        self.begin_deadlines = []
        self.generation_calls = 0

    async def begin_preparation(self, *, deadline):
        self.begin_deadlines.append(deadline)

    async def generate_before(
        self,
        request,
        *,
        ready_deadline,
        cancellation_deadline,
    ):
        self.generation_calls += 1
        raise ProviderError(self.code)

    async def generate(self, request):
        raise AssertionError("deadline-aware provider seam was bypassed")


class DynamicReviewedCache:
    def __init__(
        self,
        verifier,
        clock,
        *,
        miss=False,
        corrupt=False,
        advance_per_lookup=0.0,
    ):
        self.verifier = verifier
        self.clock = clock
        self.miss = miss
        self.corrupt = corrupt
        self.advance_per_lookup = advance_per_lookup
        self.keys = []
        self.lookup_times = []

    def lookup_reviewed(self, key):
        self.keys.append(key)
        self.lookup_times.append(self.clock())
        self.clock.now += self.advance_per_lookup
        if self.corrupt:
            raise CacheCorruptionError("cache_read_failed")
        if self.miss:
            return None

        excluded_questions = set(key.excluded_question_ids)
        excluded_templates = set(key.excluded_template_ids)
        excluded_operands = set(key.excluded_operand_signatures)
        excluded_content = set(key.excluded_content_ids)
        excluded_semantics = set(key.excluded_question_semantic_sha256s)
        excluded_contexts = set(key.excluded_context_ids)
        seed_base = key.selection_seed % (2**63)
        for offset in range(1, 512):
            compile_request = CompileRequest(
                world_id=key.world_id,
                skill_id=key.skill_id,
                family_id=key.family_id,
                difficulty=key.difficulty,
                seed=(seed_base + offset) % (2**63),
            )
            blueprint = self.verifier.compiler.compile(compile_request)
            if not set(key.required_procedure_ids).issubset(
                blueprint.allowed_procedure_ids
            ):
                continue
            bundle = _bundle_for(
                self.verifier,
                compile_request,
                required_procedure_ids=key.required_procedure_ids,
                generated_at_utc=f"2026-07-11T19:{offset % 60:02d}:00Z",
            )
            semantic = question_semantic_sha256(bundle.blueprint)
            if bundle.blueprint.question_id in excluded_questions:
                continue
            if bundle.template_id in excluded_templates:
                continue
            if bundle.operand_signature in excluded_operands:
                continue
            if bundle.context_id in excluded_contexts:
                continue
            if semantic in excluded_semantics:
                continue
            if any(
                value in excluded_content
                for value in (
                    bundle.blueprint.content_sha256,
                    bundle.cache_content_sha256,
                    bundle.semantic_content_sha256,
                )
            ):
                continue
            approval_record = hashlib.sha256(
                ("orchestrator-test-approval|" + bundle.semantic_content_sha256).encode(
                    "ascii"
                )
            ).hexdigest()
            review = ReviewReceipt.approved(
                owner_alias="owner-01",
                reviewed_at_utc="2026-07-11T19:59:00Z",
                approved_semantic_content_sha256=bundle.semantic_content_sha256,
                approval_record_sha256=approval_record,
            )
            row_hash = hashlib.sha256(
                ("orchestrator-test-cache-row|" + bundle.cache_content_sha256).encode(
                    "ascii"
                )
            ).hexdigest()
            return ReviewedCacheHit._from_validated_row(
                bundle=bundle,
                cache_row_sha256=row_hash,
                review=review,
            )
        raise AssertionError("could not synthesize a compatible reviewed cache hit")


class Poison:
    def __getattribute__(self, name):
        raise AssertionError(f"replay touched forbidden dependency: {name}")

    def __call__(self, *args, **kwargs):
        raise AssertionError("replay called a forbidden dependency")


class OrchestratorPublicSeamTests(unittest.TestCase):
    def test_orchestrator_public_seam_exists(self):
        self.assertIsNotNone(BatchPreparationOrchestrator)
        self.assertTrue(issubclass(BatchPreparationError, RuntimeError))


class BatchPreparationOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = DistractorVerifier.for_tests()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "wayline.sqlite3"
        self.store = self.open_store()
        self.profile_id = "profile-1"
        self.session_id = "session-1"
        self.state = reduce_events(
            [
                event.activate(
                    ordinal=1,
                    profile=self.profile_id,
                    session=self.session_id,
                )
            ]
        )
        self.request = BattleQuizRequest(
            schemaVersion="wayline.v1",
            requestId="prepare-orchestrator-001",
            sessionId=self.session_id,
            battleId="valuehold-route-001",
            worldId="valuehold",
            battleTier="route_1",
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def open_store(self):
        return QuizStore(
            self.database_path,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )

    def row_counts(self):
        connection = sqlite3.connect(self.database_path)
        try:
            return tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "quiz_machines",
                    "quiz_batch_material",
                    "quiz_preparation_receipts",
                )
            )
        finally:
            connection.close()

    def orchestrator(
        self,
        provider,
        cache,
        clock,
        *,
        ids=None,
        store=None,
        sleeper=None,
    ):
        ids = ids or DeterministicIds()
        return BatchPreparationOrchestrator(
            store=store or self.store,
            compiler=self.verifier.compiler,
            verifier=self.verifier,
            manifest=self.verifier.manifest,
            provider=provider,
            reviewed_cache=cache,
            monotonic=clock,
            sleeper=sleeper or clock.sleep,
            batch_id_factory=ids.batch,
            item_id_factory=ids.item,
        )

    @staticmethod
    def planning_reaches(clock, value):
        original = orchestrator_module.plan_slots

        def advance_after_planning(*args, **kwargs):
            slots = original(*args, **kwargs)
            clock.now = float(value)
            return slots

        return patch.object(
            orchestrator_module,
            "plan_slots",
            side_effect=advance_after_planning,
        )

    async def prepare(self, orchestrator):
        return await orchestrator.prepare(
            self.request,
            profile_id=self.profile_id,
            learner_state=self.state,
            content_version_id=self.verifier.compiler.curriculum.curriculum_id,
            batch_seed=20260711,
        )

    async def test_build_verified_material_does_not_persist_a_normal_quiz(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock, corrupt=True)
        assisted_intents = (
            SlotIntent(
                kind="assisted_worked_example",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            ),
            SlotIntent(
                kind="assisted_supported_mcq",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            ),
            SlotIntent(
                kind="assisted_supported_mcq",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="mental_add_sub",
            ),
        )
        context = BatchContext(
            profile_id=self.profile_id,
            session_id=self.session_id,
            world_id="valuehold",
            battle_id="valuehold_assisted_route",
            core_subskill_ids=("place_value", "mental_add_sub"),
            content_version_id=self.verifier.compiler.curriculum.curriculum_id,
            battle_tier="assisted_route",
        )

        material = await self.orchestrator(
            provider,
            cache,
            clock,
        ).build_verified_material(
            context=context,
            intents=assisted_intents,
            batch_seed=731,
            batch_id="batch_assisted_internal_001",
        )

        self.assertEqual(material.context.battle_tier, "assisted_route")
        self.assertEqual(
            tuple(item.bundle.blueprint.difficulty for item in material.items),
            (2, 1, 1),
        )
        self.assertEqual(self.row_counts(), (0, 0, 0))
        self.assertIsNone(self.store.resumable_batch_id(self.profile_id))

    async def test_live_success_finishes_before_eight_with_one_active_provider(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock, corrupt=True)

        prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertFalse(prepared.replayed)
        self.assertLess(clock(), 8.0)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(provider.max_active, 1)
        self.assertEqual(cache.keys, [])
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("live_verified", "live_verified", "live_verified"),
        )
        self.assertEqual(self.row_counts(), (1, 1, 1))

    async def test_two_attempts_use_distinct_prompts_and_second_semantic_receipt(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock, outcomes=["reject"] * 2)
        cache = DynamicReviewedCache(self.verifier, clock)
        captured = []
        original = orchestrator_module.materialize_live_candidate

        def capture(*args, **kwargs):
            candidate = original(*args, **kwargs)
            captured.append((kwargs.copy(), candidate))
            return candidate

        with patch.object(
            orchestrator_module,
            "materialize_live_candidate",
            side_effect=capture,
        ):
            prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.max_active, 1)
        first_request, second_request = provider.requests
        self.assertNotEqual(first_request.prompt_sha256, second_request.prompt_sha256)
        self.assertEqual([item[0]["live_attempt"] for item in captured], [1, 2])
        self.assertEqual(
            captured[1][0]["attempted_semantic_sha256s"],
            (captured[0][1].question_semantic_sha256,),
        )
        self.assertTrue(all(time >= 8.0 for time in cache.lookup_times))
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("reviewed_cache", "reviewed_cache", "reviewed_cache"),
        )

    async def test_fallback_waits_for_eight_and_no_provider_starts_after_eight(self):
        clock = FakeClock()
        provider = FakeProvider(
            self.verifier,
            clock,
            outcomes=["typed_error", "reject"],
        )
        cache = DynamicReviewedCache(self.verifier, clock)

        await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertTrue(provider.start_times)
        self.assertTrue(all(time < 8.0 for time in provider.start_times))
        self.assertTrue(cache.lookup_times)
        self.assertTrue(all(time >= 8.0 for time in cache.lookup_times))

    async def test_early_sleep_wakeup_cannot_start_fallback_before_eight(self):
        clock = EarlyWakeClock()
        provider = FakeProvider(self.verifier, clock, outcomes=["reject"] * 2)
        cache = DynamicReviewedCache(self.verifier, clock)

        await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertGreaterEqual(clock.completed_sleeps, 2)
        self.assertTrue(all(time >= 8.0 for time in cache.lookup_times))

    async def test_late_live_result_is_cancelled_ignored_and_cache_fills_batch(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock, outcomes=["late"])
        cache = DynamicReviewedCache(self.verifier, clock)

        prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.cancelled, 1)
        self.assertEqual(provider.active, 0)
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("reviewed_cache", "reviewed_cache", "reviewed_cache"),
        )

    async def test_confirmed_close_before_ten_precedes_reviewed_fallback(self):
        clock = CleanupWindowClock()
        provider = ConfirmedClosingProvider(clock)
        cache = DynamicReviewedCache(self.verifier, clock)
        preparation = asyncio.create_task(
            self.prepare(self.orchestrator(provider, cache, clock))
        )

        try:
            await asyncio.wait_for(provider.cancellation_started.wait(), timeout=0.2)
            await asyncio.wait_for(clock.cleanup_wait_started.wait(), timeout=0.2)
            self.assertEqual(cache.keys, [])
            provider.close_release.set()
            prepared = await preparation
        finally:
            provider.close_release.set()
            await asyncio.gather(preparation, return_exceptions=True)

        self.assertTrue(provider.closed.is_set())
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("reviewed_cache", "reviewed_cache", "reviewed_cache"),
        )
        self.assertTrue(cache.lookup_times)
        self.assertTrue(all(time >= 8.0 for time in cache.lookup_times))

    async def test_unconfirmed_close_at_ten_fails_without_fallback_or_commit(self):
        clock = CleanupWindowClock()
        provider = ConfirmedClosingProvider(clock)
        cache = DynamicReviewedCache(self.verifier, clock)
        orchestrator = self.orchestrator(provider, cache, clock)
        preparation = asyncio.create_task(self.prepare(orchestrator))

        try:
            await asyncio.wait_for(provider.cancellation_started.wait(), timeout=0.2)
            await asyncio.wait_for(clock.cleanup_wait_started.wait(), timeout=0.2)
            clock.reach_hard_deadline.set()
            with self.assertRaises(BatchPreparationError) as caught:
                await preparation
        finally:
            provider.close_release.set()
            await asyncio.wait_for(provider.closed.wait(), timeout=0.2)
            await asyncio.gather(preparation, return_exceptions=True)

        self.assertEqual(caught.exception.code, "cancellation_failed")
        self.assertEqual(clock(), 10.0)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))
        self.assertTrue(
            all(task.done() for task in orchestrator._retained_tasks)
        )

    async def test_managed_reap_before_ten_precedes_reviewed_fallback(self):
        clock = CleanupWindowClock()
        provider = DeadlineAwareManagedProvider()
        cache = DynamicReviewedCache(self.verifier, clock)
        preparation = asyncio.create_task(
            self.prepare(self.orchestrator(provider, cache, clock))
        )

        try:
            await asyncio.wait_for(provider.cancellation_started.wait(), timeout=0.2)
            await asyncio.wait_for(clock.cleanup_wait_started.wait(), timeout=0.2)
            self.assertEqual(cache.keys, [])
            provider.cleanup_release.set()
            prepared = await preparation
        finally:
            provider.cleanup_release.set()
            await asyncio.gather(preparation, return_exceptions=True)

        self.assertTrue(provider.reaped.is_set())
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generation_deadlines, [(8.0, 10.0)])
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("reviewed_cache", "reviewed_cache", "reviewed_cache"),
        )
        self.assertTrue(all(time >= 8.0 for time in cache.lookup_times))

    async def test_unreaped_managed_generation_at_ten_has_no_cache_or_write(self):
        clock = CleanupWindowClock()
        provider = DeadlineAwareManagedProvider(cleanup_confirmed=False)
        cache = DynamicReviewedCache(self.verifier, clock)
        orchestrator = self.orchestrator(provider, cache, clock)
        preparation = asyncio.create_task(self.prepare(orchestrator))

        try:
            await asyncio.wait_for(provider.cancellation_started.wait(), timeout=0.2)
            await asyncio.wait_for(clock.cleanup_wait_started.wait(), timeout=0.2)
            clock.reach_hard_deadline.set()
            with self.assertRaises(BatchPreparationError) as caught:
                await preparation
        finally:
            provider.cleanup_release.set()
            await asyncio.gather(preparation, return_exceptions=True)

        self.assertEqual(caught.exception.code, "cancellation_failed")
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generation_deadlines, [(8.0, 10.0)])
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_stopped_worker_restarts_only_at_next_preparation_boundary(self):
        clock = FakeClock()
        provider = RestartBoundaryProvider()
        cache = DynamicReviewedCache(self.verifier, clock)
        orchestrator = self.orchestrator(provider, cache, clock)

        await self.prepare(orchestrator)
        self.assertEqual(provider.epochs, 1)
        self.assertEqual(provider.posts, 1)

        second_profile_id = "profile-2"
        second_session_id = "session-2"
        second_state = reduce_events(
            [
                event.activate(
                    ordinal=1,
                    profile=second_profile_id,
                    session=second_session_id,
                )
            ]
        )
        second_request = self.request.model_copy(
            update={
                "request_id": "prepare-orchestrator-002",
                "session_id": second_session_id,
                "battle_id": "valuehold-route-002",
            }
        )
        await orchestrator.prepare(
            second_request,
            profile_id=second_profile_id,
            learner_state=second_state,
            content_version_id=self.verifier.compiler.curriculum.curriculum_id,
            batch_seed=20260712,
        )

        self.assertEqual(provider.epochs, 2)
        self.assertEqual(provider.posts, 2)
        self.assertEqual(provider.begin_deadlines, [8.0, 16.0])

    async def test_preexisting_quarantine_fails_without_cache_or_write(self):
        clock = FakeClock()
        provider = DeadlineAwareManagedProvider()
        provider.quarantined = True
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generation_deadlines, [])
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_uncertain_start_or_readiness_fails_without_cache_or_write(self):
        clock = FakeClock()
        provider = PreparationFailureProvider("worker_quarantined")
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generate_calls, 0)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_unsafe_active_worker_state_fails_without_cache_or_write(self):
        clock = FakeClock()
        provider = PreparationFailureProvider("worker_unsafe_state")
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "worker_unsafe_state")
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generate_calls, 0)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_missing_managed_authority_at_preparation_fails_without_fallback(self):
        clock = FakeClock()
        provider = PreparationFailureProvider("managed_worker_required")
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertEqual(str(caught.exception), "managed_worker_required")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generate_calls, 0)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_safely_unavailable_worker_can_use_reviewed_cache(self):
        clock = FakeClock()
        provider = PreparationFailureProvider("worker_not_ready")
        cache = DynamicReviewedCache(self.verifier, clock)

        prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generate_calls, 0)
        self.assertTrue(cache.keys)
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("reviewed_cache", "reviewed_cache", "reviewed_cache"),
        )
        self.assertEqual(self.row_counts(), (1, 1, 1))

    async def test_live_quarantine_fails_without_cache_or_write(self):
        clock = FakeClock()
        provider = GenerationFailureProvider("worker_quarantined")
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "worker_quarantined")
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generation_calls, 1)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_missing_managed_authority_during_live_generation_fails_without_fallback(self):
        clock = FakeClock()
        provider = GenerationFailureProvider("managed_worker_required")
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "managed_worker_required")
        self.assertEqual(str(caught.exception), "managed_worker_required")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generation_calls, 1)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_manifest_worker_mismatch_fails_without_fallback_or_write(self):
        clock = FakeClock()
        provider = GenerationFailureProvider("manifest_worker_mismatch")
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "manifest_worker_mismatch")
        self.assertEqual(str(caught.exception), "manifest_worker_mismatch")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertEqual(provider.generation_calls, 1)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_live_stopped_worker_remains_cache_safe(self):
        clock = FakeClock()
        provider = GenerationFailureProvider("worker_not_ready")
        cache = DynamicReviewedCache(self.verifier, clock)

        prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(provider.begin_deadlines, [8.0])
        self.assertGreaterEqual(provider.generation_calls, 1)
        self.assertTrue(cache.keys)
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("reviewed_cache", "reviewed_cache", "reviewed_cache"),
        )
        self.assertEqual(self.row_counts(), (1, 1, 1))

    async def test_cancellation_resistant_provider_fails_promptly_without_fallback(self):
        clock = FakeClock()
        provider = CancellationResistantProvider(clock)
        cache = DynamicReviewedCache(self.verifier, clock)
        loop = asyncio.get_running_loop()
        preparation = asyncio.create_task(
            self.prepare(self.orchestrator(provider, cache, clock))
        )
        await asyncio.wait_for(provider.started.wait(), timeout=0.2)
        started_at = loop.time()
        release_handle = loop.call_later(0.05, provider.release.set)

        try:
            with self.assertRaises(BatchPreparationError) as caught:
                await preparation
            elapsed = loop.time() - started_at
        finally:
            release_handle.cancel()
            provider.release.set()
            await asyncio.wait_for(provider.finished.wait(), timeout=0.2)

        self.assertEqual(caught.exception.code, "cancellation_failed")
        self.assertLess(elapsed, 0.03)
        self.assertTrue(provider.resisted.is_set())
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_cancellation_resistant_sleeper_fails_before_next_provider_or_commit(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        sleeper = CancellationResistantSleeper()
        cache = DynamicReviewedCache(self.verifier, clock)
        loop = asyncio.get_running_loop()
        preparation = asyncio.create_task(
            self.prepare(
                self.orchestrator(
                    provider,
                    cache,
                    clock,
                    sleeper=sleeper,
                )
            )
        )
        await asyncio.wait_for(sleeper.resisted.wait(), timeout=0.2)
        started_at = loop.time()
        release_handle = loop.call_later(0.05, sleeper.release.set)

        try:
            with self.assertRaises(BatchPreparationError) as caught:
                await preparation
            elapsed = loop.time() - started_at
        finally:
            release_handle.cancel()
            sleeper.release.set()
            await asyncio.wait_for(sleeper.finished.wait(), timeout=0.2)

        self.assertEqual(caught.exception.code, "cancellation_failed")
        self.assertLess(elapsed, 0.03)
        self.assertTrue(sleeper.resisted.is_set())
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_caller_cancellation_is_preserved_when_provider_resists(self):
        clock = FakeClock()
        provider = CancellationResistantProvider(clock)
        cache = DynamicReviewedCache(self.verifier, clock)
        preparation = asyncio.create_task(
            self.prepare(self.orchestrator(provider, cache, clock))
        )
        await asyncio.wait_for(provider.started.wait(), timeout=0.2)
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        release_handle = loop.call_later(0.05, provider.release.set)

        try:
            preparation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await preparation
            elapsed = loop.time() - started_at
        finally:
            release_handle.cancel()
            provider.release.set()
            await asyncio.wait_for(provider.finished.wait(), timeout=0.2)

        self.assertLess(elapsed, 0.03)
        self.assertTrue(provider.resisted.is_set())
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_mixed_live_and_cache_sources_preserve_actual_exclusions(self):
        clock = FakeClock()
        provider = FakeProvider(
            self.verifier,
            clock,
            outcomes=["valid", "reject", "reject", "reject", "reject"],
        )
        cache = DynamicReviewedCache(self.verifier, clock)

        prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("live_verified", "reviewed_cache", "reviewed_cache"),
        )
        live = prepared.material.items[0].bundle
        first_key = cache.keys[0]
        self.assertIn(live.blueprint.question_id, first_key.excluded_question_ids)
        self.assertIn(
            question_semantic_sha256(live.blueprint),
            first_key.excluded_question_semantic_sha256s,
        )
        self.assertIn(live.template_id, first_key.excluded_template_ids)
        self.assertIn(live.operand_signature, first_key.excluded_operand_signatures)
        self.assertIn(live.blueprint.content_sha256, first_key.excluded_content_ids)

    async def test_missing_cache_fails_closed_without_any_quiz_rows(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock, outcomes=["reject"] * 2)
        cache = DynamicReviewedCache(self.verifier, clock, miss=True)

        with self.assertRaises(BatchPreparationError) as caught:
            await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "fallback_unavailable")
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_corrupt_cache_fails_closed_without_any_quiz_rows(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock, corrupt=True)

        with self.planning_reaches(clock, 8.0):
            with self.assertRaises(BatchPreparationError) as caught:
                await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "fallback_cache_error")
        self.assertEqual(provider.requests, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_restart_replay_precedes_planner_compiler_provider_and_cache(self):
        clock = FakeClock()
        first = await self.prepare(
            self.orchestrator(
                FakeProvider(self.verifier, clock),
                DynamicReviewedCache(self.verifier, clock, corrupt=True),
                clock,
            )
        )
        self.store.close()
        self.store = self.open_store()
        poison = Poison()
        replay_orchestrator = BatchPreparationOrchestrator(
            store=self.store,
            compiler=poison,
            verifier=poison,
            manifest=poison,
            provider=poison,
            reviewed_cache=poison,
            monotonic=poison,
            sleeper=poison,
            batch_id_factory=poison,
            item_id_factory=poison,
        )

        with patch.object(
            orchestrator_module,
            "plan_slots",
            side_effect=AssertionError("planner must not run on replay"),
        ):
            replay = await self.prepare(replay_orchestrator)

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.material, first.material)
        self.assertEqual(replay.public_output, first.public_output)
        self.assertEqual(self.row_counts(), (1, 1, 1))

    async def test_concurrent_same_request_returns_one_persisted_winner(self):
        clock = FakeClock()
        first = self.orchestrator(
            FakeProvider(self.verifier, clock),
            DynamicReviewedCache(self.verifier, clock, corrupt=True),
            clock,
            ids=DeterministicIds("a"),
        )
        second = self.orchestrator(
            FakeProvider(self.verifier, clock),
            DynamicReviewedCache(self.verifier, clock, corrupt=True),
            clock,
            ids=DeterministicIds("b"),
        )

        outcomes = await asyncio.gather(self.prepare(first), self.prepare(second))

        self.assertEqual(outcomes[0].material.batch_id, outcomes[1].material.batch_id)
        self.assertEqual(sum(item.replayed for item in outcomes), 1)
        self.assertEqual(self.row_counts(), (1, 1, 1))

    async def test_exact_eight_starts_cache_only(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.planning_reaches(clock, 8.0):
            prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(provider.requests, [])
        self.assertEqual(cache.lookup_times[0], 8.0)
        self.assertEqual(prepared.material.public_batch.item_count, 3)

    async def test_provider_is_not_started_if_boundary_arrives_before_task_runs(self):
        clock = SequenceClock([0.0, 0.0, 0.0, 7.9, 7.9, 8.0])
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock)

        prepared = await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(provider.requests, [])
        self.assertEqual(
            tuple(item.source_proof.source_kind for item in prepared.material.items),
            ("reviewed_cache", "reviewed_cache", "reviewed_cache"),
        )

    async def test_exact_ten_performs_no_lookup_and_persists_nothing(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.planning_reaches(clock, 10.0):
            with self.assertRaises(BatchPreparationError) as caught:
                await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "preparation_deadline_exceeded")
        self.assertEqual(provider.requests, [])
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_crossing_ten_during_lookup_prevents_accept_and_commit(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(
            self.verifier,
            clock,
            advance_per_lookup=0.1,
        )

        with self.planning_reaches(clock, 9.9):
            with self.assertRaises(BatchPreparationError) as caught:
                await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "preparation_deadline_exceeded")
        self.assertEqual(len(cache.keys), 1)
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_crossing_ten_while_building_key_prevents_cache_lookup(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock)
        original_key = BatchMaterialBuilder.next_fallback_cache_key

        def key_at_deadline(builder):
            key = original_key(builder)
            clock.now = 10.0
            return key

        with self.planning_reaches(clock, 8.0):
            with patch.object(
                BatchMaterialBuilder,
                "next_fallback_cache_key",
                autospec=True,
                side_effect=key_at_deadline,
            ):
                with self.assertRaises(BatchPreparationError) as caught:
                    await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "preparation_deadline_exceeded")
        self.assertEqual(cache.keys, [])
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_crossing_ten_during_finalize_prevents_atomic_commit(self):
        clock = FakeClock()
        provider = FakeProvider(self.verifier, clock)
        cache = DynamicReviewedCache(self.verifier, clock)
        original_finalize = BatchMaterialBuilder.finalize

        def finish_at_deadline(builder):
            material = original_finalize(builder)
            clock.now = 10.0
            return material

        with self.planning_reaches(clock, 8.0):
            with patch.object(
                BatchMaterialBuilder,
                "finalize",
                autospec=True,
                side_effect=finish_at_deadline,
            ):
                with self.assertRaises(BatchPreparationError) as caught:
                    await self.prepare(self.orchestrator(provider, cache, clock))

        self.assertEqual(caught.exception.code, "preparation_deadline_exceeded")
        self.assertEqual(self.row_counts(), (0, 0, 0))

    async def test_unexpected_provider_programmer_error_propagates(self):
        clock = FakeClock()
        provider = FakeProvider(
            self.verifier,
            clock,
            outcomes=["programmer_error"],
        )
        cache = DynamicReviewedCache(self.verifier, clock)

        with self.assertRaisesRegex(RuntimeError, "programmer bug must propagate"):
            await self.prepare(self.orchestrator(provider, cache, clock))
        self.assertEqual(self.row_counts(), (0, 0, 0))
