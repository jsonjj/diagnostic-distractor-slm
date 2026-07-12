from __future__ import annotations

from dataclasses import replace
import json
import unittest

from services.wayline_forge.app.adaptive_planner import SlotIntent
from services.wayline_forge.app.assisted_route_machine import (
    AssistedRouteMachineError,
    public_assisted_batch,
    score_assisted_route,
)
from services.wayline_forge.app.batch_material import (
    BatchContext,
    BatchMaterialBuilder,
)
from services.wayline_forge.app.contracts import AssistedSelection
from services.wayline_forge.app.distractor_verifier import DistractorVerifier
from services.wayline_forge.app.slot_materializer import materialize_slots
from services.wayline_forge.app.verified_question import VerifiedQuestionBundle


class AssistedRouteMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = DistractorVerifier.for_tests()

    def _bundle(self, request: object) -> VerifiedQuestionBundle:
        blueprint = self.verifier.compiler.compile(request)
        procedure_ids = blueprint.allowed_procedure_ids[:3]
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
            for procedure_id in procedure_ids
        )
        generation = replace(
            self.verifier.fixture_generation(blueprint, "accepted.json"),
            text=json.dumps(
                {"distractors": distractors},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        verified = self.verifier.verify_generation(blueprint, generation)
        self.assertTrue(verified.accepted, verified.code)
        self.assertIsNotNone(verified.value)
        return VerifiedQuestionBundle.from_verified(
            compiler=self.verifier.compiler,
            request=request,
            blueprint=blueprint,
            verified=verified.value,
            generation=generation,
            manifest=self.verifier.manifest,
        )

    def _material(
        self,
        *,
        tier: str = "assisted_route",
        profile_id: str = "profile-assisted-001",
        session_id: str = "session-assisted-001",
    ):
        if tier == "assisted_route":
            kinds = (
                "assisted_worked_example",
                "assisted_supported_mcq",
                "assisted_supported_mcq",
            )
        else:
            kinds = ("novel_current_skill",) * 3
        intents = tuple(
            SlotIntent(
                kind=kind,
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            )
            for kind in kinds
        )
        slots = materialize_slots(intents, tier, 20260712, self.verifier.compiler)
        context = BatchContext(
            profile_id=profile_id,
            session_id=session_id,
            world_id="valuehold",
            battle_id="valuehold_assisted_route",
            core_subskill_ids=("place_value", "mental_add_sub"),
            content_version_id=self.verifier.compiler.curriculum.curriculum_id,
            battle_tier=tier,
        )
        builder = BatchMaterialBuilder(
            batch_id="batch-assisted-internal-001",
            context=context,
            planned_slots=slots,
        )
        for index, slot in enumerate(slots, start=1):
            builder.accept_live(
                self._bundle(slot.request),
                item_instance_id=f"item_{index + 200:032x}",
            )
        return builder.finalize()

    def test_public_projection_is_one_worked_example_and_two_keyless_mcqs(self):
        material = self._material()

        public = public_assisted_batch("assisted-route-001", material)

        self.assertEqual(public.worked_example.item_id, material.items[0].item_id)
        self.assertEqual(
            tuple(item.item_id for item in public.items),
            tuple(item.item_id for item in material.items[1:]),
        )
        self.assertTrue(public.worked_example.correct_answer)
        self.assertEqual(
            set(public.worked_example.model_dump(by_alias=True)),
            {
                "itemId",
                "prompt",
                "correctAnswer",
                "trustedSteps",
                "reliableMethod",
            },
        )
        for item in public.items:
            payload = item.model_dump(by_alias=True)
            self.assertEqual(set(payload), {"itemId", "prompt", "options"})
            self.assertTrue(all(
                set(option) == {"optionId", "displayText"}
                for option in payload["options"]
            ))

    def test_scoring_uses_only_sealed_supported_truth(self):
        material = self._material()
        public = public_assisted_batch("assisted-route-001", material)
        selections = tuple(
            AssistedSelection(
                itemId=item.item_id,
                optionId=next(
                    route.option_id
                    for route in material.items[index].routes
                    if route.procedure_id is not None
                ),
                confidence="leaning",
            )
            for index, item in enumerate(public.items, start=1)
        )

        score = score_assisted_route(
            "assisted-route-001",
            material,
            selections,
        )

        self.assertEqual(score.final_correct, 0)
        self.assertEqual(len(score.items), 2)
        self.assertTrue(all(item.possible_error for item in score.items))
        self.assertEqual(
            score.items[0].canonical_feedback,
            (
                score.items[0].possible_error,
                score.items[0].reliable_method,
                *score.items[0].trusted_steps,
            ),
        )
        self.assertTrue(all(score.selected_procedure_ids))
        self.assertEqual(score.material_sha256, material.batch_material_sha256)

    def test_scoring_rejects_wrong_order_duplicate_and_forged_option(self):
        material = self._material()
        public = public_assisted_batch("assisted-route-001", material)
        valid = tuple(
            AssistedSelection(
                itemId=item.item_id,
                optionId=item.options[0].option_id,
                confidence="certain",
            )
            for item in public.items
        )

        for selections in (
            tuple(reversed(valid)),
            (valid[0], valid[0]),
            (
                valid[0],
                AssistedSelection(
                    itemId=valid[1].item_id,
                    optionId="forged-option-001",
                    confidence="certain",
                ),
            ),
        ):
            with self.subTest(selections=selections), self.assertRaises(
                AssistedRouteMachineError
            ):
                score_assisted_route(
                    "assisted-route-001",
                    material,
                    selections,
                )

    def test_normal_quiz_material_is_not_an_assisted_route(self):
        with self.assertRaises(AssistedRouteMachineError) as raised:
            public_assisted_batch(
                "assisted-route-001",
                self._material(tier="route_1"),
            )

        self.assertEqual(raised.exception.code, "material_context_mismatch")


if __name__ == "__main__":
    unittest.main()
