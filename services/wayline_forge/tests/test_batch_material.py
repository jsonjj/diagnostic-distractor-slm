from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from services.wayline_forge.app import batch_material as batch_material_module
from services.wayline_forge.app.adaptive_planner import SlotIntent
from services.wayline_forge.app.batch_material import (
    BATCH_MATERIAL_SCHEMA_VERSION,
    BatchItemSourceProof,
    BatchContext,
    BatchMaterialBuilder,
    BatchMaterialError,
    BatchMaterialValidationError,
    RetryableBatchMaterialRejection,
    VerifiedBatchMaterial,
)
from services.wayline_forge.app.contracts import PublicQuizBatch, parse_public_json
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.events import ObservationEvent
from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.reviewed_cache import ReviewReceipt, ReviewedCache
from services.wayline_forge.app.quiz_machine import (
    FinalItemResult,
    RevealedSelectionResult,
    SealedQuiz,
)
from services.wayline_forge.app.slot_materializer import (
    MaterializedSlot,
    materialize_slots,
    question_semantic_sha256,
)
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class BatchMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = DistractorVerifier.for_tests()

    def setUp(self) -> None:
        intents = tuple(
            SlotIntent(
                kind="novel_current_skill",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            )
            for _ in range(3)
        )
        self.slots = materialize_slots(
            intents,
            "route_1",
            20260711,
            self.verifier.compiler,
        )
        self.context = BatchContext(
            profile_id="profile-owner-001",
            session_id="session-owner-001",
            world_id="valuehold",
            battle_id="battle-valuehold-001",
            core_subskill_ids=("place_value", "mental_add_sub"),
            content_version_id=self.verifier.compiler.curriculum.curriculum_id,
            battle_tier="route_1",
        )

    def bundle_for(
        self,
        request: CompileRequest,
        *,
        procedure_ids: tuple[str, ...] | None = None,
        generated_at_utc: str = "2026-07-11T18:00:00Z",
    ) -> VerifiedQuestionBundle:
        blueprint = self.verifier.compiler.compile(request)
        selected = procedure_ids or blueprint.allowed_procedure_ids[:3]
        self.assertEqual(len(selected), 3)
        distractors = [
            {
                "misconception": self.verifier.registry.canonical_label(procedure_id),
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
        ]
        generation = replace(
            self.verifier.fixture_generation(blueprint, "accepted.json"),
            text=_canonical_json({"distractors": distractors}),
            generated_at_utc=generated_at_utc,
        )
        result = self.verifier.verify_generation(blueprint, generation)
        self.assertTrue(result.accepted, result.code)
        self.assertIsNotNone(result.value)
        return VerifiedQuestionBundle.from_verified(
            compiler=self.verifier.compiler,
            request=request,
            blueprint=blueprint,
            verified=result.value,
            generation=generation,
            manifest=self.verifier.manifest,
        )

    def builder(
        self,
        slots: tuple[MaterializedSlot, ...] | None = None,
    ) -> BatchMaterialBuilder:
        return BatchMaterialBuilder(
            batch_id="batch-valuehold-001",
            context=self.context,
            planned_slots=slots or self.slots,
        )

    @staticmethod
    def slot_with_required_route(
        slot: MaterializedSlot,
        procedure_id: str,
    ) -> MaterializedSlot:
        return replace(
            slot,
            required_procedure_ids=(procedure_id,),
            cache_key=replace(
                slot.cache_key,
                required_procedure_ids=(procedure_id,),
            ),
        )

    def different_bundle_for_slot(
        self,
        slot: MaterializedSlot,
    ) -> VerifiedQuestionBundle:
        for offset in range(1, 100):
            request = replace(slot.request, seed=slot.request.seed + offset)
            blueprint = self.verifier.compiler.compile(request)
            if (
                question_semantic_sha256(blueprint)
                != slot.question_semantic_sha256
            ):
                return self.bundle_for(
                    request,
                    generated_at_utc=f"2026-07-11T18:{offset:02d}:00Z",
                )
        self.fail("could not build a distinct cache-style bundle")

    def complete_material(self) -> VerifiedBatchMaterial:
        builder = self.builder()
        for index, slot in enumerate(self.slots, start=1):
            builder.accept_live(
                self.bundle_for(
                    slot.request,
                    generated_at_utc=f"2026-07-11T18:0{index}:00Z",
                ),
                item_instance_id=f"item_{index:032x}",
            )
        return builder.finalize()

    def assisted_material(self) -> VerifiedBatchMaterial:
        intents = (
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
        slots = materialize_slots(
            intents,
            "assisted_route",
            20260712,
            self.verifier.compiler,
        )
        context = replace(
            self.context,
            battle_id="valuehold_assisted_route",
            battle_tier="assisted_route",
        )
        builder = BatchMaterialBuilder(
            batch_id="batch-assisted-internal-001",
            context=context,
            planned_slots=slots,
        )
        for index, slot in enumerate(slots, start=1):
            builder.accept_live(
                self.bundle_for(slot.request),
                item_instance_id=f"item_{index + 100:032x}",
            )
        return builder.finalize()

    @staticmethod
    def rehash_private_payload(payload: dict[str, object]) -> str:
        unsigned = dict(payload)
        unsigned.pop("batchMaterialSha256", None)
        payload["batchMaterialSha256"] = _canonical_sha256(unsigned)
        return _canonical_json(payload)

    @staticmethod
    def rehash_plan_contract(plan: dict[str, object]) -> None:
        unsigned = dict(plan)
        unsigned.pop("planSha256", None)
        plan["planSha256"] = _canonical_sha256(unsigned)

    @staticmethod
    def rehash_slot_contract(slot: dict[str, object]) -> None:
        unsigned = dict(slot)
        unsigned.pop("slotContractSha256", None)
        slot["slotContractSha256"] = _canonical_sha256(unsigned)

    def revealed_fixture(
        self,
        material: VerifiedBatchMaterial,
        *,
        item_index: int = 0,
    ) -> tuple[object, FinalItemResult, ObservationEvent]:
        item = material.items[item_index]
        wrong_route = next(
            route for route in item.routes if route.procedure_id is not None
        )
        correct_route = next(
            route for route in item.routes if route.procedure_id is None
        )
        final = FinalItemResult(
            item_id=item.placement.item_instance_id,
            first_selection=RevealedSelectionResult(
                option_id=wrong_route.option_id,
                confidence="leaning",
                is_correct=False,
            ),
            final_selection=RevealedSelectionResult(
                option_id=correct_route.option_id,
                confidence="certain",
                is_correct=True,
            ),
            correct_option_id=correct_route.option_id,
            correct_answer=item.bundle.blueprint.canonical_answer.display,
            trusted_steps=item.trusted_steps,
            possible_error=wrong_route.feedback,
            reliable_method=wrong_route.reliable_method,
            self_corrected=True,
        )
        event = ObservationEvent(
            schema_version="wayline.event.v1",
            event_id=f"observation-batch-material-{item_index + 1:03d}",
            idempotency_id=f"observation-request-{item_index + 1:03d}",
            ordinal=item_index + 1,
            profile_id=material.context.profile_id,
            session_id=material.context.session_id,
            world_id=item.bundle.blueprint.world_id,
            battle_id=material.context.battle_id,
            occurred_at="2026-07-11T18:30:00+00:00",
            batch_id=material.batch_id,
            item_id=item.placement.item_instance_id,
            question_id=item.bundle.blueprint.question_id,
            template_id=item.bundle.template_id,
            content_version_id=material.context.content_version_id,
            skill_id=item.bundle.blueprint.skill_id,
            world_core_subskill_ids=material.context.core_subskill_ids,
            operand_signature=item.bundle.operand_signature,
            context_id=item.bundle.context_id,
            first_option_id=wrong_route.option_id,
            final_option_id=correct_route.option_id,
            first_confidence="leaning",
            final_confidence="certain",
            first_correct=False,
            final_correct=True,
            choice_changed=True,
            self_corrected=True,
            first_procedure_id=wrong_route.procedure_id,
            final_procedure_id=None,
            targeted_procedure_ids=item.required_procedure_ids,
            is_transfer=item.is_transfer,
            is_changed_context_transfer=item.is_changed_context_transfer,
            valid_for_progression=item.valid_for_progression,
            batch_wrong_count=1,
            canonical_feedback=(wrong_route.feedback, wrong_route.reliable_method),
            optional_wording_shown=None,
            receipts=item.event_receipts,
        )
        return item, final, event

    def test_missing_targeted_route_is_a_retryable_nonadvancing_rejection(self):
        first = self.slots[0]
        self.assertGreaterEqual(len(first.blueprint.allowed_procedure_ids), 4)
        required = first.blueprint.allowed_procedure_ids[0]
        slots = (
            self.slot_with_required_route(first, required),
            *self.slots[1:],
        )
        omitted = tuple(
            procedure_id
            for procedure_id in first.blueprint.allowed_procedure_ids
            if procedure_id != required
        )[:3]
        bundle = self.bundle_for(first.request, procedure_ids=omitted)
        builder = self.builder(slots)

        with self.assertRaises(RetryableBatchMaterialRejection) as raised:
            builder.accept_live(bundle, item_instance_id="item_" + "1" * 32)

        self.assertEqual(raised.exception.code, "missing_required_procedure")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(builder.accepted_count, 0)
        self.assertEqual(builder.next_slot, slots[0])

    def test_assisted_material_is_practice_only_at_item_boundary(self):
        material = self.assisted_material()

        self.assertEqual(
            tuple(item.kind for item in material.items),
            (
                "assisted_worked_example",
                "assisted_supported_mcq",
                "assisted_supported_mcq",
            ),
        )
        self.assertTrue(
            all(not item.valid_for_progression for item in material.items)
        )

    def test_actual_cache_selection_updates_exclusions_and_catches_later_plan(self):
        cached = self.different_bundle_for_slot(self.slots[0])
        duplicate_plan = replace(
            self.slots[1],
            request=cached.request,
            blueprint=cached.blueprint,
            difficulty=cached.blueprint.difficulty,
            operand_signature=cached.operand_signature,
            question_semantic_sha256=question_semantic_sha256(cached.blueprint),
            cache_key=replace(
                self.slots[1].cache_key,
                world_id=cached.blueprint.world_id,
                skill_id=cached.blueprint.skill_id,
                family_id=cached.blueprint.family_id,
                difficulty=cached.blueprint.difficulty,
            ),
        )
        slots = (self.slots[0], duplicate_plan, self.slots[2])
        builder = self.builder(slots)

        builder.accept_live(cached, item_instance_id="item_" + "2" * 32)

        exclusions = builder.selection_exclusions
        self.assertIn(cached.blueprint.question_id, exclusions.question_ids)
        self.assertIn(
            question_semantic_sha256(cached.blueprint),
            exclusions.question_semantic_sha256s,
        )
        self.assertEqual(exclusions.adjacent_template_ids, (cached.template_id,))
        self.assertEqual(
            exclusions.adjacent_operand_signatures,
            (cached.operand_signature,),
        )
        fallback_key = builder.next_fallback_cache_key()
        planned_key = duplicate_plan.cache_key
        self.assertEqual(fallback_key.world_id, planned_key.world_id)
        self.assertEqual(fallback_key.skill_id, planned_key.skill_id)
        self.assertEqual(fallback_key.family_id, planned_key.family_id)
        self.assertEqual(fallback_key.difficulty, planned_key.difficulty)
        self.assertEqual(fallback_key.selection_seed, planned_key.selection_seed)
        self.assertEqual(
            fallback_key.excluded_question_ids,
            exclusions.question_ids,
        )
        self.assertEqual(
            fallback_key.excluded_question_semantic_sha256s,
            exclusions.question_semantic_sha256s,
        )
        self.assertEqual(
            fallback_key.excluded_template_ids,
            exclusions.adjacent_template_ids,
        )
        self.assertEqual(
            fallback_key.excluded_operand_signatures,
            exclusions.adjacent_operand_signatures,
        )
        self.assertEqual(fallback_key.excluded_content_ids, exclusions.content_ids)
        self.assertEqual(
            fallback_key.excluded_context_ids,
            duplicate_plan.excluded_context_ids,
        )
        with self.assertRaises(RetryableBatchMaterialRejection) as raised:
            builder.accept_live(cached, item_instance_id="item_" + "3" * 32)
        self.assertEqual(raised.exception.code, "duplicate_question_semantic")
        self.assertEqual(builder.accepted_count, 1)

    def test_finalize_requires_exact_tier_length_and_returns_frozen_safe_material(self):
        builder = self.builder()
        builder.accept_live(
            self.bundle_for(self.slots[0].request),
            item_instance_id="item_" + "4" * 32,
        )
        with self.assertRaises(BatchMaterialError) as raised:
            builder.finalize()
        self.assertEqual(raised.exception.code, "incomplete_batch")

        material = self.complete_material()
        self.assertIsInstance(material.public_batch, PublicQuizBatch)
        self.assertIsInstance(material.sealed_quiz, SealedQuiz)
        self.assertEqual(material.public_batch.item_count, 3)
        self.assertRegex(material.batch_material_sha256, r"^[0-9a-f]{64}$")
        with self.assertRaises(FrozenInstanceError):
            material.batch_id = "changed"
        with self.assertRaises(FrozenInstanceError):
            material.context.world_id = "changed"

        public = material.public_payload()
        self.assertEqual(
            parse_public_json(PublicQuizBatch, _canonical_json(public)),
            material.public_batch,
        )
        self.assertEqual(
            set(public),
            {"schemaVersion", "batchId", "itemCount", "items"},
        )
        serialized = _canonical_json(public).casefold()
        for item in material.items:
            for forbidden in (
                item.bundle.blueprint.question_id,
                item.bundle.blueprint.content_sha256,
                item.bundle.source_bundle_sha256,
                item.bundle.cache_content_sha256,
                item.bundle.semantic_content_sha256,
                *(option.option_id for option in item.bundle.options),
            ):
                self.assertNotIn(forbidden.casefold(), serialized)
        for forbidden_name in (
            "correctoption",
            "procedure",
            "feedback",
            "trustedsteps",
            "receipt",
            "questionid",
            "templateid",
            "operand",
        ):
            self.assertNotIn(forbidden_name, serialized)

    def test_private_restart_revalidates_bundles_and_recreates_exact_material(self):
        material = self.complete_material()
        private = material.to_private_json()

        restored = VerifiedBatchMaterial.from_private_json(
            private,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        restored_against_plan = VerifiedBatchMaterial.from_private_json(
            private,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
            expected_context=self.context,
            planned_slots=self.slots,
        )

        self.assertEqual(restored, material)
        self.assertEqual(restored_against_plan, material)
        self.assertEqual(restored.public_batch, material.public_batch)
        self.assertEqual(restored.sealed_quiz, material.sealed_quiz)
        self.assertEqual(restored.public_payload(), material.public_payload())
        self.assertEqual(restored.to_private_json(), private)
        self.assertNotIn("rawSlmOutput", private)
        self.assertNotIn("generationText", private)
        private_payload = json.loads(private)
        private_items = private_payload["items"]
        self.assertEqual(BATCH_MATERIAL_SCHEMA_VERSION, "wayline.batch-material.v4")
        self.assertEqual(
            private_payload["schemaVersion"],
            BATCH_MATERIAL_SCHEMA_VERSION,
        )
        for persisted, item in zip(private_items, material.items, strict=True):
            self.assertEqual(
                persisted["excludedContextIds"],
                list(item.excluded_context_ids),
            )
            self.assertEqual(
                persisted["excludedQuestionSemanticSha256s"],
                list(item.excluded_question_semantic_sha256s),
            )

    def test_frozen_plan_contract_persists_every_slot_authorization_and_rebuilds(self):
        material = self.complete_material()

        self.assertTrue(hasattr(batch_material_module, "BatchPlanContract"))
        self.assertTrue(hasattr(batch_material_module, "PlannedSlotContract"))
        BatchPlanContract = batch_material_module.BatchPlanContract
        PlannedSlotContract = batch_material_module.PlannedSlotContract
        self.assertIsInstance(material.plan_contract, BatchPlanContract)
        self.assertEqual(material.plan_sha256, material.plan_contract.plan_sha256)
        self.assertRegex(material.plan_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            material.plan_contract.reconstruct_slots(self.verifier.compiler),
            self.slots,
        )
        with self.assertRaises(FrozenInstanceError):
            material.plan_contract.plan_sha256 = "f" * 64
        with self.assertRaises(FrozenInstanceError):
            material.plan_contract.slots[0].kind = "under_sampled_core_skill"

        private = json.loads(material.to_private_json())
        plan = private["planContract"]
        self.assertEqual(
            set(plan),
            {"schemaVersion", "slots", "planSha256"},
        )
        self.assertEqual(plan["planSha256"], material.plan_sha256)
        self.assertEqual(len(plan["slots"]), len(self.slots))
        persisted = plan["slots"][0]
        self.assertEqual(
            set(persisted),
            {
                "schemaVersion",
                "slotIndex",
                "kind",
                "campaignWorldId",
                "contentWorldId",
                "skillId",
                "familyId",
                "difficulty",
                "compileSeed",
                "plannedQuestionId",
                "plannedTemplateId",
                "plannedContentSha256",
                "plannedQuestionSemanticSha256",
                "plannedOperandSignature",
                "plannedBlueprintSha256",
                "requiredProcedureIds",
                "selectionSeed",
                "registryId",
                "curriculumId",
                "excludedItemIds",
                "excludedQuestionIds",
                "excludedQuestionSemanticSha256s",
                "excludedTemplateIds",
                "excludedOperandSignatures",
                "excludedContextIds",
                "excludedContentIds",
                "slotContractSha256",
            },
        )
        slot = self.slots[0]
        self.assertEqual(persisted["slotIndex"], slot.slot_index)
        self.assertEqual(persisted["kind"], slot.kind)
        self.assertEqual(persisted["campaignWorldId"], slot.campaign_world_id)
        self.assertEqual(persisted["contentWorldId"], slot.request.world_id)
        self.assertEqual(persisted["skillId"], slot.request.skill_id)
        self.assertEqual(persisted["familyId"], slot.request.family_id)
        self.assertEqual(persisted["difficulty"], slot.request.difficulty)
        self.assertEqual(persisted["compileSeed"], slot.request.seed)
        self.assertEqual(
            persisted["requiredProcedureIds"],
            list(slot.required_procedure_ids),
        )
        self.assertEqual(persisted["selectionSeed"], slot.selection_seed)
        self.assertEqual(persisted["registryId"], slot.cache_key.registry_id)
        self.assertEqual(
            persisted["curriculumId"],
            slot.cache_key.curriculum_id,
        )
        for persisted_name, expected in (
            ("excludedItemIds", slot.excluded_item_ids),
            ("excludedQuestionIds", slot.excluded_question_ids),
            (
                "excludedQuestionSemanticSha256s",
                slot.excluded_question_semantic_sha256s,
            ),
            ("excludedTemplateIds", slot.excluded_template_ids),
            (
                "excludedOperandSignatures",
                slot.excluded_operand_signatures,
            ),
            ("excludedContextIds", slot.excluded_context_ids),
            ("excludedContentIds", slot.excluded_content_ids),
        ):
            self.assertEqual(persisted[persisted_name], list(expected))

        self.assertIsInstance(material.plan_contract.slots[0], PlannedSlotContract)

    def test_restart_rebuilds_plan_without_caller_state_and_requires_exact_external_plan(self):
        material = self.complete_material()
        private = material.to_private_json()

        restored = VerifiedBatchMaterial.from_private_json(
            private,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
        )
        self.assertEqual(restored.plan_contract.reconstruct_slots(self.verifier.compiler), self.slots)

        changed_slot = replace(
            self.slots[0],
            selection_seed=self.slots[0].selection_seed + 1,
            cache_key=replace(
                self.slots[0].cache_key,
                selection_seed=self.slots[0].selection_seed + 1,
            ),
        )
        with self.assertRaises(BatchMaterialValidationError) as raised:
            VerifiedBatchMaterial.from_private_json(
                private,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
                planned_slots=(changed_slot, *self.slots[1:]),
            )
        self.assertEqual(raised.exception.code, "planned_material_mismatch")

    def test_rehashed_plan_tampering_and_item_plan_disagreement_fail_closed(self):
        material = self.complete_material()
        private = material.to_private_json()
        first = material.items[0]
        actual_route = next(
            route.procedure_id
            for route in first.routes
            if route.procedure_id is not None
        )
        mutations = (
            ("kind", "under_sampled_core_skill"),
            ("requiredProcedureIds", [actual_route]),
            ("compileSeed", self.slots[0].request.seed + 1),
            ("selectionSeed", self.slots[0].selection_seed + 1),
            ("excludedItemIds", ["item_" + "f" * 32]),
            ("excludedQuestionIds", ["forged-question-id"]),
            ("excludedQuestionSemanticSha256s", ["e" * 64]),
            ("excludedTemplateIds", ["forged-template-id"]),
            ("excludedOperandSignatures", ["d" * 64]),
            ("excludedContextIds", ["forged-context-id"]),
            ("excludedContentIds", ["c" * 64]),
        )
        for field, value in mutations:
            tampered = json.loads(private)
            slot = tampered["planContract"]["slots"][0]
            slot[field] = value
            # Rehash every plan envelope. The item's independent authorization
            # binding must still make this disagree with delivered material.
            self.rehash_slot_contract(slot)
            self.rehash_plan_contract(tampered["planContract"])
            payload = self.rehash_private_payload(tampered)
            with self.subTest(field=field), self.assertRaises(
                BatchMaterialValidationError
            ):
                VerifiedBatchMaterial.from_private_json(
                    payload,
                    compiler=self.verifier.compiler,
                    manifest=self.verifier.manifest,
                )

        plan_receipt = json.loads(private)
        plan_receipt["planContract"]["planSha256"] = "f" * 64
        with self.assertRaises(BatchMaterialValidationError):
            VerifiedBatchMaterial.from_private_json(
                self.rehash_private_payload(plan_receipt),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        item_disagreement = json.loads(private)
        item_disagreement["items"][0]["kind"] = "under_sampled_core_skill"
        with self.assertRaises(BatchMaterialValidationError):
            VerifiedBatchMaterial.from_private_json(
                self.rehash_private_payload(item_disagreement),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

    def test_mixed_live_and_reviewed_sources_round_trip_with_bound_private_proofs(self):
        first_slot = self.slots[0]
        cached_bundle = None
        for offset in range(1, 100):
            candidate = self.bundle_for(
                replace(first_slot.request, seed=first_slot.request.seed + offset),
                generated_at_utc=f"2026-07-11T19:{offset:02d}:00Z",
            )
            if (
                candidate.template_id != self.slots[1].blueprint.template_id
                and candidate.operand_signature != self.slots[1].operand_signature
                and question_semantic_sha256(candidate.blueprint)
                != first_slot.question_semantic_sha256
            ):
                cached_bundle = candidate
                break
        self.assertIsNotNone(cached_bundle)
        assert cached_bundle is not None

        approval_record_sha256 = hashlib.sha256(
            (
                "reviewed-cache-owner-record-v1|"
                + cached_bundle.semantic_content_sha256
            ).encode("ascii")
        ).hexdigest()
        review = ReviewReceipt.approved(
            owner_alias="owner-01",
            reviewed_at_utc="2026-07-11T19:30:00Z",
            approved_semantic_content_sha256=(
                cached_bundle.semantic_content_sha256
            ),
            approval_record_sha256=approval_record_sha256,
        )
        builder = self.builder()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reviewed.sqlite3"
            with ReviewedCache.open_build(
                path,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            ) as cache:
                cache.insert(cached_bundle, review)
            with ReviewedCache.open_learner(
                path,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            ) as cache:
                hit = cache.lookup_reviewed(builder.next_fallback_cache_key())
        self.assertIsNotNone(hit)
        assert hit is not None

        item_ids = tuple(f"item_{index:032x}" for index in range(21, 24))
        accepted_bundles = [cached_bundle]
        builder.accept_reviewed_hit(hit, item_instance_id=item_ids[0])
        for index, slot in enumerate(self.slots[1:], start=1):
            bundle = self.bundle_for(
                slot.request,
                generated_at_utc=f"2026-07-11T20:0{index}:00Z",
            )
            accepted_bundles.append(bundle)
            builder.accept_live(bundle, item_instance_id=item_ids[index])
        material = builder.finalize()

        self.assertEqual(
            tuple(item.source_proof.source_kind for item in material.items),
            ("reviewed_cache", "live_verified", "live_verified"),
        )
        reviewed_proof = material.items[0].source_proof
        self.assertEqual(reviewed_proof.cache_row_sha256, hit.cache_row_sha256)
        self.assertEqual(
            reviewed_proof.review_decision_receipt_sha256,
            review.decision_receipt_sha256,
        )
        self.assertEqual(
            reviewed_proof.approval_record_sha256,
            approval_record_sha256,
        )
        self.assertEqual(
            reviewed_proof.approved_semantic_content_sha256,
            cached_bundle.semantic_content_sha256,
        )
        self.assertEqual(
            material.items[0].event_receipts.cache,
            reviewed_proof.source_proof_sha256,
        )

        private = material.to_private_json()
        restored = VerifiedBatchMaterial.from_private_json(
            private,
            compiler=self.verifier.compiler,
            manifest=self.verifier.manifest,
            expected_context=self.context,
            planned_slots=self.slots,
        )
        self.assertEqual(restored, material)
        persisted_proof = json.loads(private)["items"][0]["sourceProof"]
        self.assertEqual(
            persisted_proof["cacheRowSha256"],
            hit.cache_row_sha256,
        )
        self.assertEqual(
            persisted_proof["reviewDecisionReceiptSha256"],
            review.decision_receipt_sha256,
        )

        live_proof_for_same_bundle = BatchItemSourceProof.live(cached_bundle)
        self.assertNotEqual(
            live_proof_for_same_bundle.source_proof_sha256,
            reviewed_proof.source_proof_sha256,
        )
        all_live = self.builder()
        for bundle, item_id in zip(accepted_bundles, item_ids, strict=True):
            all_live.accept_live(bundle, item_instance_id=item_id)
        all_live_material = all_live.finalize()
        self.assertNotEqual(
            all_live_material.items[0].event_receipts.cache,
            material.items[0].event_receipts.cache,
        )
        self.assertNotEqual(
            all_live_material.batch_material_sha256,
            material.batch_material_sha256,
        )

        public = _canonical_json(material.public_payload()).casefold()
        for forbidden in (
            "sourceproof",
            "reviewed_cache",
            "cacherowsha256",
            "reviewdecisionreceiptsha256",
            hit.cache_row_sha256,
            review.decision_receipt_sha256,
            approval_record_sha256,
            review.owner_alias,
            review.reviewed_at_utc,
        ):
            self.assertNotIn(forbidden.casefold(), public)

        tampered = json.loads(private)
        tampered["items"][0]["sourceProof"]["cacheRowSha256"] = "f" * 64
        unsigned_tampered = dict(tampered)
        unsigned_tampered.pop("batchMaterialSha256")
        tampered["batchMaterialSha256"] = _canonical_sha256(unsigned_tampered)
        with self.assertRaises(BatchMaterialValidationError):
            VerifiedBatchMaterial.from_private_json(
                _canonical_json(tampered),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        with self.assertRaises(ValueError):
            replace(hit, bundle=accepted_bundles[1])

    def test_private_source_proof_tampering_and_live_review_fields_fail_closed(self):
        material = self.complete_material()
        parsed = json.loads(material.to_private_json())
        live_proof = parsed["items"][0]["sourceProof"]
        self.assertEqual(live_proof["sourceKind"], "live_verified")

        live_proof["cacheRowSha256"] = "f" * 64
        unsigned_proof = dict(live_proof)
        unsigned_proof.pop("sourceProofSha256")
        live_proof["sourceProofSha256"] = _canonical_sha256(unsigned_proof)
        unsigned = dict(parsed)
        unsigned.pop("batchMaterialSha256")
        parsed["batchMaterialSha256"] = _canonical_sha256(unsigned)

        with self.assertRaises(BatchMaterialValidationError):
            VerifiedBatchMaterial.from_private_json(
                _canonical_json(parsed),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

    def test_private_parser_rejects_malformed_duplicate_and_tampered_data(self):
        material = self.complete_material()
        private = material.to_private_json()

        duplicate = private.replace(
            '"batchId":"batch-valuehold-001",',
            '"batchId":"batch-valuehold-001","batchId":"batch-valuehold-001",',
            1,
        )
        with self.assertRaises(BatchMaterialValidationError) as duplicate_error:
            VerifiedBatchMaterial.from_private_json(
                duplicate,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )
        self.assertEqual(duplicate_error.exception.code, "invalid_private_payload")

        parsed = json.loads(private)
        parsed["items"][0]["bundle"]["blueprint"]["prompt"] = "Tampered prompt"
        unsigned = dict(parsed)
        unsigned.pop("batchMaterialSha256")
        parsed["batchMaterialSha256"] = _canonical_sha256(unsigned)
        with self.assertRaises(BatchMaterialValidationError):
            VerifiedBatchMaterial.from_private_json(
                _canonical_json(parsed),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        malformed_item = json.loads(private)
        malformed_item["items"][0]["itemInstanceId"] = "item-invalid"
        unsigned_item = dict(malformed_item)
        unsigned_item.pop("batchMaterialSha256")
        malformed_item["batchMaterialSha256"] = _canonical_sha256(unsigned_item)
        with self.assertRaises(BatchMaterialValidationError):
            VerifiedBatchMaterial.from_private_json(
                _canonical_json(malformed_item),
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
            )

        first_bundle = material.items[0].bundle
        omitted_by_first = next(
            procedure_id
            for procedure_id in first_bundle.blueprint.allowed_procedure_ids
            if procedure_id
            not in {
                route.procedure_id for route in first_bundle.verified_distractors
            }
        )
        conflicting_plan = (
            self.slot_with_required_route(self.slots[0], omitted_by_first),
            *self.slots[1:],
        )
        with self.assertRaises(BatchMaterialValidationError):
            VerifiedBatchMaterial.from_private_json(
                private,
                compiler=self.verifier.compiler,
                manifest=self.verifier.manifest,
                expected_context=self.context,
                planned_slots=conflicting_plan,
            )

        malformed_cases = (
            "[]",
            private + " ",
            private.replace('"schemaVersion"', '"unknownField"', 1),
            private.replace(material.batch_material_sha256, "f" * 64, 1),
        )
        for payload in malformed_cases:
            with self.subTest(payload=payload[:32]):
                with self.assertRaises(BatchMaterialValidationError):
                    VerifiedBatchMaterial.from_private_json(
                        payload,
                        compiler=self.verifier.compiler,
                        manifest=self.verifier.manifest,
                    )

        for field, forged in (
            (
                "excludedContextIds",
                [material.items[0].bundle.context_id],
            ),
            (
                "excludedQuestionSemanticSha256s",
                [material.items[0].question_semantic_sha256],
            ),
        ):
            tampered = json.loads(private)
            tampered["items"][0][field] = forged
            unsigned_tampered = dict(tampered)
            unsigned_tampered.pop("batchMaterialSha256")
            tampered["batchMaterialSha256"] = _canonical_sha256(
                unsigned_tampered
            )
            with self.subTest(tampered_field=field), self.assertRaises(
                BatchMaterialValidationError
            ):
                VerifiedBatchMaterial.from_private_json(
                    _canonical_json(tampered),
                    compiler=self.verifier.compiler,
                    manifest=self.verifier.manifest,
                )

    def test_observation_metadata_and_routes_are_bound_to_exact_placement(self):
        material = self.complete_material()
        item, final, event = self.revealed_fixture(material)

        material.validate_observation(
            event,
            final,
            observation_session_id=event.session_id,
        )

        other_wrong = next(
            route
            for route in item.routes
            if route.procedure_id is not None
            and route.option_id != event.first_option_id
        )
        altered_events = (
            replace(event, question_id="forged-question-id"),
            replace(event, template_id="forged-template-id"),
            replace(event, content_version_id="forged-content-version"),
            replace(event, context_id="forged-context-id"),
            replace(event, operand_signature="f" * 64),
            replace(event, skill_id="forged-skill-id"),
            replace(event, world_id="forged-world-id"),
            replace(event, session_id="session-forged-002"),
            replace(event, world_core_subskill_ids=("forged-skill-id",)),
            replace(event, targeted_procedure_ids=("forged-procedure",)),
            replace(event, is_transfer=True),
            replace(event, is_changed_context_transfer=True),
            replace(event, valid_for_progression=False),
            replace(event, canonical_feedback=("Forged feedback",)),
            replace(event, first_procedure_id="forged-procedure"),
            replace(event, first_option_id=other_wrong.option_id),
        )
        for altered in altered_events:
            with self.subTest(field=altered):
                with self.assertRaises(BatchMaterialValidationError):
                    material.validate_observation(
                        altered,
                        final,
                        observation_session_id=event.session_id,
                    )

        for receipt_field in fields(event.receipts):
            altered_receipts = replace(
                event.receipts,
                **{receipt_field.name: f"forged-{receipt_field.name}"},
            )
            with self.subTest(receipt=receipt_field.name):
                with self.assertRaises(BatchMaterialValidationError):
                    material.validate_observation(
                        replace(event, receipts=altered_receipts),
                        final,
                        observation_session_id=event.session_id,
                    )

        wrong_method = replace(final, reliable_method="Forged reliable method")
        wrong_steps = replace(final, trusted_steps=("Forged trusted step",))
        for altered_final in (wrong_method, wrong_steps):
            with self.assertRaises(BatchMaterialValidationError):
                material.validate_observation(
                    event,
                    altered_final,
                    observation_session_id=event.session_id,
                )

        family_methods = {
            self.verifier.registry.reliable_method(procedure_id)
            for procedure_id in item.bundle.blueprint.allowed_procedure_ids
        }
        self.assertEqual(len(family_methods), 1)
        self.assertEqual(final.reliable_method, next(iter(family_methods)))

    def test_observation_accepts_an_explicit_later_reveal_session(self):
        material = self.complete_material()
        _, final, event = self.revealed_fixture(material)
        reveal_session_id = "session-owner-resumed-002"
        resumed_event = replace(event, session_id=reveal_session_id)

        material.validate_observation(
            resumed_event,
            final,
            observation_session_id=reveal_session_id,
        )

    def test_final_feedback_is_bound_to_the_route_selected_by_scoring(self):
        material = self.complete_material()
        item, final, event = self.revealed_fixture(material)
        alternate = next(
            route
            for route in item.routes
            if route.procedure_id is not None
            and route.option_id != event.first_option_id
        )
        self.assertNotEqual(alternate.feedback, final.possible_error)
        forged_final = replace(final, possible_error=alternate.feedback)
        forged_event = replace(
            event,
            canonical_feedback=(alternate.feedback, final.reliable_method),
        )

        with self.assertRaises(BatchMaterialValidationError) as raised:
            material.validate_observation(
                forged_event,
                forged_final,
                observation_session_id=event.session_id,
            )

        self.assertEqual(raised.exception.code, "final_feedback_mismatch")

    def test_prior_world_transfer_binds_content_world_and_transfer_flags(self):
        intents = (
            SlotIntent(
                kind="spaced_prior_world_transfer",
                campaign_world_id="valuehold",
                content_world_id="decimara",
                skill_id="decimal_add_sub",
            ),
            SlotIntent(
                kind="novel_current_skill",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            ),
            SlotIntent(
                kind="novel_current_skill",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="mental_add_sub",
            ),
        )
        slots = materialize_slots(
            intents,
            "route_1",
            20260712,
            self.verifier.compiler,
        )
        builder = self.builder(slots)
        for index, slot in enumerate(slots, start=11):
            builder.accept_live(
                self.bundle_for(
                    slot.request,
                    generated_at_utc=f"2026-07-11T18:{index:02d}:00Z",
                ),
                item_instance_id=f"item_{index:032x}",
            )
        material = builder.finalize()
        item, final, event = self.revealed_fixture(material)

        self.assertEqual(item.campaign_world_id, "valuehold")
        self.assertEqual(item.bundle.blueprint.world_id, "decimara")
        self.assertEqual(event.world_id, "decimara")
        self.assertTrue(event.is_transfer)
        self.assertFalse(event.is_changed_context_transfer)
        self.assertTrue(event.valid_for_progression)
        material.validate_observation(
            event,
            final,
            observation_session_id=event.session_id,
        )

        with self.assertRaises(BatchMaterialValidationError):
            material.validate_observation(
                replace(event, world_id=self.context.world_id),
                final,
                observation_session_id=event.session_id,
            )

    def test_fragile_transfer_rejects_prior_context_and_marks_only_actual_change(self):
        intents = (
            SlotIntent(
                kind="fragile_skill_transfer",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
                excluded_context_ids=("route_counter",),
            ),
            SlotIntent(
                kind="novel_current_skill",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            ),
            SlotIntent(
                kind="novel_current_skill",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            ),
        )
        slots = materialize_slots(
            intents,
            "route_1",
            20260713,
            self.verifier.compiler,
        )
        builder = self.builder(slots)
        self.assertEqual(
            builder.next_fallback_cache_key().excluded_context_ids,
            ("route_counter",),
        )
        unchanged = None
        family = self.verifier.compiler.curriculum.families["place_value"]
        context_by_template = {
            template.template_id: template.context_id
            for template in family.templates
        }
        for offset in range(1, 100):
            request = replace(slots[0].request, seed=slots[0].request.seed + offset)
            blueprint = self.verifier.compiler.compile(request)
            if context_by_template[blueprint.template_id] == "route_counter":
                unchanged = self.bundle_for(request)
                break
        self.assertIsNotNone(unchanged)

        with self.assertRaises(RetryableBatchMaterialRejection) as raised:
            builder.accept_live(unchanged, item_instance_id="item_" + "a" * 32)
        self.assertEqual(raised.exception.code, "excluded_context")
        self.assertEqual(builder.accepted_count, 0)

        changed = self.bundle_for(slots[0].request)
        self.assertNotIn(changed.context_id, slots[0].excluded_context_ids)
        builder.accept_live(changed, item_instance_id="item_" + "b" * 32)
        first = builder.items[0]
        self.assertEqual(first.excluded_context_ids, ("route_counter",))
        self.assertTrue(first.is_changed_context_transfer)

        for index, slot in enumerate(slots[1:], start=12):
            builder.accept_live(
                self.bundle_for(slot.request),
                item_instance_id=f"item_{index:032x}",
            )
        material = builder.finalize()
        self.assertFalse(material.items[1].is_changed_context_transfer)
        self.assertFalse(material.items[2].is_changed_context_transfer)
        with self.assertRaises(BatchMaterialError):
            builder.next_fallback_cache_key()

    def test_non_fragile_item_never_claims_changed_context_with_a_baseline(self):
        bundle = self.bundle_for(self.slots[0].request)
        other_context = (
            "survey_marker"
            if bundle.context_id == "route_counter"
            else "route_counter"
        )
        first = replace(
            self.slots[0],
            excluded_context_ids=(other_context,),
            cache_key=replace(
                self.slots[0].cache_key,
                excluded_context_ids=(other_context,),
            ),
        )
        builder = self.builder((first, *self.slots[1:]))

        builder.accept_live(bundle, item_instance_id="item_" + "c" * 32)

        item = builder.items[0]
        self.assertEqual(item.excluded_context_ids, (other_context,))
        self.assertNotIn(item.bundle.context_id, item.excluded_context_ids)
        self.assertFalse(item.is_changed_context_transfer)


if __name__ == "__main__":
    unittest.main()
