from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from services.wayline_forge.app.adaptive_planner import SlotIntent
from services.wayline_forge.app.batch_material import (
    BatchContext,
    BatchMaterialBuilder,
    VerifiedBatchMaterial,
)
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.events import ObservationEvent
from services.wayline_forge.app.question_kernel import CompileRequest
from services.wayline_forge.app.quiz_machine import (
    QuizItemLayout,
    QuizMachine,
    QuizSelection,
    QuizSubmission,
    TransitionReceipt,
    mark_ready,
    new_quiz,
    submit_initial,
    submit_revision,
)
from services.wayline_forge.app.quiz_observations import (
    QuizObservationError,
    build_reveal_observations,
)
from services.wayline_forge.app.slot_materializer import (
    MaterializedSlot,
    materialize_slots,
)
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class QuizObservationBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = DistractorVerifier.for_tests()
        cls.context = BatchContext(
            profile_id="profile-owner-001",
            session_id="session-prepared-001",
            world_id="valuehold",
            battle_id="battle-valuehold-001",
            core_subskill_ids=("place_value", "mental_add_sub"),
            content_version_id=cls.verifier.compiler.curriculum.curriculum_id,
            battle_tier="route_1",
        )
        cls.material = cls._material(
            (
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
                SlotIntent(
                    kind="novel_current_skill",
                    campaign_world_id="valuehold",
                    content_world_id="valuehold",
                    skill_id="place_value",
                ),
            ),
            seed=20260711,
            batch_id="batch-valuehold-001",
            item_offset=1,
        )
        cls.transfer_material = cls._material(
            (
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
            ),
            seed=20260712,
            batch_id="batch-transfer-001",
            item_offset=11,
        )

    @classmethod
    def _bundle(cls, request: CompileRequest, generated_minute: int) -> VerifiedQuestionBundle:
        blueprint = cls.verifier.compiler.compile(request)
        selected = blueprint.allowed_procedure_ids[:3]
        distractors = [
            {
                "misconception": cls.verifier.registry.canonical_label(procedure_id),
                "computation": cls.verifier.registry.canonical_computation(
                    procedure_id,
                    blueprint,
                ),
                "answer": cls.verifier.registry.evaluate(
                    procedure_id,
                    blueprint,
                ).display,
            }
            for procedure_id in selected
        ]
        generation = replace(
            cls.verifier.fixture_generation(blueprint, "accepted.json"),
            text=_canonical_json({"distractors": distractors}),
            generated_at_utc=f"2026-07-11T18:{generated_minute:02d}:00Z",
        )
        verified = cls.verifier.verify_generation(blueprint, generation)
        if not verified.accepted or verified.value is None:
            raise AssertionError(verified.code)
        return VerifiedQuestionBundle.from_verified(
            compiler=cls.verifier.compiler,
            request=request,
            blueprint=blueprint,
            verified=verified.value,
            generation=generation,
            manifest=cls.verifier.manifest,
        )

    @classmethod
    def _material(
        cls,
        intents: tuple[SlotIntent, ...],
        *,
        seed: int,
        batch_id: str,
        item_offset: int,
    ) -> VerifiedBatchMaterial:
        slots = materialize_slots(
            intents,
            "route_1",
            seed,
            cls.verifier.compiler,
        )
        builder = BatchMaterialBuilder(
            batch_id=batch_id,
            context=cls.context,
            planned_slots=slots,
        )
        for index, slot in enumerate(slots, start=item_offset):
            builder.accept_live(
                cls._bundle(slot.request, index),
                item_instance_id=f"item_{index:032x}",
            )
        return builder.finalize()

    @staticmethod
    def _layouts(material: VerifiedBatchMaterial) -> tuple[QuizItemLayout, ...]:
        return tuple(
            QuizItemLayout(
                item_id=item.item_id,
                option_ids=tuple(option.option_id for option in item.options),
            )
            for item in material.public_batch.items
        )

    @classmethod
    def _submission(
        cls,
        material: VerifiedBatchMaterial,
        request_id: str,
        option_indexes: tuple[int, int, int],
        confidences: tuple[str, str, str],
    ) -> QuizSubmission:
        layouts = cls._layouts(material)
        return QuizSubmission(
            schema_version="wayline.v1",
            request_id=request_id,
            batch_id=material.batch_id,
            item_count=len(layouts),
            selections=tuple(
                QuizSelection(
                    item_id=layout.item_id,
                    option_id=layout.option_ids[option_index],
                    confidence=confidence,
                )
                for layout, option_index, confidence in zip(
                    layouts,
                    option_indexes,
                    confidences,
                    strict=True,
                )
            ),
        )

    @classmethod
    def _option_indexes(
        cls,
        material: VerifiedBatchMaterial,
        *,
        correct: bool,
        alternate_wrong: bool = False,
    ) -> tuple[int, int, int]:
        indexes: list[int] = []
        for item, layout in zip(
            material.items,
            cls._layouts(material),
            strict=True,
        ):
            correct_index = layout.option_ids.index(item.placement.correct_option_id)
            if correct:
                indexes.append(correct_index)
                continue
            wrong_indexes = tuple(
                index for index in range(4) if index != correct_index
            )
            indexes.append(wrong_indexes[1 if alternate_wrong else 0])
        return tuple(indexes)  # type: ignore[return-value]

    @classmethod
    def _ready(cls, material: VerifiedBatchMaterial) -> QuizMachine:
        preparing = new_quiz(material.batch_id, cls._layouts(material))
        return mark_ready(
            preparing,
            sealed_quiz=material.sealed_quiz,
            expected_version=preparing.version,
        )

    @classmethod
    def _zero_wrong_reveal(
        cls,
        material: VerifiedBatchMaterial | None = None,
    ) -> tuple[VerifiedBatchMaterial, QuizMachine, TransitionReceipt]:
        material = material or cls.material
        ready = cls._ready(material)
        initial = submit_initial(
            ready,
            cls._submission(
                material,
                "initial-zero-001",
                cls._option_indexes(material, correct=True),
                ("certain", "leaning", "guessing"),
            ),
            material.sealed_quiz,
            expected_version=ready.version,
        )
        return material, initial.machine, initial.receipt

    @classmethod
    def _revised_reveal(
        cls,
        material: VerifiedBatchMaterial | None = None,
    ) -> tuple[VerifiedBatchMaterial, QuizMachine, TransitionReceipt]:
        material = material or cls.material
        ready = cls._ready(material)
        correct = cls._option_indexes(material, correct=True)
        wrong = cls._option_indexes(material, correct=False)
        alternate_wrong = cls._option_indexes(
            material,
            correct=False,
            alternate_wrong=True,
        )
        initial_indexes = (wrong[0], correct[1], wrong[2])
        revision_indexes = (correct[0], wrong[1], alternate_wrong[2])
        initial = submit_initial(
            ready,
            cls._submission(
                material,
                "initial-revision-001",
                initial_indexes,
                ("guessing", "certain", "leaning"),
            ),
            material.sealed_quiz,
            expected_version=ready.version,
        )
        revision = submit_revision(
            initial.machine,
            cls._submission(
                material,
                "revision-001",
                revision_indexes,
                ("certain", "leaning", "guessing"),
            ),
            material.sealed_quiz,
            expected_version=initial.machine.version,
        )
        return material, revision.machine, revision.receipt

    @staticmethod
    def _build(
        material: VerifiedBatchMaterial,
        machine: QuizMachine,
        receipt: TransitionReceipt,
        **changes: object,
    ) -> tuple[ObservationEvent, ...]:
        arguments: dict[str, object] = {
            "profile_id": material.context.profile_id,
            "reveal_session_id": "session-revealed-002",
            "first_ordinal": 41,
            "occurred_at": "2026-07-11T20:30:00.123456Z",
        }
        arguments.update(changes)
        return build_reveal_observations(
            material,
            machine,
            receipt,
            **arguments,  # type: ignore[arg-type]
        )

    def test_zero_wrong_initial_reveal_binds_every_field_in_material_order(self) -> None:
        material, machine, receipt = self._zero_wrong_reveal()

        events = self._build(material, machine, receipt)

        self.assertIsInstance(events, tuple)
        self.assertEqual(len(events), len(material.items))
        self.assertEqual(tuple(event.ordinal for event in events), (41, 42, 43))
        self.assertEqual(
            tuple(event.item_id for event in events),
            tuple(item.item_id for item in material.items),
        )
        for item, result, event in zip(
            material.items,
            machine.final_result.items,  # type: ignore[union-attr]
            events,
            strict=True,
        ):
            self.assertEqual(event.schema_version, "wayline.event.v1")
            self.assertEqual(event.profile_id, material.context.profile_id)
            self.assertEqual(event.session_id, "session-revealed-002")
            self.assertEqual(event.world_id, item.bundle.blueprint.world_id)
            self.assertEqual(event.battle_id, material.context.battle_id)
            self.assertEqual(event.occurred_at, "2026-07-11T20:30:00.123456Z")
            self.assertEqual(event.batch_id, material.batch_id)
            self.assertEqual(event.question_id, item.bundle.blueprint.question_id)
            self.assertEqual(event.template_id, item.bundle.template_id)
            self.assertEqual(
                event.content_version_id,
                material.context.content_version_id,
            )
            self.assertEqual(event.skill_id, item.bundle.blueprint.skill_id)
            self.assertEqual(
                event.world_core_subskill_ids,
                material.context.core_subskill_ids,
            )
            self.assertEqual(event.operand_signature, item.bundle.operand_signature)
            self.assertEqual(event.context_id, item.bundle.context_id)
            self.assertEqual(event.first_option_id, result.first_selection.option_id)
            self.assertEqual(event.final_option_id, result.final_selection.option_id)
            self.assertEqual(event.first_confidence, result.first_selection.confidence)
            self.assertEqual(event.final_confidence, result.final_selection.confidence)
            self.assertTrue(event.first_correct)
            self.assertTrue(event.final_correct)
            self.assertFalse(event.choice_changed)
            self.assertFalse(event.self_corrected)
            self.assertIsNone(event.first_procedure_id)
            self.assertIsNone(event.final_procedure_id)
            self.assertEqual(event.targeted_procedure_ids, item.required_procedure_ids)
            self.assertEqual(event.is_transfer, item.is_transfer)
            self.assertEqual(
                event.is_changed_context_transfer,
                item.is_changed_context_transfer,
            )
            self.assertEqual(event.valid_for_progression, item.valid_for_progression)
            self.assertEqual(event.batch_wrong_count, 0)
            self.assertEqual(event.canonical_feedback, (result.reliable_method,))
            self.assertIsNone(event.optional_wording_shown)
            self.assertEqual(event.receipts, item.event_receipts)
            material.validate_observation(
                event,
                result,
                observation_session_id="session-revealed-002",
            )
        with self.assertRaises(FrozenInstanceError):
            events[0].ordinal = 99  # type: ignore[misc]

    def test_revision_reveal_uses_only_verified_routes_and_canonical_feedback(self) -> None:
        material, machine, receipt = self._revised_reveal()

        events = self._build(material, machine, receipt)

        self.assertEqual(tuple(event.batch_wrong_count for event in events), (2, 2, 2))
        for item, result, event in zip(
            material.items,
            machine.final_result.items,  # type: ignore[union-attr]
            events,
            strict=True,
        ):
            first_route = item.route_for_option(result.first_selection.option_id)
            final_route = item.route_for_option(result.final_selection.option_id)
            self.assertEqual(event.first_procedure_id, first_route.procedure_id)
            self.assertEqual(event.final_procedure_id, final_route.procedure_id)
            self.assertEqual(
                event.canonical_feedback,
                tuple(
                    value
                    for value in (result.possible_error, result.reliable_method)
                    if value is not None
                ),
            )
            self.assertEqual(
                event.choice_changed,
                result.first_selection.option_id != result.final_selection.option_id,
            )
            self.assertEqual(event.self_corrected, result.self_corrected)
        self.assertTrue(events[0].self_corrected)
        self.assertIsNone(events[0].final_procedure_id)
        self.assertIsNone(events[1].first_procedure_id)
        self.assertIsNotNone(events[1].final_procedure_id)
        self.assertIsNotNone(events[2].first_procedure_id)
        self.assertIsNotNone(events[2].final_procedure_id)

    def test_resumed_reveal_uses_actual_session_not_preparation_session(self) -> None:
        material, machine, receipt = self._zero_wrong_reveal()
        self.assertNotEqual(material.context.session_id, "session-resumed-009")

        events = self._build(
            material,
            machine,
            receipt,
            reveal_session_id="session-resumed-009",
        )

        self.assertEqual(
            {event.session_id for event in events},
            {"session-resumed-009"},
        )

    def test_prior_world_transfer_uses_content_world_not_campaign_world(self) -> None:
        material, machine, receipt = self._zero_wrong_reveal(self.transfer_material)

        events = self._build(material, machine, receipt)

        self.assertEqual(material.context.world_id, "valuehold")
        self.assertEqual(material.items[0].campaign_world_id, "valuehold")
        self.assertEqual(material.items[0].bundle.blueprint.world_id, "decimara")
        self.assertEqual(events[0].world_id, "decimara")
        self.assertEqual(events[0].battle_id, material.context.battle_id)
        self.assertTrue(events[0].is_transfer)
        self.assertFalse(events[0].is_changed_context_transfer)

    def test_rebuild_is_deterministic_and_ids_ignore_clock_and_resume_session(self) -> None:
        material, machine, receipt = self._revised_reveal()

        first = self._build(material, machine, receipt)
        rebuilt = self._build(material, machine, receipt)
        later = self._build(
            material,
            machine,
            receipt,
            reveal_session_id="session-resumed-010",
            occurred_at="2026-07-11T21:30:00Z",
        )

        self.assertEqual(first, rebuilt)
        self.assertEqual(
            tuple((event.event_id, event.idempotency_id) for event in first),
            tuple((event.event_id, event.idempotency_id) for event in later),
        )
        self.assertEqual(len({event.event_id for event in first}), len(first))
        self.assertEqual(len({event.idempotency_id for event in first}), len(first))
        for event in first:
            self.assertRegex(event.event_id, r"^obs-[0-9a-f]{64}$")
            self.assertRegex(event.idempotency_id, r"^obs-idem-[0-9a-f]{64}$")

    def test_receipt_must_be_exact_and_action_must_match_reveal_path(self) -> None:
        material, machine, receipt = self._zero_wrong_reveal()
        mutations = (
            replace(receipt, action="revision"),
            replace(receipt, request_id="initial-other-001"),
            replace(receipt, receipt_sha256="0" * 64),
        )

        for altered in mutations:
            with self.subTest(altered=altered):
                with self.assertRaises(QuizObservationError):
                    self._build(material, machine, altered)

        revised_material, revised_machine, revised_receipt = self._revised_reveal()
        with self.assertRaises(QuizObservationError):
            self._build(
                revised_material,
                revised_machine,
                replace(revised_receipt, action="initial"),
            )

    def test_profile_session_timestamp_and_ordinal_are_strict(self) -> None:
        material, machine, receipt = self._zero_wrong_reveal()
        invalid_arguments = (
            {"profile_id": "profile-other-001"},
            {"profile_id": "bad id"},
            {"reveal_session_id": "x"},
            {"reveal_session_id": "session with spaces"},
            {"occurred_at": "2026-07-11T20:30:00+00:00"},
            {"occurred_at": "2026-02-30T20:30:00Z"},
            {"occurred_at": "2026-07-11T20:30:00.123Z"},
            {"first_ordinal": 0},
            {"first_ordinal": True},
            {"first_ordinal": "41"},
        )

        for changes in invalid_arguments:
            with self.subTest(changes=changes):
                with self.assertRaises(QuizObservationError):
                    self._build(material, machine, receipt, **changes)

    def test_machine_material_layout_seal_and_final_result_are_replayed_exactly(self) -> None:
        material, machine, receipt = self._revised_reveal()
        final = machine.final_result
        self.assertIsNotNone(final)
        first_result = final.items[0]  # type: ignore[union-attr]
        alternate = next(
            route
            for route in material.items[0].routes
            if route.procedure_id is not None
            and route.option_id != first_result.final_selection.option_id
        )
        altered_feedback = replace(
            first_result,
            possible_error="forged route feedback",
        )
        altered_route = replace(
            first_result,
            final_selection=replace(
                first_result.final_selection,
                option_id=alternate.option_id,
            ),
        )
        altered_machines = (
            replace(machine, final_result=replace(final, final_correct_count=3)),
            replace(
                machine,
                final_result=replace(
                    final,
                    items=(altered_feedback, *final.items[1:]),
                ),
            ),
            replace(
                machine,
                final_result=replace(
                    final,
                    items=(altered_route, *final.items[1:]),
                ),
            ),
            replace(
                machine,
                item_layouts=(
                    replace(
                        machine.item_layouts[0],
                        option_ids=tuple(reversed(machine.item_layouts[0].option_ids)),
                    ),
                    *machine.item_layouts[1:],
                ),
            ),
            replace(machine, sealed_quiz_sha256="0" * 64),
        )

        for altered_machine in altered_machines:
            with self.subTest(altered_machine=altered_machine):
                with self.assertRaises(QuizObservationError):
                    self._build(material, altered_machine, receipt)

        other_material, _, _ = self._zero_wrong_reveal(self.transfer_material)
        with self.assertRaises(QuizObservationError):
            self._build(other_material, machine, receipt)

    def test_only_exact_production_input_types_and_revealed_state_are_accepted(self) -> None:
        material, machine, receipt = self._zero_wrong_reveal()
        ready = self._ready(material)

        with self.assertRaises(QuizObservationError):
            build_reveal_observations(  # type: ignore[arg-type]
                object(),
                machine,
                receipt,
                profile_id=material.context.profile_id,
                reveal_session_id="session-revealed-002",
                first_ordinal=1,
                occurred_at="2026-07-11T20:30:00Z",
            )
        with self.assertRaises(QuizObservationError):
            self._build(material, ready, receipt)
        with self.assertRaises(QuizObservationError):
            build_reveal_observations(  # type: ignore[arg-type]
                material,
                machine,
                object(),
                profile_id=material.context.profile_id,
                reveal_session_id="session-revealed-002",
                first_ordinal=1,
                occurred_at="2026-07-11T20:30:00Z",
            )


if __name__ == "__main__":
    unittest.main()
