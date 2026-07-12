from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from services.wayline_forge.app.adaptive_planner import (
    plan_assisted_slots,
    plan_slots,
)
from services.wayline_forge.app.boss_gate import evaluate_world_clear
from services.wayline_forge.app.evidence_reducer import reduce_events
from services.wayline_forge.tests.fixtures import event


class AdaptivePlannerTests(unittest.TestCase):
    def test_prior_world_transfer_does_not_change_active_campaign_world(self):
        state = reduce_events([
            event.activate(ordinal=1, world="valuehold"),
            event.correct(
                ordinal=2,
                world="decimara",
                skill="decimal_add_sub",
                core_subskills=("decimal_add_sub",),
                transfer=True,
            ),
        ])

        slots = plan_slots(state, "route_1")

        self.assertTrue(all(slot.campaign_world_id == "valuehold" for slot in slots))
        prior = next(slot for slot in slots if slot.kind == "spaced_prior_world_transfer")
        self.assertEqual(prior.content_world_id, "decimara")
        self.assertEqual(prior.world_id, "decimara")

    def test_quiz_lengths_are_fixed_by_battle_tier(self):
        state = reduce_events([event.activate(ordinal=1), event.correct(ordinal=2)])
        expected = {
            "route_1": 3,
            "route_2": 4,
            "route_3": 4,
            "elite": 5,
            "world_boss": 8,
            "campaign_finale": 10,
            "seal_trial": 3,
        }

        for tier, length in expected.items():
            with self.subTest(tier=tier):
                self.assertEqual(len(plan_slots(state, tier)), length)

    def test_weakness_changes_content_not_quiz_length(self):
        weak = reduce_events([
            event.activate(ordinal=1),
            event.wrong("align_by_ends", ordinal=2, confidence="certain"),
        ])
        strong = reduce_events([
            event.activate(ordinal=1),
            *(
                event.correct(ordinal=i, question=f"q-{i}", template=f"t-{i}")
                for i in range(2, 5)
            ),
        ])

        self.assertEqual(len(plan_slots(weak, "elite")), 5)
        self.assertEqual(len(plan_slots(strong, "elite")), 5)

    def test_active_misconception_probe_has_highest_priority(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.wrong("align_by_ends", ordinal=2, template="a"),
            event.wrong("align_by_ends", ordinal=3, template="b"),
            event.wrong(
                "ignore_decimal",
                ordinal=4,
                template="c",
                final_procedure="place_shift",
                keep_wrong=True,
            ),
            event.correct(
                ordinal=5,
                skill="mental_add_sub",
                confidence="guessing",
            ),
        ])

        first = plan_slots(state, "route_1")[0]

        self.assertEqual(first.kind, "active_misconception_probe")
        self.assertEqual(first.procedure_ids, ("align_by_ends",))

    def test_resolved_ambiguous_pair_is_not_scheduled_again(self):
        events = [
            event.activate(ordinal=1),
            event.wrong(
                "align_by_ends",
                ordinal=2,
                final_procedure="ignore_decimal",
                keep_wrong=True,
            ),
        ]
        events.extend(
            event.correct(
                ordinal=ordinal,
                question=f"resolved-{ordinal}",
                template=f"resolved-template-{ordinal}",
                batch="resolution-a" if ordinal < 5 else "resolution-b",
                transfer=True,
                changed_context=True,
                targeted_procedures=("align_by_ends", "ignore_decimal"),
            )
            for ordinal in range(3, 6)
        )

        slots = plan_slots(reduce_events(events), "route_1")

        self.assertNotIn("misconception_discrimination", {slot.kind for slot in slots})

    def test_wrong_answer_switch_schedules_discrimination_between_both_routes(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.wrong(
                "align_by_ends",
                ordinal=2,
                final_procedure="ignore_decimal",
                keep_wrong=True,
            )
        ])

        first = plan_slots(state, "route_1")[0]

        self.assertEqual(first.kind, "misconception_discrimination")
        self.assertEqual(
            set(first.procedure_ids),
            {"align_by_ends", "ignore_decimal"},
        )

    def test_fragile_skill_precedes_under_sampled_core_skill(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.correct(
                ordinal=2,
                skill="place_value",
                confidence="guessing",
            ),
            event.correct(
                ordinal=3,
                skill="mental_add_sub",
                confidence="certain",
            ),
        ])

        first = plan_slots(state, "route_1")[0]

        self.assertEqual(first.kind, "fragile_skill_transfer")
        self.assertEqual(first.skill_id, "place_value")

    def test_fragile_transfer_carries_only_authoritative_prior_skill_contexts(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.correct(
                ordinal=2,
                skill="place_value",
                confidence="guessing",
                context="route_counter",
            ),
            event.correct(
                ordinal=3,
                skill="mental_add_sub",
                context="unrelated_skill_context",
            ),
            event.correct(
                ordinal=4,
                skill="place_value",
                context="survey_marker",
            ),
            event.correct(
                ordinal=5,
                world="decimara",
                skill="place_value",
                context="unrelated_world_context",
                core_subskills=("place_value",),
            ),
            event.correct(
                ordinal=6,
                skill="place_value",
                context="route_counter",
            ),
        ])

        fragile = next(
            slot
            for slot in plan_slots(state, "route_1")
            if slot.kind == "fragile_skill_transfer"
        )

        self.assertEqual(
            fragile.excluded_context_ids,
            ("route_counter", "survey_marker"),
        )
        self.assertIsInstance(fragile.excluded_context_ids, tuple)
        with self.assertRaises(FrozenInstanceError):
            fragile.excluded_context_ids = ()

    def test_prior_world_transfer_follows_current_world_core_work(self):
        state = reduce_events([
            event.activate(ordinal=1, world="valuehold"),
            event.correct(
                ordinal=2,
                world="decimara",
                skill="decimal_add_sub",
                core_subskills=("decimal_add_sub",),
                question="decimal-1",
                template="decimal-template-1",
            ),
            event.correct(
                ordinal=3,
                world="valuehold",
                skill="place_value",
                core_subskills=("place_value", "mental_add_sub"),
                question="value-1",
                template="value-template-1",
            ),
        ])

        slots = plan_slots(state, "route_1")

        self.assertEqual(slots[0].world_id, "valuehold")
        self.assertIn(slots[0].kind, {"under_sampled_core_skill", "novel_current_skill"})
        self.assertTrue(any(slot.kind == "spaced_prior_world_transfer" for slot in slots))
        self.assertTrue(all(
            slot.excluded_context_ids == ()
            for slot in slots
            if slot.kind != "fragile_skill_transfer"
        ))

    def test_previous_batch_exclusions_preserve_only_adjacent_template_and_operand(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.correct(
                ordinal=2,
                batch="previous-batch",
                question="q-old-1",
                template="t-old-1",
                operand="o-old-1",
            ),
            event.correct(
                ordinal=3,
                batch="previous-batch",
                question="q-old-2",
                template="t-old-2",
                operand="o-old-2",
            ),
        ])

        slots = plan_slots(state, "route_1")
        for slot in slots:
            self.assertEqual(
                set(slot.excluded_item_ids),
                {"item-2-q-old-1", "item-3-q-old-2"},
            )
            self.assertEqual(set(slot.excluded_question_ids), {"q-old-1", "q-old-2"})
        self.assertEqual(slots[0].excluded_template_ids, ("t-old-2",))
        self.assertEqual(slots[0].excluded_operand_signatures, ("o-old-2",))
        for slot in slots[1:]:
            self.assertEqual(slot.excluded_template_ids, ())
            self.assertEqual(slot.excluded_operand_signatures, ())

    def test_assisted_slots_follow_weakness_plan_and_exclude_all_history(self):
        state = reduce_events([
            event.activate(ordinal=1),
            event.wrong(
                "pv_face_value",
                ordinal=2,
                batch="old-batch-1",
                skill="place_value",
                question="question-old-001",
                template="template-old-001",
                operand="operand-old-001",
            ),
            event.correct(
                ordinal=3,
                batch="old-batch-2",
                skill="mental_add_sub",
                question="question-old-002",
                template="template-old-002",
                operand="operand-old-002",
            ),
            event.boss(ordinal=4, final_correct=5),
            event.seal_trial(ordinal=5, passed=False, attempt=1),
            event.seal_trial(ordinal=6, passed=False, attempt=2),
        ])
        route_plan = evaluate_world_clear(
            state,
            "valuehold",
        ).assisted_route_plan
        self.assertIsNotNone(route_plan)

        slots = plan_assisted_slots(state, route_plan)

        self.assertEqual(
            tuple(slot.kind for slot in slots),
            (
                "assisted_worked_example",
                "assisted_supported_mcq",
                "assisted_supported_mcq",
            ),
        )
        self.assertEqual(
            tuple(slot.skill_id for slot in slots),
            ("place_value", "place_value", "mental_add_sub"),
        )
        for slot in slots:
            self.assertEqual(
                set(slot.excluded_question_ids),
                {"question-old-001", "question-old-002"},
            )
            self.assertEqual(
                set(slot.excluded_operand_signatures),
                {"operand-old-001", "operand-old-002"},
            )
            self.assertEqual(
                set(slot.excluded_item_ids),
                {"item-2-question-old-001", "item-3-question-old-002"},
            )

    def test_unknown_battle_tier_is_rejected(self):
        state = reduce_events([event.correct()])

        with self.assertRaises(ValueError):
            plan_slots(state, "punish_weakness")


if __name__ == "__main__":
    unittest.main()
